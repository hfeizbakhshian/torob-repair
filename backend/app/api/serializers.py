"""Turning domain records into API responses.

The permission rules live in the domain services; this module only decides what a given
role is *shown* — before acceptance a bidder is presented without contact details, and a
score is never rendered as a bare number without its denominator.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import offers as offers_service
from app.domain.agreements import active_agreement
from app.domain.evaluation import score_for
from app.domain.parts import MANUAL_SOURCE_LABEL
from app.models import (
    AgreementVersion,
    Approval,
    Attachment,
    Completion,
    Dispute,
    DisputeDecision,
    DisputeResolutionProposal,
    DisputeStatement,
    Evaluation,
    Expense,
    ExpenseVersion,
    Offer,
    OfferVersion,
    PartOption,
    PartPriceCheck,
    PartPriceSnapshot,
    Payment,
    ReceiptReview,
    Refund,
    Request,
    RequestVersion,
    Selection,
    Settlement,
    SpecialistProfile,
    User,
)
from app.models.enums import EvaluationStatus, PriceCheckVerdict, ReceiptStatus
from app.policy import Policy
from app.schemas.api import (
    AgreementOut,
    CompletionOut,
    DisputeOut,
    EvaluationOut,
    ExpenseVersionOut,
    OfferOut,
    OfferVersionOut,
    PartOptionOut,
    PaymentOut,
    PriceCheckOut,
    PriceSnapshotOut,
    RefundOut,
    RequestOut,
    RequestVersionOut,
    SelectionOut,
    SpecialistPublicOut,
)


def request_version_out(version: RequestVersion) -> RequestVersionOut:
    return RequestVersionOut.model_validate(version)


async def request_out(
    session: AsyncSession, request: Request, *, include_version: bool = True
) -> RequestOut:
    from app.domain.requests import has_successful_payment

    version = None
    if include_version and request.current_version_id:
        found = await session.get(RequestVersion, request.current_version_id)
        version = request_version_out(found) if found else None
    return RequestOut(
        id=request.id,
        status=request.status,
        revision=request.revision,
        close_reason=request.close_reason.value if request.close_reason else None,
        published_at=request.published_at,
        response_deadline=request.response_deadline,
        offers_closed_at=request.offers_closed_at,
        visit_window_start=request.visit_window_start,
        visit_window_end=request.visit_window_end,
        refund_policy_mode=request.refund_policy_mode,
        specialist_replacements_used=request.specialist_replacements_used,
        closed_at=request.closed_at,
        version=version,
        has_paid=await has_successful_payment(session, request.id),
    )


def payment_out(payment: Payment) -> PaymentOut:
    return PaymentOut(
        id=payment.id,
        amount_toman=payment.amount_toman,
        status=payment.status,
        receipt_label=(
            f"رسید آزمایشی — بدون انتقال وجه واقعی — {payment.amount_toman:,} تومان"
        ),
        is_sample=payment.is_sample,
        refund_policy_mode=payment.refund_policy_mode,
    )


def offer_version_out(version: OfferVersion) -> OfferVersionOut:
    return OfferVersionOut.model_validate(version)


async def specialist_public_out(
    session: AsyncSession, specialist_id: uuid.UUID, policy: Policy
) -> SpecialistPublicOut:
    user = await session.get(User, specialist_id)
    profile = (
        await session.execute(
            select(SpecialistProfile).where(SpecialistProfile.user_id == specialist_id)
        )
    ).scalar_one_or_none()
    summary = await score_for(session, specialist_id, policy)
    return SpecialistPublicOut(
        specialist_id=specialist_id,
        display_name=user.display_name if user else "متخصص نمونه",
        shop_name=profile.shop_name if profile else "—",
        district=profile.district if profile else "—",
        # Below the minimum sample size the score stays None and the UI shows the
        # "not enough history" wording instead of a number.
        score=summary.score,
        has_enough_history=summary.has_enough_history,
        assessable_cases=summary.assessable_cases,
        negative_cases=summary.negative_cases,
        unjustified_increase_cases=summary.unjustified_increase_cases,
        invoice_overprice_cases=summary.invoice_overprice_cases,
        satisfaction_average=summary.satisfaction_average,
        satisfaction_responses=summary.satisfaction_responses,
    )


async def offer_out(
    session: AsyncSession,
    offer: Offer,
    version: OfferVersion,
    request: Request,
    policy: Policy,
) -> OfferOut:
    selectable, reason = offers_service.is_version_selectable(version, offer, request)
    return OfferOut(
        id=offer.id,
        request_id=offer.request_id,
        status=offer.status,
        revision=offer.revision,
        specialist=await specialist_public_out(session, offer.specialist_id, policy),
        version=offer_version_out(version),
        selectable=selectable,
        not_selectable_reason=reason,
    )


def selection_out(selection: Selection) -> SelectionOut:
    return SelectionOut.model_validate(selection)


async def agreement_out(session: AsyncSession, agreement: AgreementVersion) -> AgreementOut:
    approvals = list(
        (
            await session.execute(
                select(Approval).where(Approval.agreement_version_id == agreement.id)
            )
        ).scalars()
    )
    return AgreementOut(
        id=agreement.id,
        version_number=agreement.version_number,
        status=agreement.status,
        revision=agreement.revision,
        lines=agreement.lines,
        scenarios=agreement.scenarios,
        total_toman=agreement.total_toman,
        specialist_payable_toman=agreement.specialist_payable_toman,
        scheduled_at=agreement.scheduled_at,
        warranty_note=agreement.warranty_note,
        cancellation_terms=agreement.cancellation_terms,
        arbitration_clause=agreement.arbitration_clause,
        change_reason=agreement.change_reason,
        change_diff=agreement.change_diff,
        evidence_ids=agreement.evidence_ids,
        proposed_by=agreement.proposed_by,
        expires_at=agreement.expires_at,
        activated_at=agreement.activated_at,
        approved_by=[approval.party for approval in approvals],
        base_offer_version_id=agreement.base_offer_version_id,
    )


async def expense_version_out(
    session: AsyncSession, version: ExpenseVersion
) -> ExpenseVersionOut:
    review = (
        await session.execute(
            select(ReceiptReview).where(ReceiptReview.expense_version_id == version.id)
        )
    ).scalar_one_or_none()
    attachments = list(
        (
            await session.execute(
                select(Attachment.id).where(
                    Attachment.expense_version_id == version.id,
                    Attachment.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    return ExpenseVersionOut(
        id=version.id,
        version_number=version.version_number,
        source_text=version.source_text,
        lines=version.lines,
        actual_minutes=version.actual_minutes,
        total_toman=version.total_toman,
        specialist_payable_toman=version.specialist_payable_toman,
        extracted_by_ai=version.extracted_by_ai,
        specialist_reviewed_at=version.specialist_reviewed_at,
        submitted_at=version.submitted_at,
        receipt_status=review.status if review else ReceiptStatus.pending,
        receipt_reason=review.reason if review else None,
        receipt_reviewed_at=review.reviewed_at if review else None,
        attachment_ids=attachments,
    )


def completion_out(completion: Completion) -> CompletionOut:
    return CompletionOut.model_validate(completion)


def part_option_out(option: PartOption) -> PartOptionOut:
    return PartOptionOut(
        id=option.id,
        part_title=option.part_title,
        part_number=option.part_number,
        brand=option.brand,
        condition=option.condition,
        warranty_note=option.warranty_note,
        product_url=option.product_url,
        seller_name=option.seller_name,
        price_toman=option.price_toman,
        delivery_note=option.delivery_note,
        delivery_cost_toman=option.delivery_cost_toman,
        observed_at=option.observed_at,
        source_label=(
            MANUAL_SOURCE_LABEL
            if option.source_kind.value == "manual"
            else "دادهٔ نمونهٔ دمو"
        ),
        compatibility_confirmed_at=option.compatibility_confirmed_at,
    )


def price_snapshot_out(snapshot: PartPriceSnapshot) -> PriceSnapshotOut:
    return PriceSnapshotOut(
        id=snapshot.id,
        seller_name=snapshot.seller_name,
        brand=snapshot.brand,
        part_number=snapshot.part_number,
        unit_price_toman=snapshot.unit_price_toman,
        observed_at=snapshot.observed_at,
        verified_by_support_at=snapshot.verified_by_support_at,
        rejected_reason=snapshot.rejected_reason,
        source_label=(
            MANUAL_SOURCE_LABEL
            if snapshot.source_kind.value == "manual"
            else "دادهٔ نمونهٔ دمو"
        ),
    )


_PRICE_CHECK_LABELS = {
    PriceCheckVerdict.overpriced: "اختلاف فاحش قیمت فاکتور با ترب",
    PriceCheckVerdict.within_range: "قیمت فاکتور در محدودهٔ قابل قبول",
    PriceCheckVerdict.not_assessable: "قابل بررسی نیست",
}


def price_check_out(check: PartPriceCheck) -> PriceCheckOut:
    label = _PRICE_CHECK_LABELS[check.verdict]
    if check.verdict is not PriceCheckVerdict.not_assessable and check.established_at is None:
        # Until the basis is settled the row is shown as pending, never as a penalty.
        label = "نیازمند بررسی"
    return PriceCheckOut(
        id=check.id,
        line_id=check.line_id,
        invoice_unit_price_toman=check.invoice_unit_price_toman,
        median_reference_toman=check.median_reference_toman,
        ratio=float(check.ratio) if check.ratio is not None else None,
        comparable=check.comparable,
        verdict=check.verdict,
        equivalence_note=check.equivalence_note,
        established=check.established_at is not None,
        snapshot_ids=check.snapshot_ids,
        display_label=label,
    )


_EVALUATION_STATES = {
    EvaluationStatus.queued: "در انتظار",
    EvaluationStatus.running: "در حال بررسی",
    EvaluationStatus.completed: "انجام‌شده",
    EvaluationStatus.needs_review: "اطلاعات ناکافی — در انتظار بررسی",
    EvaluationStatus.failed: "اطلاعات ناکافی — در انتظار بررسی",
}


def evaluation_out(evaluation: Evaluation) -> EvaluationOut:
    return EvaluationOut(
        id=evaluation.id,
        status=evaluation.status,
        attempt=evaluation.attempt,
        change_verdicts=evaluation.change_verdicts,
        has_unjustified_increase=evaluation.has_unjustified_increase,
        has_invoice_overprice=evaluation.has_invoice_overprice,
        is_assessable=evaluation.is_assessable,
        note=evaluation.note,
        model_label=evaluation.model_label,
        # An evaluation that has not run shows a waiting state rather than a made-up score.
        display_state=_EVALUATION_STATES[evaluation.status],
    )


def refund_out(refund: Refund) -> RefundOut:
    return RefundOut(
        id=refund.id,
        status=refund.status,
        amount_toman=refund.amount_toman,
        reason=refund.reason.value if refund.reason else None,
        policy_mode=refund.policy_mode,
        customer_note=refund.customer_note,
        decision_note=refund.decision_note,
        review_deadline=refund.review_deadline,
        transferred_at=refund.transferred_at,
        transfer_attempts=refund.transfer_attempts,
        findings=refund.findings,
        is_sample=True,
    )


async def dispute_out(session: AsyncSession, dispute: Dispute) -> DisputeOut:
    statements = list(
        (
            await session.execute(
                select(DisputeStatement).where(
                    DisputeStatement.dispute_id == dispute.id,
                    DisputeStatement.is_current.is_(True),
                )
            )
        ).scalars()
    )
    proposal = (
        await session.execute(
            select(DisputeResolutionProposal).where(
                DisputeResolutionProposal.dispute_id == dispute.id,
                DisputeResolutionProposal.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    decision = (
        await session.execute(
            select(DisputeDecision)
            .where(DisputeDecision.dispute_id == dispute.id)
            .order_by(DisputeDecision.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    settlement = (
        await session.execute(
            select(Settlement).where(Settlement.dispute_id == dispute.id)
        )
    ).scalar_one_or_none()

    return DisputeOut(
        id=dispute.id,
        status=dispute.status,
        revision=dispute.revision,
        claim_items=dispute.claim_items,
        statement_deadline=dispute.statement_deadline,
        evidence_deadline=dispute.evidence_deadline,
        customer_closed_statements_at=dispute.customer_closed_statements_at,
        specialist_closed_statements_at=dispute.specialist_closed_statements_at,
        evidence_rounds_used=dispute.evidence_rounds_used,
        support_evidence=dispute.support_evidence,
        resolved_at=dispute.resolved_at,
        statements=[
            {
                "party": statement.party.value,
                "body": statement.body,
                "itemPositions": statement.item_positions,
                "evidenceIds": statement.evidence_ids,
                "versionNumber": statement.version_number,
            }
            for statement in statements
        ],
        current_proposal=(
            {
                "id": str(proposal.id),
                "versionNumber": proposal.version_number,
                "proposedBy": proposal.proposed_by.value,
                "lines": proposal.lines,
                "totalToman": proposal.total_toman,
                "specialistPayableToman": proposal.specialist_payable_toman,
                "note": proposal.note,
                "customerApprovedAt": proposal.customer_approved_at.isoformat()
                if proposal.customer_approved_at
                else None,
                "specialistApprovedAt": proposal.specialist_approved_at.isoformat()
                if proposal.specialist_approved_at
                else None,
            }
            if proposal
            else None
        ),
        decision=(
            {
                "id": str(decision.id),
                "status": decision.status,
                "lineDecisions": decision.line_decisions,
                "reason": decision.reason,
                "evidenceIds": decision.evidence_ids,
                "missingFields": decision.missing_fields,
                "appliedAt": decision.applied_at.isoformat()
                if decision.applied_at
                else None,
            }
            if decision
            else None
        ),
        settlement=(
            {
                "id": str(settlement.id),
                "source": settlement.source.value,
                "lines": settlement.lines,
                "totalToman": settlement.total_toman,
                "specialistPayableToman": settlement.specialist_payable_toman,
                "appliedAt": settlement.applied_at.isoformat(),
                "note": (
                    "اجرای حکم در این نسخه یعنی ثبت و ابلاغ نتیجه و مبلغ تسویه؛ "
                    "وصول وجه تعمیر انجام نمی‌شود."
                ),
            }
            if settlement
            else None
        ),
    )


async def current_expense_version(
    session: AsyncSession, selection_id: uuid.UUID
) -> ExpenseVersion | None:
    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection_id))
    ).scalar_one_or_none()
    if expense is None or expense.current_version_id is None:
        return None
    return await session.get(ExpenseVersion, expense.current_version_id)


async def visible_agreement(
    session: AsyncSession, selection_id: uuid.UUID
) -> AgreementVersion | None:
    active = await active_agreement(session, selection_id)
    if active is not None:
        return active
    return (
        await session.execute(
            select(AgreementVersion)
            .where(AgreementVersion.selection_id == selection_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def deadline_seconds_left(deadline: datetime | None) -> float | None:
    """How long is left, never negative. `None` means there is no such deadline."""
    if deadline is None:
        return None
    return max(0.0, (deadline - now()).total_seconds())
