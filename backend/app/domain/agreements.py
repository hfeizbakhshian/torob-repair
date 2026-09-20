"""Agreement versions, the two approvals that activate one, appointments and cancellation.

The rule this module exists to enforce: a version becomes binding only when *both* parties
explicitly approve *that exact version*. Silence is not approval, an approval of an older
version is not an approval of this one, and the model has no vote in it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit, jobs
from app.domain.errors import forbidden, invalid_state
from app.domain.money import LineItem, compute_totals, require_unique_ids
from app.models import (
    AgreementVersion,
    Approval,
    OfferVersion,
    Request,
    Selection,
)
from app.models.enums import (
    AgreementStatus,
    CloseReason,
    JobKind,
    Party,
    RequestStatus,
    SelectionStatus,
)
from app.policy import Policy
from app.repository.locking import check_revision, lock_row

DEFAULT_CANCELLATION_TERMS = (
    "پیش از شروع خدمت، هزینهٔ انصراف صفر است. پس از شروع، فقط عیب‌یابی انجام‌شده یا "
    "عملیات تکمیل‌شده‌ای که از قبل پذیرفته شده بود قابل مطالبه است؛ هر هزینهٔ دیگر باید "
    "پیش از ایجاد تعهد به تأیید هر دو طرف برسد."
)
DEFAULT_ARBITRATION_CLAUSE = (
    "اگر اختلاف با توافق حل نشود، حکم هوش مصنوعی دربارهٔ اقلام و مبلغ این پرونده "
    "لازم‌الاجراست و بدون تأیید مجدد طرفین ثبت و ابلاغ می‌شود. در این نسخه، اجرای حکم "
    "یعنی ثبت نتیجه و مبلغ تسویه؛ وصول واقعی وجه تعمیر انجام نمی‌شود."
)


async def active_agreement(
    session: AsyncSession, selection_id: uuid.UUID
) -> AgreementVersion | None:
    return (
        await session.execute(
            select(AgreementVersion).where(
                AgreementVersion.selection_id == selection_id,
                AgreementVersion.status == AgreementStatus.active,
            )
        )
    ).scalar_one_or_none()


async def current_proposal(
    session: AsyncSession, selection_id: uuid.UUID
) -> AgreementVersion | None:
    return (
        await session.execute(
            select(AgreementVersion)
            .where(
                AgreementVersion.selection_id == selection_id,
                AgreementVersion.status == AgreementStatus.proposed,
            )
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _party_for(selection: Selection, user_id: uuid.UUID, request: Request) -> Party:
    if user_id == request.customer_id:
        return Party.customer
    if user_id == selection.specialist_id:
        return Party.specialist
    raise forbidden("شما طرف این همکاری نیستید.")


async def propose_version(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    actor_id: uuid.UUID,
    lines: list[LineItem],
    scenarios: list[dict[str, object]],
    scheduled_at: datetime,
    warranty_note: str | None,
    change_reason: str | None,
    evidence_ids: list[str],
    policy: Policy,
    expected_revision: int | None = None,
) -> AgreementVersion:
    """Draft a new agreement version.

    Editing a draft always produces a *new* version: the approvals of the previous draft
    are not carried over. The previously active version and its approvals stay in force
    until this one has both approvals.
    """
    selection = await lock_row(session, Selection, selection_id)
    if selection.status is not SelectionStatus.accepted:
        raise invalid_state("برای ثبت توافق، همکاری باید پذیرفته شده باشد.")
    request = await lock_row(session, Request, selection.request_id)
    party = _party_for(selection, actor_id, request)

    require_unique_ids(lines)
    totals = compute_totals(lines)

    previous_active = await active_agreement(session, selection_id)
    pending = await current_proposal(session, selection_id)
    if pending is not None:
        check_revision(pending, expected_revision)
        pending.status = AgreementStatus.superseded
        pending.bump()

    base_offer_version_id: uuid.UUID | None = None
    expires_at = now() + timedelta(hours=policy.timing.agreement_draft_validity_hours)
    offer_version = await session.get(OfferVersion, selection.offer_version_id)
    if previous_active is None:
        # The very first agreement is anchored to the selected offer version, which is
        # where the price-change evaluation chain starts.
        base_offer_version_id = selection.offer_version_id
        if offer_version is not None:
            expires_at = min(expires_at, offer_version.valid_until)

    # A reason is required for any change to an agreement already in force, and for any
    # increase above the base — including the very first agreement, where an unexplained
    # jump from the selected offer is exactly what the evaluation has to catch.
    base_total = (
        previous_active.total_toman
        if previous_active is not None
        else (offer_version.total_toman if offer_version is not None else None)
    )
    is_increase = (
        base_total is not None
        and totals.total_toman is not None
        and totals.total_toman > base_total
    )
    if (previous_active is not None or is_increase) and not change_reason:
        raise invalid_state(
            "تغییر توافق فعال یا افزایش مبلغ نسبت به مبنا باید دلیل ثبت‌شده داشته باشد."
        )

    if scheduled_at <= now():
        raise invalid_state("زمان مراجعهٔ توافق باید در آینده باشد.")
    if request.visit_window_end and scheduled_at > request.visit_window_end:
        raise invalid_state("زمان مراجعه خارج از بازهٔ اعلام‌شدهٔ درخواست است.")

    version_number = (
        await session.scalar(
            select(AgreementVersion.version_number)
            .where(AgreementVersion.selection_id == selection_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
        or 0
    ) + 1

    agreement = AgreementVersion(
        selection_id=selection_id,
        version_number=version_number,
        supersedes_id=previous_active.id if previous_active else None,
        base_offer_version_id=base_offer_version_id,
        status=AgreementStatus.proposed,
        lines=[line.model_dump(mode="json", by_alias=True) for line in lines],
        scenarios=list(scenarios),
        total_toman=totals.total_toman,
        specialist_payable_toman=totals.specialist_payable_toman,
        scheduled_at=scheduled_at,
        warranty_note=warranty_note,
        cancellation_terms=DEFAULT_CANCELLATION_TERMS,
        arbitration_clause=DEFAULT_ARBITRATION_CLAUSE,
        change_reason=change_reason,
        change_diff=_diff(previous_active, totals.total_toman),
        evidence_ids=list(evidence_ids),
        proposed_by=party,
        expires_at=expires_at,
    )
    session.add(agreement)
    await session.flush()

    await jobs.schedule(
        session,
        JobKind.expire_agreement,
        expires_at,
        subject_id=agreement.id,
        dedupe_key=f"expire_agreement:{agreement.id}",
    )
    await audit.record(
        session,
        "agreement_proposed",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=party.value,
        subject_id=agreement.id,
        reason=change_reason,
        data={
            "versionNumber": version_number,
            "totalToman": totals.total_toman,
            "specialistPayableToman": totals.specialist_payable_toman,
        },
    )
    return agreement


def _diff(previous: AgreementVersion | None, new_total: int | None) -> dict[str, object]:
    if previous is None:
        return {"kind": "initial"}
    return {
        "kind": "change",
        "previousTotalToman": previous.total_toman,
        "newTotalToman": new_total,
        "delta": (
            None
            if previous.total_toman is None or new_total is None
            else new_total - previous.total_toman
        ),
    }


async def approve_version(
    session: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int | None,
) -> AgreementVersion:
    """Record one party's approval; the second one activates the version.

    A unique constraint on (version, party) makes a double approval a no-op rather than a
    second vote, and two simultaneous approvals cannot produce two active agreements.
    """
    agreement = await lock_row(session, AgreementVersion, agreement_id)
    check_revision(agreement, expected_revision)
    if agreement.status is not AgreementStatus.proposed:
        raise invalid_state("این نسخه دیگر در انتظار تأیید نیست.")
    if agreement.expires_at <= now():
        raise invalid_state("اعتبار این پیش‌نویس به پایان رسیده است.")

    selection = await lock_row(session, Selection, agreement.selection_id)
    request = await lock_row(session, Request, selection.request_id)
    party = _party_for(selection, actor_id, request)

    existing = (
        await session.execute(
            select(Approval).where(
                Approval.agreement_version_id == agreement.id, Approval.party == party
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Approval(
                agreement_version_id=agreement.id,
                party=party,
                user_id=actor_id,
                approved_at=now(),
            )
        )
        await session.flush()

    approvals = list(
        (
            await session.execute(
                select(Approval).where(Approval.agreement_version_id == agreement.id)
            )
        ).scalars()
    )
    parties = {approval.party for approval in approvals}
    if parties == {Party.customer, Party.specialist}:
        previous = await active_agreement(session, selection.id)
        if previous is not None and previous.id != agreement.id:
            previous.status = AgreementStatus.superseded
            previous.ended_at = now()
            previous.bump()
        agreement.status = AgreementStatus.active
        agreement.activated_at = now()
        selection.scheduled_at = agreement.scheduled_at
        selection.bump()
        await audit.record(
            session,
            "agreement_activated",
            request_id=request.id,
            actor_id=actor_id,
            actor_role=party.value,
            subject_id=agreement.id,
            data={
                "versionNumber": agreement.version_number,
                "totalToman": agreement.total_toman,
            },
        )
    else:
        await audit.record(
            session,
            "agreement_approved_by_party",
            request_id=request.id,
            actor_id=actor_id,
            actor_role=party.value,
            subject_id=agreement.id,
        )

    agreement.bump()
    return agreement


async def reject_version(
    session: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int | None,
    reason: str | None,
) -> AgreementVersion:
    """Refuse a draft. Any already-active version keeps its force."""
    agreement = await lock_row(session, AgreementVersion, agreement_id)
    check_revision(agreement, expected_revision)
    if agreement.status is not AgreementStatus.proposed:
        raise invalid_state("این نسخه دیگر در انتظار تأیید نیست.")

    selection = await lock_row(session, Selection, agreement.selection_id)
    request = await lock_row(session, Request, selection.request_id)
    party = _party_for(selection, actor_id, request)

    agreement.status = AgreementStatus.rejected
    agreement.ended_at = now()
    agreement.bump()
    await audit.record(
        session,
        "agreement_rejected",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=party.value,
        subject_id=agreement.id,
        reason=reason,
    )
    return agreement


async def expire_agreement(session: AsyncSession, agreement_id: uuid.UUID) -> None:
    """Worker path. Expiry only ever touches a draft, never an active agreement."""
    agreement = await lock_row(session, AgreementVersion, agreement_id)
    if agreement.status is not AgreementStatus.proposed:
        return
    if agreement.expires_at > now():
        return
    agreement.status = AgreementStatus.expired
    agreement.ended_at = now()
    agreement.bump()
    selection = await session.get(Selection, agreement.selection_id)
    await audit.record(
        session,
        "agreement_expired",
        request_id=selection.request_id if selection else None,
        actor_role="system",
        subject_id=agreement.id,
    )


async def start_work(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int | None,
) -> Selection:
    """Begin the service.

    Requires an accepted selection, an active agreement, and that the appointment now in
    force has actually arrived. Starting later within the same appointment is fine.
    """
    selection = await lock_row(session, Selection, selection_id)
    if selection.specialist_id != actor_id:
        raise forbidden("شروع خدمت را متخصص منتخب ثبت می‌کند.")
    check_revision(selection, expected_revision)
    if selection.status is not SelectionStatus.accepted:
        raise invalid_state("همکاری پذیرفته‌شده‌ای برای شروع وجود ندارد.")
    if selection.work_started_at is not None:
        return selection

    agreement = await active_agreement(session, selection_id)
    if agreement is None:
        raise invalid_state("پیش از شروع کار، توافق باید به تأیید هر دو طرف برسد.")
    if selection.scheduled_at > now():
        raise invalid_state("نوبت تأییدشده هنوز فرا نرسیده است.")

    selection.work_started_at = now()
    selection.bump()
    request = await lock_row(session, Request, selection.request_id)
    request.status = RequestStatus.in_progress
    request.bump()

    await audit.record(
        session,
        "work_started",
        request_id=request.id,
        actor_id=actor_id,
        actor_role="specialist",
        subject_id=selection.id,
    )
    return selection


async def cancel_collaboration(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int | None,
    reason: str,
    claimed_amount_toman: int | None = None,
) -> Selection:
    """Either side may walk away; the other side's consent is not required.

    A claim about work already done is *recorded*, not owed: the final number comes from a
    settlement agreement or from an adjudication, never from the claim itself.
    """
    selection = await lock_row(session, Selection, selection_id)
    check_revision(selection, expected_revision)
    request = await lock_row(session, Request, selection.request_id)
    party = _party_for(selection, actor_id, request)
    if selection.status is not SelectionStatus.accepted:
        raise invalid_state("همکاری فعالی برای انصراف وجود ندارد.")

    selection.status = SelectionStatus.ended
    selection.ended_at = now()
    selection.bump()

    agreement = await active_agreement(session, selection_id)
    if agreement is not None:
        agreement.status = AgreementStatus.ended
        agreement.ended_at = now()
        agreement.bump()

    has_claim = claimed_amount_toman is not None and claimed_amount_toman > 0
    if has_claim:
        request.status = RequestStatus.dispute_open
    else:
        request.status = RequestStatus.cancelled
        request.close_reason = (
            CloseReason.customer_cancelled
            if party is Party.customer
            else CloseReason.specialist_cancelled
        )
        request.closed_at = now()
    request.bump()

    # A cancellation never creates a positive sample, but an already-established
    # negative cause on this case still has to be aggregated.
    from app.domain.evaluation import queue_for_closed_case

    await queue_for_closed_case(session, request=request, selection=selection)

    await audit.record(
        session,
        "collaboration_cancelled",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=party.value,
        subject_id=selection.id,
        reason=reason,
        data={
            "claimedAmountToman": claimed_amount_toman,
            "workStarted": selection.work_started_at is not None,
        },
    )
    return selection
