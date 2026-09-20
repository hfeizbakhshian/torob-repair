"""Dispute adjudication, settlement, the score, and the refund paths."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain import (
    agreements as agreement_service,
    ai_flows,
    disputes as service,
    evaluation as evaluation_service,
    expenses as expense_service,
    refunds as refund_service,
)
from app.domain.ai_service import AiService
from app.domain.errors import DomainError
from app.domain.money import LineItem
from app.models import (
    Dispute,
    DisputeDecision,
    Evaluation,
    Refund,
    Request,
    Settlement,
)
from app.models.enums import (
    DisputeStatus,
    EvaluationStatus,
    PaymentStatus,
    RefundPolicyMode,
    RefundReason,
    RefundStatus,
    RequestStatus,
    SettlementSource,
)
from app.policy import DEFAULT_POLICY
from app.providers.ai.mock import MockAiProvider
from app.providers.payment import Outcome, get_gateway
from tests import factories


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


@pytest.fixture
def ai() -> AiService:
    return AiService(MockAiProvider(), DEFAULT_POLICY)


async def disputed_case(session, policy, advance):
    """A case where the customer rejected the receipt and opened a dispute."""
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    agreement = await factories.activate_agreement(session, policy, request, selection)
    advance(days=3)
    await agreement_service.start_work(
        session, selection_id=selection.id, actor_id=selection.specialist_id,
        expected_revision=None,
    )
    await session.commit()

    extra = [
        *factories.clutch_lines(),
        LineItem(id="extra-oil", type="extra", title="روغن گیربکس اضافه",
                 amount_toman=900_000),
    ]
    version = await expense_service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=extra, source_text=None, actual_minutes=210, extracted_by_ai=False,
    )
    await session.commit()
    await expense_service.review_receipt(
        session, expense_version_id=version.id, customer_id=request.customer_id,
        approve=False, reason="این قلم در توافق نبود.",
    )
    await session.commit()

    dispute = await service.open_dispute(
        session,
        request_id=request.id,
        actor_id=request.customer_id,
        claim_items=[
            {
                "claimItemId": "extra-oil",
                "title": "روغن گیربکس اضافه",
                "type": "extra",
                "claimedQuantity": 1,
                "claimedAmountToman": 900_000,
                "reason": "خارج از دامنهٔ توافق",
                "evidenceIds": [],
                "suppliedBy": "specialist",
                "paidTo": "specialist",
            }
        ],
        policy=policy,
    )
    await session.commit()
    return request, selection, agreement, dispute


async def test_a_second_dispute_returns_the_same_process(session, policy, advance):
    request, _, _, dispute = await disputed_case(session, policy, advance)
    again = await service.open_dispute(
        session, request_id=request.id, actor_id=request.customer_id,
        claim_items=[{"claimItemId": "other", "claimedAmountToman": 1}], policy=policy,
    )
    await session.commit()
    assert again.id == dispute.id


async def test_adjudication_waits_for_the_statement_window(session, policy, advance):
    _, _, _, dispute = await disputed_case(session, policy, advance)
    assert await service.can_enter_adjudication(dispute) is False
    advance(hours=25)
    assert await service.can_enter_adjudication(dispute) is True


async def test_both_sides_declaring_finished_opens_adjudication_early(
    session, policy, advance
):
    request, selection, _, dispute = await disputed_case(session, policy, advance)
    await service.close_statements(
        session, dispute_id=dispute.id, actor_id=request.customer_id
    )
    await session.commit()
    assert await service.can_enter_adjudication(dispute) is False
    await service.close_statements(
        session, dispute_id=dispute.id, actor_id=selection.specialist_id
    )
    await session.commit()
    assert await service.can_enter_adjudication(dispute) is True


async def test_ruling_applies_without_re_confirmation_and_only_once(
    session, policy, advance, ai
):
    request, selection, _, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    await session.commit()

    await ai_flows.adjudicate_dispute(ai, dispute_id=dispute.id, allow_evidence_round=False)
    await session.close()

    refreshed = await session.get(Dispute, dispute.id)
    await session.refresh(refreshed)
    assert refreshed.status is DisputeStatus.resolved_by_ai

    settlement = (
        await session.execute(select(Settlement).where(Settlement.dispute_id == dispute.id))
    ).scalar_one()
    assert settlement.source is SettlementSource.ai_decision

    closed = await session.get(Request, request.id)
    await session.refresh(closed)
    assert closed.status is RequestStatus.closed_adjudicated

    # The customer's rejection of the receipt is untouched by the ruling.
    from app.models import Expense, ReceiptReview

    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection.id))
    ).scalar_one()
    review = (
        await session.execute(
            select(ReceiptReview).where(
                ReceiptReview.expense_version_id == expense.current_version_id
            )
        )
    ).scalar_one()
    assert review.status.value == "rejected"

    # Re-running adjudication creates no second settlement.
    await ai_flows.adjudicate_dispute(ai, dispute_id=dispute.id, allow_evidence_round=False)
    await session.close()
    settlements = list(
        (
            await session.execute(select(Settlement).where(Settlement.dispute_id == dispute.id))
        ).scalars()
    )
    assert len(settlements) == 1


async def test_settlement_amount_is_computed_by_code_not_by_the_model(
    session, policy, advance, ai
):
    request, selection, agreement, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    await session.commit()
    await ai_flows.adjudicate_dispute(ai, dispute_id=dispute.id, allow_evidence_round=False)
    await session.close()

    settlement = (
        await session.execute(select(Settlement).where(Settlement.dispute_id == dispute.id))
    ).scalar_one()
    decision = (
        await session.execute(
            select(DisputeDecision).where(DisputeDecision.dispute_id == dispute.id)
        )
    ).scalars().first()

    accepted = sum(
        int(item["acceptedAmountToman"]) for item in decision.line_decisions
    )
    undisputed = agreement.total_toman
    # Undisputed agreed items plus exactly what the ruling accepted.
    assert settlement.total_toman == undisputed + accepted


async def test_a_ruling_may_not_exceed_the_claim(session, policy, advance):
    _, _, _, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    snapshot = await service.build_snapshot(session, dispute, round_number=1)
    await session.commit()

    from app.schemas.ai import DisputeDecisionOutput

    output = DisputeDecisionOutput.model_validate(
        {
            "disputeId": str(dispute.id),
            "inputSnapshotId": str(snapshot.id),
            "status": "decided",
            "lineDecisions": [
                {
                    "claimItemId": "extra-oil",
                    "verdict": "accepted",
                    "acceptedQuantity": 1,
                    "acceptedAmountToman": 5_000_000,
                    "reason": "بیش از ادعا",
                    "evidenceIds": [],
                }
            ],
            "reason": "آزمون",
            "evidenceIds": [],
            "missingFields": [],
        }
    )
    with pytest.raises(ValueError, match="بیشتر است"):
        service.validate_decision(output, snapshot, dispute)


async def test_a_ruling_may_not_cite_evidence_outside_its_input(
    session, policy, advance
):
    _, _, _, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    snapshot = await service.build_snapshot(session, dispute, round_number=1)
    await session.commit()

    from app.schemas.ai import DisputeDecisionOutput

    output = DisputeDecisionOutput.model_validate(
        {
            "disputeId": str(dispute.id),
            "inputSnapshotId": str(snapshot.id),
            "status": "decided",
            "lineDecisions": [
                {
                    "claimItemId": "extra-oil",
                    "verdict": "accepted",
                    "acceptedQuantity": 1,
                    "acceptedAmountToman": 100,
                    "reason": "با شاهد ساختگی",
                    "evidenceIds": ["not-in-this-case"],
                }
            ],
            "reason": "آزمون",
            "evidenceIds": [],
            "missingFields": [],
        }
    )
    with pytest.raises(ValueError, match="شاهد"):
        service.validate_decision(output, snapshot, dispute)


async def test_a_ruling_must_decide_every_disputed_item(session, policy, advance):
    _, _, _, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    snapshot = await service.build_snapshot(session, dispute, round_number=1)
    await session.commit()

    from app.schemas.ai import DisputeDecisionOutput

    output = DisputeDecisionOutput.model_validate(
        {
            "disputeId": str(dispute.id),
            "inputSnapshotId": str(snapshot.id),
            "status": "decided",
            "lineDecisions": [],
            "reason": "هیچ قلمی تعیین تکلیف نشد",
            "evidenceIds": [],
            "missingFields": [],
        }
    )
    with pytest.raises(ValueError, match="همهٔ اقلام"):
        service.validate_decision(output, snapshot, dispute)


async def test_editing_a_statement_expires_the_frozen_input(session, policy, advance):
    request, _, _, dispute = await disputed_case(session, policy, advance)
    snapshot = await service.build_snapshot(session, dispute, round_number=1)
    await session.commit()
    assert snapshot.expired_at is None

    await service.submit_statement(
        session, dispute_id=dispute.id, actor_id=request.customer_id,
        body="توضیح تکمیلی", item_positions=[], evidence_ids=[],
    )
    await session.commit()
    await session.refresh(snapshot)
    assert snapshot.expired_at is not None
    await session.refresh(dispute)
    assert dispute.current_snapshot_id is None


async def test_agreement_before_the_ruling_wins_and_is_recorded_as_such(
    session, policy, advance
):
    request, selection, _, dispute = await disputed_case(session, policy, advance)
    proposal = await service.propose_resolution(
        session, dispute_id=dispute.id, actor_id=request.customer_id,
        lines=factories.clutch_lines(), note="توافق داوطلبانه",
    )
    await session.commit()

    settlement = await service.approve_resolution(
        session, proposal_id=proposal.id, actor_id=selection.specialist_id
    )
    await session.commit()
    assert settlement is not None
    assert settlement.source is SettlementSource.agreement

    await session.refresh(dispute)
    assert dispute.status is DisputeStatus.resolved_by_agreement
    closed = await session.get(Request, request.id)
    await session.refresh(closed)
    # A voluntary settlement is recorded distinctly from an adjudicated one.
    assert closed.status is RequestStatus.closed_settled


async def test_one_approval_does_not_apply_a_settlement(session, policy, advance):
    request, _, _, dispute = await disputed_case(session, policy, advance)
    proposal = await service.propose_resolution(
        session, dispute_id=dispute.id, actor_id=request.customer_id,
        lines=factories.clutch_lines(), note=None,
    )
    await session.commit()
    settlements = list((await session.execute(select(Settlement))).scalars())
    assert settlements == []
    assert proposal.specialist_approved_at is None


async def test_late_ruling_on_an_expired_snapshot_is_refused(session, policy, advance):
    request, selection, _, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    snapshot = await service.build_snapshot(session, dispute, round_number=1)
    await session.commit()

    decision = DisputeDecision(
        dispute_id=dispute.id,
        snapshot_id=snapshot.id,
        status="decided",
        line_decisions=[
            {
                "claimItemId": "extra-oil",
                "verdict": "accepted",
                "acceptedQuantity": 1,
                "acceptedAmountToman": 900_000,
                "reason": "نمونه",
                "evidenceIds": [],
            }
        ],
        reason="نمونه",
        evidence_ids=[],
        missing_fields=[],
    )
    session.add(decision)
    await session.commit()

    # The parties change the input while the model was thinking.
    await service.submit_statement(
        session, dispute_id=dispute.id, actor_id=request.customer_id,
        body="تغییر اظهارات", item_positions=[], evidence_ids=[],
    )
    await session.commit()

    with pytest.raises(DomainError, match="منقضی"):
        await service.apply_decision(
            session, dispute_id=dispute.id, decision_id=decision.id
        )


# --- refunds --------------------------------------------------------------


async def test_bootstrap_refund_is_accepted_even_with_a_fair_offer(
    session, policy, advance
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    assert request.refund_policy_mode is RefundPolicyMode.bootstrap

    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note="منصرف شدم", policy=policy,
    )
    await session.commit()
    assert refund.status is RefundStatus.approved
    assert refund.reason is RefundReason.bootstrap_policy
    # No price analysis, no waiting period, and no AI call at all.
    from app.models import AiRun

    runs = list((await session.execute(select(AiRun))).scalars())
    assert runs == []


async def test_no_valid_offer_is_refunded_automatically(session, policy, advance):
    request = await factories.published_request(session, policy)
    advance(hours=25)
    from app.domain import selection as selection_service

    await selection_service.close_offer_window(session, request.id)
    await session.commit()

    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()
    assert refund.status is RefundStatus.approved
    assert refund.reason is RefundReason.no_valid_offer


async def test_refund_before_the_deadline_does_not_use_the_no_offer_path(
    session, policy
):
    request = await factories.published_request(session, policy)
    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()
    # In bootstrap the customer still gets their money back, but not on the
    # "no valid offer" ground, which only applies once the deadline has passed.
    assert refund.reason is RefundReason.bootstrap_policy


async def test_only_one_refund_per_payment(session, policy):
    request = await factories.published_request(session, policy)
    first = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()
    again = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()
    assert again.id == first.id
    refunds = list((await session.execute(select(Refund))).scalars())
    assert len(refunds) == 1


async def test_failed_transfer_retries_with_the_same_reference(session, policy):
    request = await factories.published_request(session, policy)
    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()

    get_gateway().queue_outcome(Outcome.fail)
    await refund_service.run_transfer(session, refund_id=refund.id)
    await session.commit()
    await session.refresh(refund)
    assert refund.status is RefundStatus.transfer_failed
    reference = refund.transfer_reference

    await refund_service.run_transfer(session, refund_id=refund.id)
    await session.commit()
    await session.refresh(refund)
    assert refund.status is RefundStatus.paid
    assert refund.transfer_reference == reference
    assert refund.transfer_attempts == 2

    # Running it again does not pay twice.
    await refund_service.run_transfer(session, refund_id=refund.id)
    await session.commit()
    await session.refresh(refund)
    assert refund.transfer_attempts == 2


async def test_rejected_refund_creates_no_transfer(session, policy):
    request = await factories.published_request(session, policy)
    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()
    await refund_service.reject(
        session, refund_id=refund.id, note="نمونهٔ رد", policy=policy
    )
    await session.commit()
    await session.refresh(refund)

    await refund_service.run_transfer(session, refund_id=refund.id)
    await session.commit()
    await session.refresh(refund)
    assert refund.status is RefundStatus.rejected
    assert refund.transferred_at is None
    assert refund.transfer_attempts == 0


async def test_refund_needs_a_successful_original_payment(session, policy):
    from app.domain import requests as request_service

    customer = await factories.user(session, "customer-sahar")
    request = await request_service.create_draft(
        session, customer_id=customer.id, city=factories.CITY, district="تهرانسر",
        vehicle_details={}, vehicle_code=factories.VEHICLE,
        service_code=factories.CLUTCH, symptoms="نمونه",
    )
    await session.commit()
    get_gateway().queue_outcome(Outcome.fail)
    payment = await request_service.pay_registration_fee(
        session, request_id=request.id, customer_id=customer.id,
        idempotency_key="failing", policy=policy,
    )
    await session.commit()
    assert payment.status is PaymentStatus.failed

    with pytest.raises(DomainError):
        await refund_service.request_refund(
            session, request_id=request.id, customer_id=customer.id,
            note=None, policy=policy,
        )


# --- score ----------------------------------------------------------------


async def test_score_hides_the_number_below_the_minimum_history(session, policy):
    specialist = await factories.user(session, "specialist-arya")
    summary = await evaluation_service.score_for(session, specialist.id, policy)
    assert summary.has_enough_history is False
    assert summary.score is None
    assert summary.assessable_cases == 0


async def test_unjustified_increase_at_the_first_agreement_is_recorded(
    session, policy, advance, ai
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    # A higher first agreement over the same scope, with a reason but no evidence.
    await factories.activate_agreement(
        session, policy, request, selection,
        lines=factories.clutch_lines(part_toman=8_000_000),
        change_reason="افزایش قیمت بازار",
        evidence_ids=[],
    )
    evaluation = await evaluation_service.create_evaluation(
        session, request_id=request.id, selection=selection,
        policy_version_id=request.policy_version_id,
    )
    await session.commit()

    chain = evaluation.input_payload["changes"]
    assert chain and chain[0]["isFirstAgreement"] is True
    assert chain[0]["delta"] > 0

    await ai_flows.evaluate_case(ai, evaluation_id=evaluation.id)
    await session.close()
    refreshed = await session.get(Evaluation, evaluation.id)
    await session.refresh(refreshed)
    assert refreshed.status is EvaluationStatus.completed
    # Reason without evidence is "insufficient evidence", never an accusation.
    assert refreshed.change_verdicts[0]["verdict"] == "insufficient_evidence"


async def test_price_reduction_has_no_negative_effect(session, policy, advance, ai):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(
        session, policy, request, selection,
        lines=factories.clutch_lines(part_toman=2_000_000), change_reason=None,
        evidence_ids=[],
    )
    evaluation = await evaluation_service.create_evaluation(
        session, request_id=request.id, selection=selection,
        policy_version_id=request.policy_version_id,
    )
    await session.commit()
    await ai_flows.evaluate_case(ai, evaluation_id=evaluation.id)
    await session.close()
    refreshed = await session.get(Evaluation, evaluation.id)
    await session.refresh(refreshed)
    assert refreshed.has_unjustified_increase is False


async def test_appeal_removes_the_case_from_aggregation_until_resolved(
    session, policy, advance, ai
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(session, policy, request, selection)
    evaluation = await evaluation_service.create_evaluation(
        session, request_id=request.id, selection=selection,
        policy_version_id=request.policy_version_id,
    )
    await session.commit()
    await ai_flows.evaluate_case(ai, evaluation_id=evaluation.id)
    await session.close()

    appeal = await evaluation_service.file_appeal(
        session, evaluation_id=evaluation.id, specialist_id=selection.specialist_id,
        reason="شواهد را ارائه کرده بودم", evidence_note="پیوست نمونه", policy=policy,
    )
    await session.commit()
    refreshed = await session.get(Evaluation, evaluation.id)
    await session.refresh(refreshed)
    assert refreshed.excluded_by_appeal is True

    support = await factories.support_user(session)
    await evaluation_service.resolve_appeal(
        session, appeal_id=appeal.id, support_id=support.id,
        note="شواهد اصلاح شد", new_verdicts=None,
    )
    await session.commit()
    await session.refresh(refreshed)
    assert refreshed.excluded_by_appeal is False


async def test_only_one_appeal_per_evaluation(session, policy, advance, ai):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(session, policy, request, selection)
    evaluation = await evaluation_service.create_evaluation(
        session, request_id=request.id, selection=selection,
        policy_version_id=request.policy_version_id,
    )
    await session.commit()
    await ai_flows.evaluate_case(ai, evaluation_id=evaluation.id)
    await session.close()

    await evaluation_service.file_appeal(
        session, evaluation_id=evaluation.id, specialist_id=selection.specialist_id,
        reason="یکم", evidence_note=None, policy=policy,
    )
    await session.commit()
    with pytest.raises(DomainError):
        await evaluation_service.file_appeal(
            session, evaluation_id=evaluation.id, specialist_id=selection.specialist_id,
            reason="دوم", evidence_note=None, policy=policy,
        )
