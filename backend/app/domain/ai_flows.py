"""The three conversation stages and the four auxiliary tasks.

These are fixed, pre-defined flows — there is no autonomous tool loop, no web browsing and
no agent framework. Each flow reads its input in one session, releases it, calls the model
with no database lock held, then applies the result in a fresh session after checking that
the input version has not moved on in the meantime.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.clock import now
from app.db import session_scope
from app.domain import audit
from app.domain import evaluation as evaluation_service
from app.domain import requests as request_service
from app.domain.ai_service import AiOutcome, AiService
from app.domain.disputes import build_snapshot, validate_decision
from app.domain.errors import invalid_state, not_found
from app.domain.policy_service import load_policy
from app.domain.reference import build_snapshot as build_reference_snapshot
from app.domain.reference import group_key
from app.models import (
    Dispute,
    DisputeDecision,
    DisputeSnapshot,
    Evaluation,
    Expense,
    ExpenseVersion,
    Offer,
    OfferVersion,
    Refund,
    Request,
    RequestVersion,
    Selection,
)
from app.models.enums import (
    AiPurpose,
    AiRunStatus,
    AiStage,
    DisputeStatus,
    EvaluationStatus,
    FairnessVerdict,
    Party,
    RefundReason,
)
from app.schemas.ai import (
    ClarifyQuestions,
    DisputeDecisionOutput,
    EvaluationResult,
    ExpenseExtraction,
    PriceFairnessReview,
    RequestSummary,
)


def _version_key(*parts: object) -> str:
    return ":".join(str(part) for part in parts)


async def clarify_request(
    service: AiService, *, request_id: uuid.UUID, actor_id: uuid.UUID
) -> AiOutcome:
    """Stage one, turn one: every necessary question asked at once."""
    async with session_scope() as session:
        request = await request_service.get_request(session, request_id)
        version = await request_service.current_version(session, request)
        context = {
            "serviceCode": version.service_code,
            "vehicleCode": version.vehicle_code,
            "city": version.city,
            "district": version.district,
            "symptoms": version.symptoms[:2000],
            "answersSoFar": version.answers,
        }
        key = _version_key("request", version.id, request.revision)

    return await service.run(
        purpose=AiPurpose.clarify_questions,
        stage=AiStage.customer,
        output_model=ClarifyQuestions,
        context=context,
        request_id=request_id,
        actor_id=actor_id,
        input_version_key=key,
        thinking=False,
    )


async def summarise_request(
    service: AiService,
    *,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    answers: dict[str, Any],
) -> AiOutcome:
    """Stage one, turn two: the summary and the remaining unknowns."""
    async with session_scope() as session:
        request = await request_service.get_request(session, request_id)
        version = await request_service.current_version(session, request)
        version.answers = {**version.answers, **answers}
        context = {
            "serviceCode": version.service_code,
            "vehicleCode": version.vehicle_code,
            "city": version.city,
            "symptoms": version.symptoms[:2000],
            "answers": version.answers,
            "allowedOfferTypes": version.allowed_offer_types,
        }
        key = _version_key("request", version.id, request.revision)
        version_id = version.id

    outcome = await service.run(
        purpose=AiPurpose.request_summary,
        stage=AiStage.customer,
        output_model=RequestSummary,
        context=context,
        request_id=request_id,
        actor_id=actor_id,
        input_version_key=key,
        thinking=False,
    )
    if not outcome.ok:
        return outcome

    summary = RequestSummary.model_validate(outcome.payload)
    async with session_scope() as session:
        request = await request_service.get_request(session, request_id)
        version = await session.get(RequestVersion, version_id)
        if version is None or request.current_version_id != version_id:
            await _mark_stale(session, outcome.run_id)
            raise invalid_state("نسخهٔ درخواست تغییر کرده است؛ پاسخ قبلی اعمال نشد.")

        version.summary_facts = summary.facts
        version.summary_unknowns = summary.unknowns
        # Missing information narrows the allowed offer type towards a diagnosis or an
        # in-person check, rather than producing a confident price.
        if summary.needs_in_person_check:
            version.allowed_offer_types = ["diagnostic"]
        elif summary.suggested_offer_type in version.allowed_offer_types:
            version.allowed_offer_types = [summary.suggested_offer_type]
        version.summary_confirmed_at = None
        request.bump()
    return outcome


async def _mark_stale(session: Any, run_id: uuid.UUID) -> None:
    from app.models import AiRun

    run = await session.get(AiRun, run_id)
    if run is not None:
        run.status = AiRunStatus.stale


async def extract_expenses(
    service: AiService,
    *,
    request_id: uuid.UUID,
    selection_id: uuid.UUID,
    actor_id: uuid.UUID,
    text: str,
) -> AiOutcome:
    """Auxiliary task: free text turned into line items, once per case by default."""
    async with session_scope() as session:
        selection = await session.get(Selection, selection_id)
        if selection is None:
            raise not_found("همکاری پیدا نشد.")
        key = _version_key("expense", selection_id, len(text))

    return await service.run(
        purpose=AiPurpose.expense_extraction,
        stage=AiStage.auxiliary,
        output_model=ExpenseExtraction,
        context={"text": text[:4000]},
        request_id=request_id,
        actor_id=actor_id,
        input_version_key=key,
        thinking=False,
    )


async def evaluate_case(service: AiService, *, evaluation_id: uuid.UUID) -> AiOutcome:
    """Auxiliary task: judge every price increase along the chain."""
    async with session_scope() as session:
        evaluation = await session.get(Evaluation, evaluation_id)
        if evaluation is None:
            raise not_found("ارزیابی پیدا نشد.")
        evaluation.status = EvaluationStatus.running
        request_id = evaluation.request_id
        context = dict(evaluation.input_payload)
        key = _version_key("evaluation", evaluation.id, evaluation.attempt)

    outcome = await service.run(
        purpose=AiPurpose.final_evaluation,
        stage=AiStage.auxiliary,
        output_model=EvaluationResult,
        context=context,
        request_id=request_id,
        input_version_key=key,
        thinking=True,
        counts_as_turn=False,
    )

    async with session_scope() as session:
        if not outcome.ok:
            await evaluation_service.mark_evaluation_unavailable(
                session,
                evaluation_id=evaluation_id,
                reason="پاسخ معتبر ارزیابی دریافت نشد؛ نتیجه در انتظار بررسی است.",
            )
            return outcome
        result = EvaluationResult.model_validate(outcome.payload)
        await evaluation_service.apply_evaluation_result(
            session,
            evaluation_id=evaluation_id,
            changes=[change.model_dump(mode="json", by_alias=True) for change in result.changes],
            model_label=f"{service.provider.name}:{service.provider.model}",
            ai_run_id=outcome.run_id,
        )
    return outcome


async def review_refund_price(service: AiService, *, refund_id: uuid.UUID) -> AiOutcome | None:
    """Operational task: the reference-based price complaint review.

    Offers are reviewed in one batch — never one model call per offer — cheapest and most
    complete first. A single documented fair offer is enough to reject the price claim;
    approving on price needs *every* valid offer reviewed and none found fair. The
    operational budget is free to the customer.
    """
    async with session_scope() as session:
        refund = await session.get(Refund, refund_id)
        if refund is None:
            raise not_found("بازپرداخت پیدا نشد.")
        request = await session.get(Request, refund.request_id)
        if request is None:
            raise not_found("پرونده پیدا نشد.")
        policy = await load_policy(session, refund.policy_version_id)
        version = await request_service.current_version(session, request)

        key = group_key(
            city=version.city,
            vehicle_code=version.vehicle_code,
            service_code=version.service_code,
            scenario_code=None,
            part_spec=None,
            part_condition="new",
            warranty_level="standard",
            buyer=Party.specialist,
        )
        reference = await build_reference_snapshot(session, key, policy)
        refund.reference_snapshot_id = reference.id

        candidates = [
            item for item in refund.offers_snapshot if item.get("wasSelectable")
        ]
        candidates.sort(
            key=lambda item: (
                item.get("totalToman") is None,
                item.get("totalToman") or 0,
            )
        )
        context = {
            "offers": [
                {
                    "offerVersionId": item["offerVersionId"],
                    "offerType": item["offerType"],
                    "totalToman": item.get("totalToman"),
                    "comparable": item.get("totalToman") is not None,
                }
                for item in candidates
            ],
            "referenceSnapshot": {
                "id": str(reference.id),
                "meanToman": reference.mean_toman,
                "caseCount": reference.case_count,
                "isReady": reference.is_ready,
            },
            "fairPriceFactor": float(policy.price.fair_price_factor),
        }
        request_id = request.id
        no_candidates = not candidates
        reference_ready = reference.is_ready

    if no_candidates:
        async with session_scope() as session:
            from app.domain import refunds as refund_service

            await refund_service.approve_by_review(
                session,
                refund_id=refund_id,
                reason=RefundReason.no_valid_offer,
                note="در زمان پایان دریافت پیشنهاد، پیشنهاد معتبر قابل انتخابی وجود نداشت.",
            )
        return None

    if not reference_ready:
        async with session_scope() as session:
            from app.domain import refunds as refund_service

            await refund_service.approve_by_review(
                session,
                refund_id=refund_id,
                reason=RefundReason.review_not_completed_in_time,
                note=(
                    "مرجع قیمتی کافی برای بررسی وجود ندارد؛ این تصمیم اثبات گران‌فروشی "
                    "نیست و امتیاز متخصص را کاهش نمی‌دهد."
                ),
            )
        return None

    outcome = await service.run(
        purpose=AiPurpose.price_fairness,
        stage=AiStage.operations,
        output_model=PriceFairnessReview,
        context=context,
        request_id=request_id,
        input_version_key=_version_key("refund", refund_id, len(candidates)),
        thinking=True,
        scope="operations",
        scope_key=f"refund:{refund_id}",
        counts_as_turn=False,
    )

    async with session_scope() as session:
        from app.domain import refunds as refund_service

        refund = await session.get(Refund, refund_id)
        if refund is None:
            return outcome
        if not outcome.ok:
            # An unresolved review that runs out of time is refunded with an explicit
            # "review not completed" reason — which is not a finding of overcharging.
            if refund.review_deadline is not None and now() >= refund.review_deadline:
                await refund_service.approve_by_review(
                    session,
                    refund_id=refund_id,
                    reason=RefundReason.review_not_completed_in_time,
                    note="بررسی در مهلت مقرر تکمیل نشد؛ این تصمیم اثبات گران‌فروشی نیست.",
                )
            else:
                await audit.record(
                    session,
                    "refund_review_needs_support",
                    request_id=refund.request_id,
                    actor_role="system",
                    subject_id=refund.id,
                    reason=outcome.error_code,
                )
            return outcome

        review = PriceFairnessReview.model_validate(outcome.payload)
        refund.findings = [
            finding.model_dump(mode="json", by_alias=True) for finding in review.findings
        ]
        reviewed = {finding.offer_version_id for finding in review.findings}
        expected = {
            item["offerVersionId"]
            for item in refund.offers_snapshot
            if item["wasSelectable"]
        }

        if any(finding.verdict is FairnessVerdict.fair for finding in review.findings):
            await refund_service.reject(
                session,
                refund_id=refund_id,
                note=(
                    "حداقل یک پیشنهاد معتبر و منصفانه برای این درخواست وجود داشت، "
                    "بنابراین بازپرداخت قیمتی برقرار نیست."
                ),
                policy=await load_policy(session, refund.policy_version_id),
            )
        elif reviewed >= expected and all(
            finding.verdict is FairnessVerdict.unfair for finding in review.findings
        ):
            await refund_service.approve_by_review(
                session,
                refund_id=refund_id,
                reason=RefundReason.price_complaint,
                note="هیچ پیشنهاد معتبر و منصفانه‌ای برای این درخواست احراز نشد.",
            )
        else:
            # Unreviewed offers may never be ignored, whatever the reason.
            await audit.record(
                session,
                "refund_review_needs_support",
                request_id=refund.request_id,
                actor_role="system",
                subject_id=refund.id,
                reason="بررسی همهٔ پیشنهادهای معتبر کامل نشد.",
            )
    return outcome


async def adjudicate_dispute(
    service: AiService, *, dispute_id: uuid.UUID, allow_evidence_round: bool = True
) -> AiOutcome | None:
    """Operational task: the binding ruling.

    Only the frozen snapshot is examined. A non-answering party is recorded and does not
    block the process, but it never proves the other side's claim either.
    """
    async with session_scope() as session:
        dispute = await session.get(Dispute, dispute_id)
        if dispute is None:
            raise not_found("اختلاف پیدا نشد.")
        if dispute.status in (
            DisputeStatus.resolved_by_ai,
            DisputeStatus.resolved_by_agreement,
        ):
            return None

        round_number = dispute.evidence_rounds_used + 1
        snapshot = await build_snapshot(session, dispute, round_number=round_number)
        dispute.status = DisputeStatus.reviewing
        dispute.bump()
        snapshot_id = snapshot.id
        request_id = dispute.request_id
        context = {
            **snapshot.payload,
            "inputSnapshotId": str(snapshot.id),
            "requestEvidenceRound": allow_evidence_round
            and dispute.evidence_rounds_used == 0,
        }
        policy = await load_policy(session, None)

    outcome = await service.run(
        purpose=AiPurpose.dispute_adjudication,
        stage=AiStage.operations,
        output_model=DisputeDecisionOutput,
        context=context,
        request_id=request_id,
        input_version_key=_version_key("dispute", dispute_id, snapshot_id),
        thinking=True,
        scope="operations",
        scope_key=f"dispute:{dispute_id}",
        counts_as_turn=False,
        max_output_tokens=policy.ai.operations_max_output_per_call,
    )

    async with session_scope() as session:
        dispute = await session.get(Dispute, dispute_id)
        if dispute is None:
            return outcome
        snapshot = await session.get(DisputeSnapshot, snapshot_id)
        if snapshot is None or snapshot.expired_at is not None:
            # The parties changed the input, or settled, while the model was thinking.
            await _mark_stale(session, outcome.run_id)
            return outcome

        if not outcome.ok:
            dispute.status = DisputeStatus.awaiting_ai
            dispute.bump()
            await audit.record(
                session,
                "dispute_awaiting_ai",
                request_id=dispute.request_id,
                actor_role="system",
                subject_id=dispute.id,
                reason=outcome.error_code,
            )
            return outcome

        output = DisputeDecisionOutput.model_validate(outcome.payload)
        try:
            validate_decision(output, snapshot, dispute)
        except ValueError as error:
            dispute.status = DisputeStatus.awaiting_ai
            dispute.bump()
            await audit.record(
                session,
                "dispute_decision_rejected",
                request_id=dispute.request_id,
                actor_role="system",
                subject_id=dispute.id,
                reason=str(error)[:400],
            )
            return outcome

        decision = DisputeDecision(
            dispute_id=dispute.id,
            snapshot_id=snapshot.id,
            ai_run_id=outcome.run_id,
            status=output.status,
            line_decisions=[
                item.model_dump(mode="json", by_alias=True) for item in output.line_decisions
            ],
            reason=output.reason,
            evidence_ids=output.evidence_ids,
            missing_fields=output.missing_fields,
        )
        session.add(decision)
        await session.flush()

        if output.status == "needs_evidence":
            dispute.status = DisputeStatus.needs_evidence
            dispute.evidence_rounds_used += 1
            dispute.evidence_deadline = now() + timedelta(
                hours=policy.timing.dispute_evidence_hours
            )
            dispute.bump()
            await audit.record(
                session,
                "dispute_needs_evidence",
                request_id=dispute.request_id,
                actor_role="system",
                subject_id=dispute.id,
                data={"missingFields": output.missing_fields},
            )
            return outcome

        decision_id = decision.id

    async with session_scope() as session:
        from app.domain import disputes as dispute_service

        await dispute_service.apply_decision(
            session, dispute_id=dispute_id, decision_id=decision_id
        )
    return outcome


async def explain_comparison(
    service: AiService, *, request_id: uuid.UUID, actor_id: uuid.UUID
) -> AiOutcome:
    """Auxiliary task: a plain-language note on the offer comparison, at most once."""
    async with session_scope() as session:
        request = await session.get(Request, request_id)
        if request is None:
            raise not_found("پرونده پیدا نشد.")
        rows = (
            await session.execute(
                select(Offer, OfferVersion)
                .join(OfferVersion, OfferVersion.id == Offer.current_version_id)
                .where(Offer.request_id == request_id)
            )
        ).all()
        context = {
            "offers": [
                {
                    "offerVersionId": str(version.id),
                    "offerType": version.offer_type.value,
                    "totalToman": version.total_toman,
                    "comparable": version.total_toman is not None,
                }
                for _, version in rows
            ],
            "referenceSnapshot": {},
            "fairPriceFactor": 1.2,
        }
        key = _version_key("comparison", request_id, request.revision)

    return await service.run(
        purpose=AiPurpose.comparison_explanation,
        stage=AiStage.auxiliary,
        output_model=PriceFairnessReview,
        context=context,
        request_id=request_id,
        actor_id=actor_id,
        input_version_key=key,
        thinking=False,
        counts_as_turn=False,
    )


async def current_expense_version(session: Any, selection_id: uuid.UUID) -> ExpenseVersion | None:
    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection_id))
    ).scalar_one_or_none()
    if expense is None or expense.current_version_id is None:
        return None
    return await session.get(ExpenseVersion, expense.current_version_id)
