"""The scenarios from the spec that the other files do not already cover.

Reference-based refund review, the review that runs out of time, access revoked when a
specialist is replaced, and concurrent settlement attempts.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.domain import (
    agreements as agreement_service,
)
from app.domain import (
    ai_flows,
    demo_control,
)
from app.domain import (
    disputes as dispute_service,
)
from app.domain import (
    expenses as expense_service,
)
from app.domain import (
    refunds as refund_service,
)
from app.domain import (
    selection as selection_service,
)
from app.domain.ai_service import AiService
from app.domain.errors import DomainError
from app.domain.reference import build_snapshot, group_key
from app.models import Offer, Refund, Request, Settlement
from app.models.enums import (
    OfferStatus,
    Party,
    RefundReason,
    RefundStatus,
    RequestStatus,
)
from app.policy import DEFAULT_POLICY
from app.providers.ai.mock import MockAiProvider
from tests import factories


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


@pytest.fixture
def ai() -> AiService:
    return AiService(MockAiProvider(), DEFAULT_POLICY)


async def _reference_based_case(session, policy, *, offer_totals: list[int]):
    """Publish a case under the data-driven policy and collect the given offer prices."""
    await demo_control.set_demo_reference_ready(session, True)
    await session.commit()

    request = await factories.published_request(session, policy)
    assert request.refund_policy_mode.value == "reference_based"

    keys = ["specialist-arya", "specialist-behnam", "specialist-kaveh"]
    for key, total in zip(keys, offer_totals, strict=False):
        labour = 1_200_000
        await factories.submit_offer(
            session, policy, request, key,
            lines=factories.clutch_lines(part_toman=total - labour),
        )
    return request


async def test_one_fair_offer_rejects_the_price_refund(session, policy, advance, ai):
    """A single documented fair offer is enough; not choosing it changes nothing."""
    # The seeded reference mean for this group is 7,500,000.
    request = await _reference_based_case(
        session, policy, offer_totals=[8_200_000, 12_000_000, 13_500_000]
    )
    advance(hours=25)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()

    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note="گران بود", policy=policy,
    )
    await session.commit()
    assert refund.status is RefundStatus.reviewing

    await ai_flows.review_refund_price(ai, refund_id=refund.id)
    await session.close()

    decided = await session.get(Refund, refund.id)
    assert decided.status is RefundStatus.rejected
    assert "منصفانه" in (decided.decision_note or "")
    assert decided.transferred_at is None
    assert decided.transfer_attempts == 0


async def test_all_unfair_offers_approve_the_price_refund(session, policy, advance, ai):
    request = await _reference_based_case(
        session, policy, offer_totals=[12_000_000, 13_000_000, 14_000_000]
    )
    advance(hours=25)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()

    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note="همه گران بودند", policy=policy,
    )
    await session.commit()
    await ai_flows.review_refund_price(ai, refund_id=refund.id)
    await session.close()

    decided = await session.get(Refund, refund.id)
    assert decided.status is RefundStatus.approved
    assert decided.reason is RefundReason.price_complaint


async def test_sample_rows_alone_never_make_a_real_group_ready(session, policy):
    """The demo flag switches the scenario; it does not turn samples into market data."""
    key = group_key(
        city=factories.CITY,
        vehicle_code=factories.VEHICLE,
        service_code=factories.CLUTCH,
        scenario_code=None,
        part_spec=None,
        part_condition="new",
        warranty_level="standard",
        buyer=Party.specialist,
    )
    snapshot = await build_snapshot(session, key, policy)
    await session.commit()

    assert snapshot.case_count >= policy.reference.min_cases
    assert snapshot.is_sample is True
    assert snapshot.is_ready is False
    assert "نمونه" in (snapshot.not_ready_reason or "")


async def test_unfinished_review_refunds_with_its_own_reason(
    session, policy, advance, monkeypatch, ai
):
    """Running out of time is refunded, and that is explicitly not proof of overcharging."""
    request = await _reference_based_case(
        session, policy, offer_totals=[12_000_000, 13_000_000, 14_000_000]
    )
    advance(hours=25)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()

    refund = await refund_service.request_refund(
        session, request_id=request.id, customer_id=request.customer_id,
        note=None, policy=policy,
    )
    await session.commit()

    from app.domain import ai_service as ai_service_module
    from app.domain.ai_service import AiOutcome
    from app.models.enums import AiRunStatus

    async def unavailable(*args, **kwargs):
        import uuid as uuid_module

        return AiOutcome(
            run_id=uuid_module.uuid4(), status=AiRunStatus.failed,
            payload=None, error_code="timeout",
        )

    monkeypatch.setattr(ai_service_module.AiService, "run", unavailable)
    advance(hours=49)  # past the 48-hour handling deadline
    await ai_flows.review_refund_price(ai, refund_id=refund.id)
    await session.close()

    decided = await session.get(Refund, refund.id)
    assert decided.status is RefundStatus.approved
    assert decided.reason is RefundReason.review_not_completed_in_time
    assert "اثبات گران‌فروشی نیست" in (decided.decision_note or "")


async def test_replacing_a_specialist_revokes_the_previous_one(session, policy):
    """The previous collaborator keeps no access to the case that continues without them."""
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    await factories.submit_offer(session, policy, request, "specialist-behnam")
    first = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(session, policy, request, first)

    refreshed = await session.get(Request, request.id)
    await selection_service.replace_specialist(
        session, request_id=refreshed.id, customer_id=refreshed.customer_id,
        expected_revision=None, policy=policy, reason="ترجیح مشتری",
    )
    await session.commit()

    # The former specialist can no longer act on this case at all.
    with pytest.raises(DomainError):
        await expense_service.submit_expense_version(
            session, selection_id=first.id, specialist_id=first.specialist_id,
            lines=factories.clutch_lines(), source_text=None, actual_minutes=None,
            extracted_by_ai=False,
        )
    with pytest.raises(DomainError):
        await agreement_service.propose_version(
            session, selection_id=first.id, actor_id=first.specialist_id,
            lines=factories.clutch_lines(), scenarios=[],
            scheduled_at=first.scheduled_at, warranty_note=None,
            change_reason="تلاش پس از تعویض", evidence_ids=[], policy=policy,
        )

    # Their own offer is no longer selectable, and the quota is not reset.
    offer = (
        await session.execute(
            select(Offer).where(
                Offer.request_id == request.id,
                Offer.specialist_id == first.specialist_id,
            )
        )
    ).scalar_one()
    await session.refresh(offer)
    assert offer.status is OfferStatus.unselectable
    await session.refresh(refreshed)
    assert refreshed.specialist_replacements_used == 1


async def test_agreement_and_ruling_cannot_both_produce_a_settlement(
    session, policy, advance, ai
):
    """A settlement agreed while the model was thinking wins; the late ruling is refused."""
    from tests.test_disputes_and_refunds import disputed_case

    request, selection, _, dispute = await disputed_case(session, policy, advance)
    advance(hours=25)
    snapshot = await dispute_service.build_snapshot(session, dispute)
    await session.commit()

    proposal = await dispute_service.propose_resolution(
        session, dispute_id=dispute.id, actor_id=request.customer_id,
        lines=factories.clutch_lines(), note="توافق پیش از حکم",
    )
    await session.commit()
    await dispute_service.approve_resolution(
        session, proposal_id=proposal.id, actor_id=selection.specialist_id
    )
    await session.commit()

    from app.models import DisputeDecision

    late = DisputeDecision(
        dispute_id=dispute.id,
        snapshot_id=snapshot.id,
        status="decided",
        line_decisions=[
            {
                "claimItemId": "extra-oil",
                "verdict": "accepted",
                "acceptedQuantity": 1,
                "acceptedAmountToman": 900_000,
                "reason": "حکم دیررس",
                "evidenceIds": [],
            }
        ],
        reason="حکم دیررس",
        evidence_ids=[],
        missing_fields=[],
    )
    session.add(late)
    await session.commit()

    result = await dispute_service.apply_decision(
        session, dispute_id=dispute.id, decision_id=late.id
    )
    await session.commit()
    assert result is None

    settlements = list(
        (
            await session.execute(select(Settlement).where(Settlement.dispute_id == dispute.id))
        ).scalars()
    )
    assert len(settlements) == 1
    assert settlements[0].source.value == "agreement"
    closed = await session.get(Request, request.id)
    await session.refresh(closed)
    assert closed.status is RequestStatus.closed_settled


async def test_offer_validity_snapshot_ignores_withdrawn_offers(session, policy, advance):
    """An offer withdrawn before the window closed never blocks a refund."""
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")

    specialist = await factories.user(session, "specialist-arya")
    offer = (
        await session.execute(
            select(Offer).where(
                Offer.request_id == request.id, Offer.specialist_id == specialist.id
            )
        )
    ).scalar_one()
    await selection_service.offers_service.withdraw_offer(
        session, offer_id=offer.id, specialist_id=specialist.id, expected_revision=None
    )
    await session.commit()

    advance(hours=25)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()

    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    snapshot = await refund_service.snapshot_offers(session, refreshed)
    assert snapshot and all(item["wasSelectable"] is False for item in snapshot)
    assert refreshed.status is RequestStatus.closed_unselected


async def test_visit_window_is_capped_at_seven_days(session, policy):
    request = await factories.published_request(session, policy)
    assert request.visit_window_start == request.response_deadline
    assert request.visit_window_end is not None
    assert request.visit_window_end - request.visit_window_start == timedelta(
        days=policy.timing.visit_window_max_days
    )


async def test_offer_stays_selectable_up_to_its_appointment(session, policy):
    request = await factories.published_request(session, policy)
    version = await factories.submit_offer(
        session, policy, request, "specialist-arya", scheduled_in_days=1
    )
    offer = await session.get(Offer, version.offer_id)
    selectable, reason = selection_service.offers_service.is_version_selectable(
        version, offer, request, at=version.scheduled_at - timedelta(minutes=1)
    )
    assert selectable is True and reason is None

    selectable, reason = selection_service.offers_service.is_version_selectable(
        version, offer, request, at=version.scheduled_at + timedelta(minutes=1)
    )
    assert selectable is False
    assert "زمان مراجعه" in (reason or "")
