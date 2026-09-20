"""Cost transparency: judging price increases, aggregating the score, and appeals.

The chain judged runs from the *selected offer version* through the first agreement and
every later change to the invoice — recording an increase as "the first agreement" is not
an exemption. A documented invoice overprice is a second, independent negative cause.

A customer agreeing to a change is permission for it, not a justification of it. Weak
evidence is `insufficient_evidence`, never an accusation. Lowering a price, quoting high
in the first place, and a registration-fee refund have no negative effect on their own,
and no fine, ban or suspension is ever built on this number.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit
from app.domain.errors import forbidden, invalid_state, validation_error
from app.models import (
    AgreementVersion,
    Appeal,
    AuditEvent,
    Completion,
    Evaluation,
    OfferVersion,
    PartPriceCheck,
    Request,
    Selection,
)
from app.models.enums import (
    ChangeVerdict,
    EvaluationStatus,
    PriceCheckVerdict,
    RequestStatus,
)
from app.policy import Policy
from app.repository.locking import lock_row

CLOSED_ASSESSABLE = {
    RequestStatus.completed,
    RequestStatus.closed_settled,
    RequestStatus.closed_adjudicated,
    RequestStatus.cancelled,
}


@dataclass(frozen=True)
class ScoreSummary:
    """What is shown next to a specialist: never a bare number without its denominator."""

    has_enough_history: bool
    score: int | None
    assessable_cases: int
    negative_cases: int
    window_days: int
    unjustified_increase_cases: int
    invoice_overprice_cases: int
    satisfaction_average: float | None
    satisfaction_responses: int


async def build_change_chain(
    session: AsyncSession, selection: Selection
) -> list[dict[str, Any]]:
    """Every priced step from the selected offer to the last agreement version."""
    offer_version = await session.get(OfferVersion, selection.offer_version_id)
    agreements = list(
        (
            await session.execute(
                select(AgreementVersion)
                .where(AgreementVersion.selection_id == selection.id)
                .order_by(AgreementVersion.version_number)
            )
        ).scalars()
    )

    chain: list[dict[str, Any]] = []
    previous_total = offer_version.total_toman if offer_version else None
    previous_label = "selected_offer"

    for agreement in agreements:
        if agreement.activated_at is None:
            continue
        delta = (
            None
            if previous_total is None or agreement.total_toman is None
            else agreement.total_toman - previous_total
        )
        chain.append(
            {
                "changeId": str(agreement.id),
                "from": previous_label,
                "to": f"agreement_v{agreement.version_number}",
                "previousTotalToman": previous_total,
                "newTotalToman": agreement.total_toman,
                "delta": delta,
                "reason": agreement.change_reason,
                "evidenceIds": list(agreement.evidence_ids),
                # The very first agreement is judged like any other step: an unexplained
                # increase from the selected offer counts, even when the customer approved.
                "isFirstAgreement": previous_label == "selected_offer",
            }
        )
        previous_total = agreement.total_toman
        previous_label = f"agreement_v{agreement.version_number}"
    return chain


async def create_evaluation(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    selection: Selection,
    policy_version_id: uuid.UUID | None,
) -> Evaluation:
    chain = await build_change_chain(session, selection)
    previous = (
        await session.execute(
            select(Evaluation).where(
                Evaluation.request_id == request_id, Evaluation.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    attempt = 1
    if previous is not None:
        previous.is_current = False
        attempt = previous.attempt + 1

    evaluation = Evaluation(
        request_id=request_id,
        specialist_id=selection.specialist_id,
        attempt=attempt,
        status=EvaluationStatus.queued,
        input_payload={"changes": chain},
        policy_version_id=policy_version_id,
    )
    session.add(evaluation)
    await session.flush()
    return evaluation


async def apply_evaluation_result(
    session: AsyncSession,
    *,
    evaluation_id: uuid.UUID,
    changes: list[dict[str, Any]],
    model_label: str,
    ai_run_id: uuid.UUID | None,
) -> Evaluation:
    """Fold a judgement into the case record and decide whether it is a negative sample."""
    evaluation = await lock_row(session, Evaluation, evaluation_id)
    evaluation.change_verdicts = changes
    evaluation.has_unjustified_increase = any(
        change.get("verdict") == ChangeVerdict.unjustified.value for change in changes
    )
    overprice_count = await session.scalar(
        select(func.count())
        .select_from(PartPriceCheck)
        .where(
            PartPriceCheck.request_id == evaluation.request_id,
            PartPriceCheck.verdict == PriceCheckVerdict.overpriced,
            PartPriceCheck.established_at.is_not(None),
            PartPriceCheck.is_superseded.is_(False),
        )
    )
    evaluation.has_invoice_overprice = bool(overprice_count)

    has_negative = evaluation.has_unjustified_increase or evaluation.has_invoice_overprice
    only_unknown = bool(changes) and all(
        change.get("verdict") == ChangeVerdict.insufficient_evidence.value
        for change in changes
    )
    # A case that is merely inconclusive leaves the denominator; an established negative
    # cause is not erased by another line being inconclusive.
    evaluation.is_assessable = has_negative or not only_unknown
    evaluation.status = EvaluationStatus.completed
    evaluation.model_label = model_label
    evaluation.ai_run_id = ai_run_id
    evaluation.bump()

    await audit.record(
        session,
        "evaluation_completed",
        request_id=evaluation.request_id,
        actor_role="system",
        subject_id=evaluation.id,
        data={
            "hasUnjustifiedIncrease": evaluation.has_unjustified_increase,
            "hasInvoiceOverprice": evaluation.has_invoice_overprice,
            "isAssessable": evaluation.is_assessable,
        },
    )
    return evaluation


async def mark_evaluation_unavailable(
    session: AsyncSession, *, evaluation_id: uuid.UUID, reason: str
) -> Evaluation:
    """No valid output: the result waits rather than inventing a score."""
    evaluation = await lock_row(session, Evaluation, evaluation_id)
    evaluation.status = EvaluationStatus.needs_review
    evaluation.note = reason
    evaluation.bump()
    return evaluation


async def score_for(
    session: AsyncSession, specialist_id: uuid.UUID, policy: Policy
) -> ScoreSummary:
    """`round(100 × (1 − U/N))` over the recent closed, assessable cases.

    Below the minimum sample size no number is shown at all — only "not enough history"
    and the count. Negative events are still recorded from the very first one.
    """
    window_start = now() - timedelta(days=policy.score.window_days)
    rows = list(
        (
            await session.execute(
                select(Evaluation, Request)
                .join(Request, Request.id == Evaluation.request_id)
                .where(
                    Evaluation.specialist_id == specialist_id,
                    Evaluation.is_current.is_(True),
                    Evaluation.excluded_by_appeal.is_(False),
                    Evaluation.status == EvaluationStatus.completed,
                    Request.status.in_(CLOSED_ASSESSABLE),
                    Request.closed_at.is_not(None),
                    Request.closed_at >= window_start,
                )
                .order_by(Request.closed_at.desc())
                .limit(policy.score.max_cases)
            )
        ).all()
    )

    assessable = [evaluation for evaluation, _ in rows if evaluation.is_assessable]
    negatives = [
        evaluation
        for evaluation in assessable
        if evaluation.has_unjustified_increase or evaluation.has_invoice_overprice
    ]

    satisfaction_rows = list(
        (
            await session.execute(
                select(Completion.satisfaction_score)
                .join(Selection, Selection.id == Completion.selection_id)
                .where(
                    Selection.specialist_id == specialist_id,
                    Completion.satisfaction_score.is_not(None),
                )
            )
        ).scalars()
    )

    total = len(assessable)
    enough = total >= policy.score.min_cases_for_number
    score = round(100 * (1 - len(negatives) / total)) if enough and total else None

    return ScoreSummary(
        has_enough_history=enough,
        score=score,
        assessable_cases=total,
        negative_cases=len(negatives),
        window_days=policy.score.window_days,
        unjustified_increase_cases=sum(
            1 for evaluation in assessable if evaluation.has_unjustified_increase
        ),
        invoice_overprice_cases=sum(
            1 for evaluation in assessable if evaluation.has_invoice_overprice
        ),
        satisfaction_average=(
            round(sum(satisfaction_rows) / len(satisfaction_rows), 2)
            if satisfaction_rows
            else None
        ),
        satisfaction_responses=len(satisfaction_rows),
    )


async def file_appeal(
    session: AsyncSession,
    *,
    evaluation_id: uuid.UUID,
    specialist_id: uuid.UUID,
    reason: str,
    evidence_note: str | None,
    policy: Policy,
) -> Appeal:
    """One objection and one supplementary document, within 7 days of notification.

    While it is open the case leaves the score aggregation and the profile shows it is
    under review. Handling it costs the customer no quota.
    """
    evaluation = await lock_row(session, Evaluation, evaluation_id)
    if evaluation.specialist_id != specialist_id:
        raise forbidden("این ارزیابی مربوط به شما نیست.")
    if evaluation.status is not EvaluationStatus.completed:
        raise invalid_state("این ارزیابی هنوز نتیجهٔ نهایی ندارد.")

    existing = (
        await session.execute(select(Appeal).where(Appeal.evaluation_id == evaluation_id))
    ).scalar_one_or_none()
    if existing is not None:
        raise invalid_state("برای این ارزیابی یک اعتراض ثبت شده است.")

    window_end = evaluation.created_at + timedelta(days=policy.timing.appeal_window_days)
    if now() > window_end:
        raise invalid_state("مهلت ۷ روزهٔ اعتراض به این نتیجه گذشته است.")
    if not reason:
        raise validation_error("دلیل اعتراض الزامی است.", reason="دلیل را بنویسید.")

    appeal = Appeal(
        evaluation_id=evaluation_id,
        specialist_id=specialist_id,
        reason=reason,
        evidence_note=evidence_note,
        deadline=now() + timedelta(hours=policy.timing.appeal_resolution_hours),
    )
    session.add(appeal)
    evaluation.excluded_by_appeal = True
    evaluation.bump()

    await audit.record(
        session,
        "evaluation_appealed",
        request_id=evaluation.request_id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=evaluation.id,
        reason=reason,
    )
    return appeal


async def resolve_appeal(
    session: AsyncSession,
    *,
    appeal_id: uuid.UUID,
    support_id: uuid.UUID,
    note: str,
    new_verdicts: list[dict[str, Any]] | None,
) -> Appeal:
    """Support corrects the evidence or re-runs the judgement.

    A manual change of the outcome needs a reason and the acting person on record, and the
    previous result is kept rather than deleted. If nothing reliable emerges by the
    deadline, `insufficient_evidence` is what gets recorded.
    """
    appeal = await lock_row(session, Appeal, appeal_id)
    evaluation = await lock_row(session, Evaluation, appeal.evaluation_id)
    if not note:
        raise validation_error("ثبت دلیل برای تغییر نتیجه الزامی است.", note="دلیل الزامی است.")

    if new_verdicts is None and now() > appeal.deadline:
        new_verdicts = [
            {**verdict, "verdict": ChangeVerdict.insufficient_evidence.value}
            for verdict in evaluation.change_verdicts
        ]

    if new_verdicts is not None:
        replacement = Evaluation(
            request_id=evaluation.request_id,
            specialist_id=evaluation.specialist_id,
            attempt=evaluation.attempt + 1,
            status=EvaluationStatus.completed,
            input_payload=evaluation.input_payload,
            change_verdicts=new_verdicts,
            has_unjustified_increase=any(
                verdict.get("verdict") == ChangeVerdict.unjustified.value
                for verdict in new_verdicts
            ),
            has_invoice_overprice=evaluation.has_invoice_overprice,
            is_assessable=evaluation.is_assessable,
            policy_version_id=evaluation.policy_version_id,
            model_label=evaluation.model_label,
            note=note,
        )
        evaluation.is_current = False
        session.add(replacement)

    appeal.resolved_at = now()
    appeal.resolution_note = note
    appeal.resolved_by = support_id
    evaluation.excluded_by_appeal = False
    evaluation.bump()

    await audit.record(
        session,
        "evaluation_appeal_resolved",
        request_id=evaluation.request_id,
        actor_id=support_id,
        actor_role="support",
        subject_id=appeal.id,
        reason=note,
        data={"replacedResult": new_verdicts is not None},
    )
    return appeal


async def negative_event_count(session: AsyncSession, request_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.request_id == request_id, AuditEvent.event_type == "invoice_overprice")
    )
    return int(count or 0)
