"""The support desk: queues, price-source verification, appeals and the metrics board."""

from __future__ import annotations

import statistics
import uuid

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import PolicyDep, SessionDep, SupportDep
from app.api.serializers import dispute_out, evaluation_out, price_snapshot_out, refund_out
from app.domain import disputes as dispute_service
from app.domain import evaluation as evaluation_service
from app.domain import parts as parts_service
from app.domain.errors import not_found
from app.models import (
    AiRun,
    Appeal,
    AuditEvent,
    Completion,
    Dispute,
    Evaluation,
    PartPriceSnapshot,
    Refund,
    Request,
)
from app.models.enums import (
    DisputeStatus,
    EvaluationStatus,
    PaymentStatus,
    RefundStatus,
    RequestStatus,
)
from app.schemas.api import (
    DisputeOut,
    EvaluationOut,
    ExtendBudgetInput,
    MetricsOut,
    ResolveAppealInput,
    SupportEvidenceInput,
    SupportQueueOut,
    VerifySnapshotInput,
)

router = APIRouter(prefix="/support", tags=["support"])


@router.get("/queue", response_model=SupportQueueOut)
async def queue(session: SessionDep, _: SupportDep) -> SupportQueueOut:
    refunds = list(
        (
            await session.execute(
                select(Refund)
                .where(
                    Refund.status.in_(
                        [
                            RefundStatus.reviewing,
                            RefundStatus.needs_review,
                            RefundStatus.transfer_failed,
                        ]
                    )
                )
                .order_by(Refund.created_at)
            )
        ).scalars()
    )
    disputes = list(
        (
            await session.execute(
                select(Dispute)
                .where(
                    Dispute.status.in_(
                        [DisputeStatus.awaiting_ai, DisputeStatus.needs_evidence]
                    )
                )
                .order_by(Dispute.created_at)
            )
        ).scalars()
    )
    snapshots = list(
        (
            await session.execute(
                select(PartPriceSnapshot)
                .where(
                    PartPriceSnapshot.verified_by_support_at.is_(None),
                    PartPriceSnapshot.rejected_reason.is_(None),
                )
                .order_by(PartPriceSnapshot.created_at)
            )
        ).scalars()
    )
    appeals = list(
        (
            await session.execute(
                select(Appeal).where(Appeal.resolved_at.is_(None)).order_by(Appeal.deadline)
            )
        ).scalars()
    )

    return SupportQueueOut(
        refunds=[refund_out(item) for item in refunds],
        disputes_awaiting_ai=[await dispute_out(session, item) for item in disputes],
        price_sources_pending=[price_snapshot_out(item) for item in snapshots],
        appeals_open=[
            {
                "id": str(appeal.id),
                "evaluationId": str(appeal.evaluation_id),
                "reason": appeal.reason,
                "evidenceNote": appeal.evidence_note,
                "deadline": appeal.deadline.isoformat(),
            }
            for appeal in appeals
        ],
    )


@router.post("/price-snapshots/{snapshot_id}/verify", status_code=200)
async def verify_snapshot(
    snapshot_id: uuid.UUID,
    payload: VerifySnapshotInput,
    session: SessionDep,
    support: SupportDep,
) -> dict[str, object]:
    """One party typing a number is never enough to dock a score."""
    snapshot = await parts_service.verify_price_snapshot(
        session,
        snapshot_id=snapshot_id,
        support_id=support.user_id,
        approve=payload.approve,
        reason=payload.reason,
    )
    return {
        "id": str(snapshot.id),
        "verified": snapshot.verified_by_support_at is not None,
        "rejectedReason": snapshot.rejected_reason,
    }


@router.get("/evaluations/{request_id}", response_model=EvaluationOut | None)
async def get_evaluation(
    request_id: uuid.UUID, session: SessionDep, _: SupportDep
) -> EvaluationOut | None:
    evaluation = (
        await session.execute(
            select(Evaluation).where(
                Evaluation.request_id == request_id, Evaluation.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    return evaluation_out(evaluation) if evaluation else None


@router.post("/appeals/{appeal_id}/resolve", status_code=200)
async def resolve_appeal(
    appeal_id: uuid.UUID,
    payload: ResolveAppealInput,
    session: SessionDep,
    support: SupportDep,
) -> dict[str, object]:
    """Correcting the evidence or re-running the judgement.

    A manual change keeps the previous result on record and always names a reason and the
    acting person. Support never overrides an applied settlement.
    """
    appeal = await evaluation_service.resolve_appeal(
        session,
        appeal_id=appeal_id,
        support_id=support.user_id,
        note=payload.note,
        new_verdicts=payload.new_verdicts,
    )
    resolved_at = appeal.resolved_at
    return {
        "id": str(appeal.id),
        "resolvedAt": resolved_at.isoformat() if resolved_at else None,
    }


@router.post("/disputes/{dispute_id}/evidence", response_model=DisputeOut)
async def add_dispute_evidence(
    dispute_id: uuid.UUID,
    payload: SupportEvidenceInput,
    session: SessionDep,
    support: SupportDep,
) -> DisputeOut:
    """Attach supplementary material to a dispute that asked for it."""
    dispute = await dispute_service.add_support_evidence(
        session,
        dispute_id=dispute_id,
        support_id=support.user_id,
        note=payload.note,
        attachment_ids=[str(item) for item in payload.attachment_ids],
    )
    return await dispute_out(session, dispute)


@router.post("/disputes/{dispute_id}/extend-budget", response_model=DisputeOut)
async def extend_dispute_budget(
    dispute_id: uuid.UUID,
    payload: ExtendBudgetInput,
    session: SessionDep,
    support: SupportDep,
    policy: PolicyDep,
) -> DisputeOut:
    """One extra allowance of the dispute's own operational budget, with a reason.

    Support completes evidence and unblocks the process; it never issues or rewrites the
    ruling itself.
    """
    dispute = await dispute_service.extend_budget(
        session,
        dispute_id=dispute_id,
        support_id=support.user_id,
        reason=payload.reason,
        policy=policy,
    )
    return await dispute_out(session, dispute)


@router.get("/metrics", response_model=MetricsOut)
async def metrics(session: SessionDep, _: SupportDep, policy: PolicyDep) -> MetricsOut:
    """In-product metrics. Every ratio carries its denominator; zero says "no data"."""
    first_offer_events = list(
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.event_type == "offer_submitted")
            )
        ).scalars()
    )
    published = list(
        (
            await session.execute(
                select(Request).where(Request.published_at.is_not(None))
            )
        ).scalars()
    )
    by_request = {request.id: request for request in published}

    minutes: list[float] = []
    seen: set[uuid.UUID] = set()
    for event in sorted(first_offer_events, key=lambda item: item.created_at):
        request = by_request.get(event.request_id) if event.request_id else None
        if request is None or request.id in seen or request.published_at is None:
            continue
        seen.add(request.id)
        minutes.append((event.created_at - request.published_at).total_seconds() / 60)

    deadline_passed = [
        request
        for request in published
        if request.response_deadline is not None
        and request.offers_closed_at is not None
    ]
    no_offer = [
        request
        for request in deadline_passed
        if request.status is RequestStatus.closed_unselected
    ]

    cost_shown = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == "request_drafted")
    )
    paid = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.event_type == "payment_settled",
            AuditEvent.data["status"].astext == PaymentStatus.succeeded.value,
        )
    )

    evaluations = list(
        (
            await session.execute(
                select(Evaluation).where(
                    Evaluation.is_current.is_(True),
                    Evaluation.status == EvaluationStatus.completed,
                    Evaluation.is_assessable.is_(True),
                )
            )
        ).scalars()
    )
    negatives = [
        item
        for item in evaluations
        if item.has_unjustified_increase or item.has_invoice_overprice
    ]

    satisfaction: list[int] = [
        score
        for score in (
            await session.execute(
                select(Completion.satisfaction_score).where(
                    Completion.satisfaction_score.is_not(None)
                )
            )
        ).scalars()
        if score is not None
    ]

    closed_requests = [
        request.id
        for request in published
        if request.status
        in (
            RequestStatus.completed,
            RequestStatus.closed_settled,
            RequestStatus.closed_adjudicated,
            RequestStatus.closed_unselected,
        )
    ]
    token_totals: list[float] = []
    if closed_requests:
        token_rows = list(
            (
                await session.execute(
                    select(
                        AiRun.request_id,
                        func.sum(func.coalesce(AiRun.actual_input_tokens, 0)),
                    )
                    .where(AiRun.request_id.in_(closed_requests))
                    .group_by(AiRun.request_id)
                )
            ).all()
        )
        token_totals = [float(total or 0) for _, total in token_rows]

    disputes = list((await session.execute(select(Dispute))).scalars())
    decided_hours = [
        (dispute.resolved_at - dispute.created_at).total_seconds() / 3600
        for dispute in disputes
        if dispute.resolved_at is not None
    ]
    refunds = list((await session.execute(select(Refund))).scalars())
    transfer_hours = [
        (refund.transferred_at - refund.created_at).total_seconds() / 3600
        for refund in refunds
        if refund.transferred_at is not None
    ]

    def _median(values: list[float]) -> float | None:
        return round(statistics.median(values), 2) if values else None

    def _p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return round(ordered[index], 2)

    return MetricsOut(
        first_valid_offer_median_minutes=_median(minutes),
        first_valid_offer_count=len(minutes),
        comparable_offers_average=(
            round(len(first_offer_events) / len(published), 2) if published else None
        ),
        comparable_offers_denominator=len(published),
        no_offer_ratio=(
            round(len(no_offer) / len(deadline_passed), 3) if deadline_passed else None
        ),
        no_offer_denominator=len(deadline_passed),
        payment_after_cost_shown_ratio=(
            round(int(paid or 0) / int(cost_shown), 3) if cost_shown else None
        ),
        payment_denominator=int(cost_shown or 0),
        transparency_negative_ratio=(
            round(len(negatives) / len(evaluations), 3) if evaluations else None
        ),
        transparency_denominator=len(evaluations),
        satisfaction_average=(
            round(sum(satisfaction) / len(satisfaction), 2) if satisfaction else None
        ),
        satisfaction_responses=len(satisfaction),
        ai_tokens_average=(
            round(sum(token_totals) / len(token_totals), 1) if token_totals else None
        ),
        ai_tokens_p95=_p95(token_totals),
        ai_denominator=len(token_totals),
        dispute_count=len(disputes),
        dispute_median_hours_to_decision=_median(decided_hours),
        refund_count=len(refunds),
        refund_median_hours_to_transfer=_median(transfer_hours),
        is_sample_data=True,
    )


@router.get("/requests/{request_id}/evaluation", response_model=EvaluationOut)
async def request_evaluation(
    request_id: uuid.UUID, session: SessionDep, _: SupportDep
) -> EvaluationOut:
    evaluation = (
        await session.execute(
            select(Evaluation).where(
                Evaluation.request_id == request_id, Evaluation.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if evaluation is None:
        raise not_found("ارزیابی برای این پرونده ثبت نشده است.")
    return evaluation_out(evaluation)
