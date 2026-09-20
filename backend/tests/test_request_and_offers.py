"""Request publication, offer validity and relevance, against real PostgreSQL."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import now
from app.domain import offers as offer_service
from app.domain import requests as request_service
from app.domain.auth import specialist_profile
from app.domain.errors import DomainError, ErrorCode
from app.domain.money import LineItem
from app.models import Offer, OfferVersion, Payment, Request
from app.models.enums import OfferType, PaymentStatus, RequestStatus
from app.providers.payment import Outcome, get_gateway
from tests import factories


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


async def test_publication_sets_deadline_and_visit_window(session, policy):
    request = await factories.published_request(session, policy)
    assert request.status is RequestStatus.open
    assert request.response_deadline is not None
    assert request.published_at is not None
    assert request.response_deadline - request.published_at == timedelta(hours=24)
    # The visit window opens where the offer deadline closes, for at most 7 days.
    assert request.visit_window_start == request.response_deadline
    assert request.visit_window_end == request.response_deadline + timedelta(days=7)


async def test_publication_requires_confirmed_summary_and_payment(session, policy):
    customer = await factories.user(session, "customer-sahar")
    request = await request_service.create_draft(
        session,
        customer_id=customer.id,
        city=factories.CITY,
        district="تهرانسر",
        vehicle_details={},
        vehicle_code=factories.VEHICLE,
        service_code=factories.CLUTCH,
        symptoms="صدای غیرعادی",
    )
    await session.commit()

    with pytest.raises(DomainError) as unconfirmed:
        await request_service.publish(
            session,
            request_id=request.id,
            customer_id=customer.id,
            expected_revision=request.revision,
            policy=policy,
        )
    assert unconfirmed.value.code is ErrorCode.INVALID_STATE


async def test_out_of_coverage_is_refused_before_any_payment(session):
    customer = await factories.user(session, "customer-sahar")
    with pytest.raises(DomainError) as error:
        await request_service.create_draft(
            session,
            customer_id=customer.id,
            city="اصفهان",
            district="جلفا",
            vehicle_details={},
            vehicle_code=factories.VEHICLE,
            service_code=factories.CLUTCH,
            symptoms="صدای غیرعادی",
        )
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    assert "پوشش" in error.value.message


async def test_payment_result_comes_from_the_gateway_not_the_caller(session, policy):
    customer = await factories.user(session, "customer-sahar")
    request = await request_service.create_draft(
        session,
        customer_id=customer.id,
        city=factories.CITY,
        district="تهرانسر",
        vehicle_details={},
        vehicle_code=factories.VEHICLE,
        service_code=factories.CLUTCH,
        symptoms="لغزش کلاچ",
    )
    await session.commit()

    get_gateway().queue_outcome(Outcome.fail)
    payment = await request_service.pay_registration_fee(
        session,
        request_id=request.id,
        customer_id=customer.id,
        idempotency_key="k-fail",
        policy=policy,
    )
    await session.commit()
    assert payment.status is PaymentStatus.failed
    assert await request_service.has_successful_payment(session, request.id) is False


async def test_repeated_payment_never_charges_twice(session, policy):
    """A second call, even under a different key, returns the existing successful charge."""
    customer = await factories.user(session, "customer-sahar")
    request = await request_service.create_draft(
        session,
        customer_id=customer.id,
        city=factories.CITY,
        district="تهرانسر",
        vehicle_details={},
        vehicle_code=factories.VEHICLE,
        service_code=factories.CLUTCH,
        symptoms="لغزش کلاچ",
    )
    await session.commit()

    first = await request_service.pay_registration_fee(
        session, request_id=request.id, customer_id=customer.id,
        idempotency_key="key-one", policy=policy,
    )
    await session.commit()
    again = await request_service.pay_registration_fee(
        session, request_id=request.id, customer_id=customer.id,
        idempotency_key="key-two", policy=policy,
    )
    await session.commit()

    assert first.status is PaymentStatus.succeeded
    assert again.id == first.id
    successes = list(
        (
            await session.execute(
                select(Payment).where(
                    Payment.request_id == request.id,
                    Payment.status == PaymentStatus.succeeded,
                )
            )
        ).scalars()
    )
    assert len(successes) == 1


async def test_paying_again_after_publication_is_refused(session, policy):
    request = await factories.published_request(session, policy)
    with pytest.raises(DomainError) as error:
        await request_service.pay_registration_fee(
            session, request_id=request.id, customer_id=request.customer_id,
            idempotency_key="late-key", policy=policy,
        )
    assert error.value.code is ErrorCode.INVALID_STATE


async def test_only_relevant_specialists_see_the_request(session, policy):
    request = await factories.published_request(session, policy)
    version = await request_service.current_version(session, request)

    for key in ("specialist-arya", "specialist-behnam", "specialist-kaveh"):
        user = await factories.user(session, key)
        profile = await specialist_profile(session, user.id)
        assert offer_service.is_relevant(profile, version) is True

    # Wrong city, wrong vehicle and wrong specialism are each excluded.
    for key in ("specialist-rasht", "specialist-heavy", "specialist-body"):
        user = await factories.user(session, key)
        profile = await specialist_profile(session, user.id)
        assert offer_service.is_relevant(profile, version) is False


async def test_archive_specialists_never_bid_on_live_requests(session, policy):
    request = await factories.published_request(session, policy)
    version = await request_service.current_version(session, request)
    user = await factories.user(session, "archive-1")
    profile = await specialist_profile(session, user.id)
    assert offer_service.is_relevant(profile, version) is False


async def test_high_and_low_prices_are_both_accepted(session, policy):
    request = await factories.published_request(session, policy)
    cheap = await factories.submit_offer(
        session, policy, request, "specialist-arya",
        lines=factories.clutch_lines(part_toman=1_000_000),
    )
    expensive = await factories.submit_offer(
        session, policy, request, "specialist-behnam",
        lines=factories.clutch_lines(part_toman=90_000_000),
    )
    assert cheap.total_toman < expensive.total_toman
    # Price is free: neither amount is blocked, and no AI approval was involved.


async def test_offer_with_wholly_unknown_cost_is_invalid(session, policy):
    request = await factories.published_request(session, policy)
    with pytest.raises(DomainError) as error:
        await factories.submit_offer(
            session, policy, request, "specialist-arya",
            lines=[LineItem(id="x", type="extra", title="بعداً معلوم می‌شود",
                            amount_known=False)],
        )
    assert error.value.code is ErrorCode.VALIDATION_ERROR


async def test_diagnostic_offer_needs_a_known_fee_and_scope(session, policy):
    customer = await factories.user(session, "customer-omid")
    request = await request_service.create_draft(
        session,
        customer_id=customer.id,
        city=factories.CITY,
        district="تهرانسر",
        vehicle_details={},
        vehicle_code=factories.VEHICLE,
        service_code="cooling_system_diagnosis",
        symptoms="موتور در ترافیک داغ می‌کند",
    )
    await session.commit()
    version = await request_service.current_version(session, request)
    assert version.allowed_offer_types == ["diagnostic"]

    # A `fixed` offer is refused for a diagnosis-only request: a cheap diagnosis is never
    # an alternative valid offer for a full repair.
    specialist = await factories.user(session, "specialist-arya")
    profile = await specialist_profile(session, specialist.id)
    with pytest.raises(DomainError):
        await offer_service.submit_offer(
            session,
            request_id=request.id,
            specialist_id=specialist.id,
            profile=profile,
            offer_type=OfferType.fixed,
            lines=factories.clutch_lines(),
            scenarios=[],
            scheduled_at=now() + timedelta(days=2),
            valid_until=now() + timedelta(days=4),
            estimated_minutes=90,
            warranty_note=None,
            conditions_note=None,
            diagnostic_scope=None,
            diagnostic_fee_toman=None,
            diagnostic_fee_credited=False,
            policy=policy,
        )


async def test_offer_validity_must_outlast_the_deadline_by_a_day(session, policy):
    request = await factories.published_request(session, policy)
    with pytest.raises(DomainError) as error:
        await factories.submit_offer(
            session, policy, request, "specialist-arya", valid_extra_hours=2
        )
    assert "اعتبار" in error.value.message


async def test_appointment_outside_the_visit_window_is_refused(session, policy):
    request = await factories.published_request(session, policy)
    with pytest.raises(DomainError) as error:
        await factories.submit_offer(
            session, policy, request, "specialist-arya", scheduled_in_days=30,
            valid_extra_hours=24 * 40,
        )
    assert error.value.code is ErrorCode.VALIDATION_ERROR


async def test_revising_an_offer_keeps_one_live_offer_with_version_history(session, policy):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    await factories.submit_offer(
        session, policy, request, "specialist-arya",
        lines=factories.clutch_lines(part_toman=4_500_000),
    )
    specialist = await factories.user(session, "specialist-arya")
    offers = list(
        (
            await session.execute(
                select(Offer).where(
                    Offer.request_id == request.id, Offer.specialist_id == specialist.id
                )
            )
        ).scalars()
    )
    assert len(offers) == 1
    versions = list(
        (
            await session.execute(
                select(OfferVersion).where(OfferVersion.offer_id == offers[0].id)
            )
        ).scalars()
    )
    assert sorted(v.version_number for v in versions) == [1, 2]


async def test_offers_are_refused_once_the_window_closed(session, policy, advance):
    request = await factories.published_request(session, policy)
    advance(hours=25)
    with pytest.raises(DomainError) as error:
        await factories.submit_offer(session, policy, request, "specialist-arya")
    assert error.value.code is ErrorCode.INVALID_STATE


async def test_typo_note_does_not_invalidate_existing_offers(session, policy):
    request = await factories.published_request(session, policy)
    version = await factories.submit_offer(session, policy, request, "specialist-arya")
    before = version.request_version_id

    await request_service.add_typo_note(
        session, request=request, customer_id=request.customer_id, note="اصلاح غلط تایپی"
    )
    await session.commit()

    refreshed = await session.get(Request, request.id)
    # The scope version the offers were made against is unchanged.
    assert refreshed.current_version_id == before
    current = await request_service.current_version(session, refreshed)
    assert len(current.typo_notes) == 1
