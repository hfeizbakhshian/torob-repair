"""Selection, mutual acceptance, agreement versioning, appointments and concurrency."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.clock import now
from app.domain import agreements as agreement_service, selection as selection_service
from app.domain.errors import DomainError, ErrorCode
from app.models import AgreementVersion, Approval, Offer, OfferVersion, Request, Selection
from app.models.enums import (
    AgreementStatus,
    OfferStatus,
    RequestStatus,
    SelectionStatus,
)
from tests import factories


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


async def _offer_version(session, request, specialist_key) -> OfferVersion:
    specialist = await factories.user(session, specialist_key)
    offer = (
        await session.execute(
            select(Offer).where(
                Offer.request_id == request.id, Offer.specialist_id == specialist.id
            )
        )
    ).scalar_one()
    return await session.get(OfferVersion, offer.current_version_id)


async def test_acceptance_deadline_is_the_earliest_of_the_three_limits(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    version = await _offer_version(session, request, "specialist-arya")

    selection = await selection_service.select_offer(
        session,
        request_id=request.id,
        customer_id=request.customer_id,
        offer_version_id=version.id,
        expected_revision=request.revision,
        policy=policy,
        accepted_arbitration=True,
    )
    await session.commit()
    # Two hours from now is the earliest of (now+2h, valid_until, scheduled_at).
    assert selection.acceptance_deadline <= now() + timedelta(hours=2, seconds=1)


async def test_selection_requires_accepting_the_arbitration_clause(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    version = await _offer_version(session, request, "specialist-arya")

    with pytest.raises(DomainError) as error:
        await selection_service.select_offer(
            session,
            request_id=request.id,
            customer_id=request.customer_id,
            offer_version_id=version.id,
            expected_revision=request.revision,
            policy=policy,
            accepted_arbitration=False,
        )
    assert "داوری" in error.value.message


async def test_only_one_selection_survives_two_concurrent_attempts(session, policy, engine):
    """Two customers' clicks race; the partial unique index lets exactly one win."""
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    await factories.submit_offer(session, policy, request, "specialist-behnam")
    first = await _offer_version(session, request, "specialist-arya")
    second = await _offer_version(session, request, "specialist-behnam")

    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def pick(offer_version_id):
        async with maker() as s:
            try:
                await selection_service.select_offer(
                    s,
                    request_id=request.id,
                    customer_id=request.customer_id,
                    offer_version_id=offer_version_id,
                    expected_revision=None,
                    policy=policy,
                    accepted_arbitration=True,
                )
                await s.commit()
                return True
            except Exception:
                await s.rollback()
                return False

    results = await asyncio.gather(pick(first.id), pick(second.id))
    assert sum(results) == 1

    open_selections = list(
        (
            await session.execute(
                select(Selection).where(
                    Selection.request_id == request.id,
                    Selection.status.in_([SelectionStatus.pending, SelectionStatus.accepted]),
                )
            )
        ).scalars()
    )
    assert len(open_selections) == 1


async def test_rejection_reopens_comparison_under_the_original_deadline(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    await factories.submit_offer(session, policy, request, "specialist-behnam")
    version = await _offer_version(session, request, "specialist-arya")
    deadline_before = request.response_deadline

    selection = await selection_service.select_offer(
        session,
        request_id=request.id,
        customer_id=request.customer_id,
        offer_version_id=version.id,
        expected_revision=request.revision,
        policy=policy,
        accepted_arbitration=True,
    )
    await session.commit()

    specialist = await factories.user(session, "specialist-arya")
    await selection_service.reject_selection(
        session,
        selection_id=selection.id,
        specialist_id=specialist.id,
        expected_revision=selection.revision,
        cannot_perform=True,
        reason="توان انجام این کار را ندارم",
    )
    await session.commit()

    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    assert refreshed.status is RequestStatus.open
    # The 24-hour deadline is never reset or extended by a refusal.
    assert refreshed.response_deadline == deadline_before
    assert refreshed.offers_closed_at is None

    offer = await session.get(Offer, version.offer_id)
    await session.refresh(offer)
    assert offer.status is OfferStatus.unselectable


async def test_acceptance_closes_the_offer_window(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    assert refreshed.status is RequestStatus.assigned
    assert refreshed.offers_closed_at is not None
    assert selection.status is SelectionStatus.accepted

    with pytest.raises(DomainError) as error:
        await factories.submit_offer(session, policy, refreshed, "specialist-kaveh")
    assert error.value.code is ErrorCode.INVALID_STATE


async def test_expired_selection_frees_the_request_without_closing_it(
    session, policy, advance
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    version = await _offer_version(session, request, "specialist-arya")
    selection = await selection_service.select_offer(
        session,
        request_id=request.id,
        customer_id=request.customer_id,
        offer_version_id=version.id,
        expected_revision=request.revision,
        policy=policy,
        accepted_arbitration=True,
    )
    await session.commit()

    advance(hours=3)
    await selection_service.expire_selection(session, selection.id)
    await session.commit()
    await session.refresh(selection)
    assert selection.status is SelectionStatus.expired

    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    assert refreshed.status is RequestStatus.open


async def test_deadline_does_not_cancel_a_still_valid_pending_selection(
    session, policy, advance
):
    """Reaching the 24-hour mark stops new offers but leaves a live selection alone."""
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    version = await _offer_version(session, request, "specialist-arya")

    advance(hours=23)
    selection = await selection_service.select_offer(
        session,
        request_id=request.id,
        customer_id=request.customer_id,
        offer_version_id=version.id,
        expected_revision=None,
        policy=policy,
        accepted_arbitration=True,
    )
    await session.commit()

    advance(hours=1, minutes=5)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()

    await session.refresh(selection)
    assert selection.status is SelectionStatus.pending
    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    assert refreshed.status is not RequestStatus.closed_unselected


async def test_request_without_offers_stays_open_until_the_deadline(
    session, policy, advance
):
    request = await factories.published_request(session, policy)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()
    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    # Before the deadline, an empty list never closes the request.
    assert refreshed.status is RequestStatus.open

    advance(hours=25)
    await selection_service.close_offer_window(session, request.id)
    await session.commit()
    await session.refresh(refreshed)
    assert refreshed.status is RequestStatus.closed_unselected
    assert refreshed.close_reason.value == "no_valid_offer"


async def test_past_appointment_cannot_be_selected_even_if_price_is_valid(
    session, policy, advance
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(
        session, policy, request, "specialist-arya",
        scheduled_in_days=1, valid_extra_hours=24 * 10,
    )
    version = await _offer_version(session, request, "specialist-arya")

    # Move past the appointment while the offer is still financially valid.
    advance(hours=24 + 26)
    assert version.valid_until > now()

    with pytest.raises(DomainError) as error:
        await selection_service.select_offer(
            session,
            request_id=request.id,
            customer_id=request.customer_id,
            offer_version_id=version.id,
            expected_revision=None,
            policy=policy,
            accepted_arbitration=True,
        )
    assert "زمان مراجعه" in error.value.message


async def test_agreement_needs_both_approvals_of_the_same_version(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )

    agreement = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=factories.clutch_lines(),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note=None,
        change_reason=None,
        evidence_ids=[],
        policy=policy,
    )
    await session.commit()

    await agreement_service.approve_version(
        session, agreement_id=agreement.id, actor_id=selection.specialist_id,
        expected_revision=agreement.revision,
    )
    await session.commit()
    await session.refresh(agreement)
    # One approval is not enough; silence is never agreement.
    assert agreement.status is AgreementStatus.proposed

    await agreement_service.approve_version(
        session, agreement_id=agreement.id, actor_id=request.customer_id,
        expected_revision=agreement.revision,
    )
    await session.commit()
    await session.refresh(agreement)
    assert agreement.status is AgreementStatus.active
    assert agreement.base_offer_version_id == selection.offer_version_id


async def test_editing_a_draft_does_not_carry_approvals_over(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    first = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=factories.clutch_lines(),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note=None,
        change_reason=None,
        evidence_ids=[],
        policy=policy,
    )
    await session.commit()
    await agreement_service.approve_version(
        session, agreement_id=first.id, actor_id=request.customer_id,
        expected_revision=first.revision,
    )
    await session.commit()

    second = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=6_000_000),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note=None,
        change_reason="اصلاح پیش‌نویس پس از بازبینی اقلام",
        evidence_ids=["ev-draft"],
        policy=policy,
    )
    await session.commit()

    approvals = list(
        (
            await session.execute(
                select(Approval).where(Approval.agreement_version_id == second.id)
            )
        ).scalars()
    )
    assert approvals == []
    await session.refresh(first)
    assert first.status is AgreementStatus.superseded


async def test_active_agreement_survives_until_the_new_one_has_both_approvals(
    session, policy
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    active = await factories.activate_agreement(session, policy, request, selection)

    proposal = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=6_000_000),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note=None,
        change_reason="یافتهٔ تازه در بازکردن گیربکس",
        evidence_ids=["ev-1"],
        policy=policy,
    )
    await session.commit()
    await agreement_service.approve_version(
        session, agreement_id=proposal.id, actor_id=selection.specialist_id,
        expected_revision=proposal.revision,
    )
    await session.commit()

    # Only one side has approved the change, so the earlier version still governs.
    still_active = await agreement_service.active_agreement(session, selection.id)
    assert still_active is not None
    assert still_active.id == active.id


async def test_a_change_without_a_reason_is_refused(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(session, policy, request, selection)

    with pytest.raises(DomainError) as error:
        await agreement_service.propose_version(
            session,
            selection_id=selection.id,
            actor_id=selection.specialist_id,
            lines=factories.clutch_lines(part_toman=9_000_000),
            scenarios=[],
            scheduled_at=selection.scheduled_at,
            warranty_note=None,
            change_reason=None,
            evidence_ids=[],
            policy=policy,
        )
    assert "دلیل" in error.value.message


async def test_stale_expected_revision_is_a_version_conflict(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    agreement = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=factories.clutch_lines(),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note=None,
        change_reason=None,
        evidence_ids=[],
        policy=policy,
    )
    await session.commit()
    await agreement_service.approve_version(
        session, agreement_id=agreement.id, actor_id=selection.specialist_id,
        expected_revision=agreement.revision,
    )
    await session.commit()

    with pytest.raises(DomainError) as error:
        await agreement_service.approve_version(
            session, agreement_id=agreement.id, actor_id=request.customer_id,
            expected_revision=1,
        )
    assert error.value.code is ErrorCode.VERSION_CONFLICT
    assert error.value.current_revision is not None


async def test_work_cannot_start_before_two_approvals(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    with pytest.raises(DomainError) as error:
        await agreement_service.start_work(
            session, selection_id=selection.id, actor_id=selection.specialist_id,
            expected_revision=selection.revision,
        )
    assert "توافق" in error.value.message


async def test_work_cannot_start_before_the_appointment(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(session, policy, request, selection)
    with pytest.raises(DomainError) as error:
        await agreement_service.start_work(
            session, selection_id=selection.id, actor_id=selection.specialist_id,
            expected_revision=None,
        )
    assert "نوبت" in error.value.message


async def test_replacing_a_specialist_is_allowed_once_and_only_before_work(
    session, policy, advance
):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    await factories.submit_offer(session, policy, request, "specialist-behnam")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(session, policy, request, selection)

    refreshed = await session.get(Request, request.id)
    await selection_service.replace_specialist(
        session,
        request_id=refreshed.id,
        customer_id=refreshed.customer_id,
        expected_revision=None,
        policy=policy,
        reason="ترجیح مشتری",
    )
    await session.commit()
    await session.refresh(refreshed)
    assert refreshed.specialist_replacements_used == 1
    assert refreshed.status is RequestStatus.open

    await session.refresh(selection)
    assert selection.status is SelectionStatus.ended
    ended = list(
        (
            await session.execute(
                select(AgreementVersion).where(
                    AgreementVersion.selection_id == selection.id,
                    AgreementVersion.status == AgreementStatus.ended,
                )
            )
        ).scalars()
    )
    assert ended  # the previous agreement ended with the collaboration

    second = await factories.accepted_collaboration(
        session, policy, refreshed, "specialist-behnam"
    )
    await factories.activate_agreement(session, policy, refreshed, second)
    await session.refresh(refreshed)

    with pytest.raises(DomainError) as error:
        await selection_service.replace_specialist(
            session,
            request_id=refreshed.id,
            customer_id=refreshed.customer_id,
            expected_revision=None,
            policy=policy,
            reason="بار دوم",
        )
    assert "یک بار" in error.value.message


async def test_first_agreement_above_the_selected_offer_needs_a_reason(session, policy):
    """An unexplained jump between the chosen offer and the first agreement is not exempt."""
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    with pytest.raises(DomainError) as error:
        await agreement_service.propose_version(
            session,
            selection_id=selection.id,
            actor_id=selection.specialist_id,
            lines=factories.clutch_lines(part_toman=8_000_000),
            scenarios=[],
            scheduled_at=selection.scheduled_at,
            warranty_note=None,
            change_reason=None,
            evidence_ids=[],
            policy=policy,
        )
    assert "دلیل" in error.value.message


async def test_redrafting_at_or_below_the_base_needs_no_reason(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    lowered = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=3_000_000),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note=None,
        change_reason=None,
        evidence_ids=[],
        policy=policy,
    )
    await session.commit()
    assert lowered.status is AgreementStatus.proposed
