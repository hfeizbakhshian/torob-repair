"""Selecting a specialist, mutual acceptance, and replacing one before work starts."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit, jobs
from app.domain import offers as offers_service
from app.domain.errors import forbidden, invalid_state, not_found
from app.models import (
    AgreementVersion,
    Offer,
    OfferVersion,
    Request,
    Selection,
)
from app.models.enums import (
    AgreementStatus,
    CloseReason,
    JobKind,
    OfferStatus,
    RequestStatus,
    SelectionStatus,
)
from app.policy import Policy
from app.repository.locking import check_revision, lock_row


def acceptance_deadline(
    *, offer_version: OfferVersion, policy: Policy, at: datetime | None = None
) -> datetime:
    """The earliest of: selection time + 2h, the offer's validity end, the appointment."""
    moment = at or now()
    return min(
        moment + timedelta(hours=policy.timing.selection_acceptance_hours),
        offer_version.valid_until,
        offer_version.scheduled_at,
    )


async def select_offer(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    customer_id: uuid.UUID,
    offer_version_id: uuid.UUID,
    expected_revision: int | None,
    policy: Policy,
    accepted_arbitration: bool,
) -> Selection:
    """Pick exactly one specialist, inside a transaction with the request row locked.

    A partial unique index guarantees a single open selection per request, so two
    simultaneous selections cannot both win.
    """
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    check_revision(request, expected_revision)
    if request.status not in (RequestStatus.open, RequestStatus.selecting):
        raise invalid_state("در وضعیت فعلی، انتخاب متخصص ممکن نیست.")
    if not accepted_arbitration:
        raise invalid_state("پیش از پذیرش همکاری باید شرط داوری را بپذیرید.")

    version = await session.get(OfferVersion, offer_version_id)
    if version is None:
        raise not_found("این نسخهٔ پیشنهاد پیدا نشد.")
    offer = await lock_row(session, Offer, version.offer_id)
    if offer.request_id != request_id:
        raise not_found("این پیشنهاد به درخواست شما مربوط نیست.")

    selectable, reason = offers_service.is_version_selectable(version, offer, request)
    if not selectable:
        raise invalid_state(reason or "این پیشنهاد قابل انتخاب نیست.")

    existing = (
        await session.execute(
            select(Selection)
            .where(
                Selection.request_id == request_id,
                Selection.status.in_([SelectionStatus.pending, SelectionStatus.accepted]),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise invalid_state("برای این درخواست هم‌اکنون یک انتخاب باز وجود دارد.")

    deadline = acceptance_deadline(offer_version=version, policy=policy)
    if deadline <= now():
        raise invalid_state("فرصت پذیرش این پیشنهاد باقی نمانده است.")

    selection = Selection(
        request_id=request_id,
        specialist_id=offer.specialist_id,
        offer_version_id=version.id,
        acceptance_deadline=deadline,
        scheduled_at=version.scheduled_at,
        arbitration_terms_version=policy.version,
        customer_accepted_terms_at=now(),
    )
    session.add(selection)
    offer.status = OfferStatus.selected
    offer.bump()
    request.status = RequestStatus.selecting
    request.bump()
    await session.flush()

    await jobs.schedule(
        session,
        JobKind.expire_selection,
        deadline,
        subject_id=selection.id,
        dedupe_key=f"expire_selection:{selection.id}",
    )
    await audit.record(
        session,
        "selection_created",
        request_id=request_id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=selection.id,
        data={"acceptanceDeadline": deadline.isoformat()},
    )
    return selection


async def accept_selection(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    specialist_id: uuid.UUID,
    expected_revision: int | None,
    accepted_arbitration: bool,
) -> Selection:
    """The specialist's acceptance. Only after this does their working chat open.

    Acceptance alone is not permission to repair — that still needs an agreement version
    both sides approve.
    """
    selection = await lock_row(session, Selection, selection_id)
    if selection.specialist_id != specialist_id:
        raise forbidden("این انتخاب مربوط به شما نیست.")
    check_revision(selection, expected_revision)
    if selection.status is not SelectionStatus.pending:
        raise invalid_state("این انتخاب دیگر در انتظار پاسخ نیست.")
    if not accepted_arbitration:
        raise invalid_state("پیش از پذیرش همکاری باید شرط داوری را بپذیرید.")

    moment = now()
    if selection.acceptance_deadline <= moment:
        raise invalid_state("مهلت پذیرش این انتخاب گذشته است.")
    if selection.scheduled_at <= moment:
        raise invalid_state("زمان مراجعهٔ این پیشنهاد گذشته و پذیرش آن ممکن نیست.")

    request = await lock_row(session, Request, selection.request_id)
    selection.status = SelectionStatus.accepted
    selection.accepted_at = moment
    selection.specialist_accepted_terms_at = moment
    selection.bump()

    request.status = RequestStatus.assigned
    # Offers stop here, or at the original deadline — whichever came first.
    request.offers_closed_at = min(
        moment, request.response_deadline or moment
    )
    request.bump()

    await audit.record(
        session,
        "selection_accepted",
        request_id=request.id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=selection.id,
    )
    return selection


async def reject_selection(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    specialist_id: uuid.UUID,
    expected_revision: int | None,
    cannot_perform: bool = False,
    reason: str | None = None,
) -> Selection:
    """Refusal returns the request to comparison under its *original* deadline.

    The refused offer becomes unselectable. When the specialist says they cannot do the
    work at all, that is recorded on the offer version so the refund review re-weighs its
    validity; the other valid offers are untouched.
    """
    selection = await lock_row(session, Selection, selection_id)
    if selection.specialist_id != specialist_id:
        raise forbidden("این انتخاب مربوط به شما نیست.")
    check_revision(selection, expected_revision)
    if selection.status is not SelectionStatus.pending:
        raise invalid_state("این انتخاب دیگر در انتظار پاسخ نیست.")

    await _release_selection(
        session,
        selection,
        SelectionStatus.rejected,
        cannot_perform=cannot_perform,
        actor_id=specialist_id,
        actor_role="specialist",
        reason=reason,
    )
    return selection


async def expire_selection(session: AsyncSession, selection_id: uuid.UUID) -> Selection | None:
    """Worker path: the 2-hour acceptance window elapsed without an answer."""
    selection = await lock_row(session, Selection, selection_id)
    if selection.status is not SelectionStatus.pending:
        return None
    if selection.acceptance_deadline > now():
        return None
    await _release_selection(
        session, selection, SelectionStatus.expired, actor_role="system"
    )
    return selection


async def _release_selection(
    session: AsyncSession,
    selection: Selection,
    outcome: SelectionStatus,
    *,
    cannot_perform: bool = False,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    reason: str | None = None,
) -> None:
    selection.status = outcome
    selection.ended_at = now()
    selection.bump()

    offer_version = await session.get(OfferVersion, selection.offer_version_id)
    if offer_version is not None:
        if cannot_perform:
            offer_version.rejected_capability_at = now()
        offer = await lock_row(session, Offer, offer_version.offer_id)
        offer.status = OfferStatus.unselectable
        offer.bump()

    request = await lock_row(session, Request, selection.request_id)
    # Back to comparison under the original deadline; the offer window is not reopened
    # and the 24-hour deadline is never reset.
    request.status = RequestStatus.open
    request.bump()

    await audit.record(
        session,
        f"selection_{outcome.value}",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=actor_role,
        subject_id=selection.id,
        reason=reason,
        data={"cannotPerform": cannot_perform},
    )
    await jobs.cancel_pending(session, JobKind.expire_selection, selection.id)


async def close_offer_window(session: AsyncSession, request_id: uuid.UUID) -> Request:
    """Worker path at the 24-hour deadline.

    Reaching the deadline stops new offers. It never cancels a selection that is still
    valid and awaiting acceptance, and an empty list before the deadline never closes a
    request.
    """
    request = await lock_row(session, Request, request_id)
    if request.response_deadline is None or request.response_deadline > now():
        return request
    if request.offers_closed_at is None:
        request.offers_closed_at = request.response_deadline
        request.bump()

    if request.status not in (RequestStatus.open, RequestStatus.selecting):
        return request

    open_selection = (
        await session.execute(
            select(Selection).where(
                Selection.request_id == request_id,
                Selection.status.in_([SelectionStatus.pending, SelectionStatus.accepted]),
            )
        )
    ).scalar_one_or_none()
    if open_selection is not None:
        return request

    if await offers_service.count_selectable_offers(session, request) == 0:
        request.status = RequestStatus.closed_unselected
        request.close_reason = CloseReason.no_valid_offer
        request.closed_at = now()
        request.bump()
        await audit.record(
            session,
            "request_closed_unselected",
            request_id=request.id,
            actor_role="system",
            reason="no_valid_offer",
        )
    return request


async def active_selection(
    session: AsyncSession, request_id: uuid.UUID
) -> Selection | None:
    return (
        await session.execute(
            select(Selection).where(
                Selection.request_id == request_id,
                Selection.status == SelectionStatus.accepted,
            )
        )
    ).scalar_one_or_none()


async def replace_specialist(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    customer_id: uuid.UUID,
    expected_revision: int | None,
    policy: Policy,
    reason: str,
) -> Request:
    """End the current collaboration before work starts, at most once per case.

    The previous selection and agreement end, the previous specialist loses chat and
    document access immediately, and the remaining AI quota carries over — no new free
    package is created and the offer deadline is not reset.
    """
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    check_revision(request, expected_revision)
    if request.specialist_replacements_used >= policy.max_specialist_replacements:
        raise invalid_state(
            "در این نسخه، تعویض متخصص فقط یک بار و پیش از شروع کار ممکن است."
        )

    selection = await active_selection(session, request_id)
    if selection is None:
        raise invalid_state("همکاری پذیرفته‌شده‌ای برای پایان‌دادن وجود ندارد.")
    if selection.work_started_at is not None:
        raise invalid_state(
            "پس از شروع کار، تعویض متخصص با بستن پرونده و ثبت درخواست تازه انجام می‌شود."
        )

    selection.status = SelectionStatus.ended
    selection.ended_at = now()
    selection.bump()

    agreements = (
        await session.execute(
            select(AgreementVersion).where(
                AgreementVersion.selection_id == selection.id,
                AgreementVersion.status.in_(
                    [AgreementStatus.active, AgreementStatus.proposed]
                ),
            )
        )
    ).scalars()
    for agreement in agreements:
        agreement.status = AgreementStatus.ended
        agreement.ended_at = now()
        agreement.bump()

    offer_version = await session.get(OfferVersion, selection.offer_version_id)
    if offer_version is not None:
        offer = await lock_row(session, Offer, offer_version.offer_id)
        offer.status = OfferStatus.unselectable
        offer.bump()

    request.specialist_replacements_used += 1
    remaining = await offers_service.count_selectable_offers(session, request)
    if remaining > 0:
        request.status = RequestStatus.open
    else:
        request.status = RequestStatus.cancelled
        request.close_reason = CloseReason.replaced_specialist
        request.closed_at = now()
    request.bump()

    await audit.record(
        session,
        "specialist_replaced",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=selection.id,
        reason=reason,
        data={"remainingSelectableOffers": remaining},
    )
    return request
