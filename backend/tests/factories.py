"""Helpers that build a realistic case without going through the HTTP layer."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import (
    agreements as agreement_service,
    offers as offer_service,
    requests as request_service,
    selection as selection_service,
)
from app.domain.auth import specialist_profile
from app.domain.money import LineItem, labor_amount
from app.models import Request, Selection, User
from app.models.enums import OfferType, Role
from app.policy import Policy
from app.seed import CITY, VEHICLE, seed

CLUTCH = "clutch_kit_replacement"


async def prepare(session: AsyncSession) -> None:
    await seed(session)
    await session.commit()


async def user(session: AsyncSession, login_key: str) -> User:
    from sqlalchemy import select

    return (
        await session.execute(select(User).where(User.login_key == login_key))
    ).scalar_one()


def clutch_lines(*, part_toman: int = 4_200_000, minutes: int = 180,
                 rate: int = 400_000, part_supplied_by: str = "specialist") -> list[LineItem]:
    """A part line plus a labour line, priced the way the domain computes labour."""
    return [
        LineItem(
            id="part-clutch",
            type="part",
            title="کیت کلاچ کامل",
            spec="CL-206-T5",
            quantity=1,
            unit_rate_toman=part_toman,
            amount_toman=part_toman,
            supplied_by=part_supplied_by,  # type: ignore[arg-type]
            paid_to="specialist" if part_supplied_by == "specialist" else "third_party",
        ),
        LineItem(
            id="labor-clutch",
            type="labor",
            title="اجرت تعویض کلاچ",
            operation_code="clutch_replace",
            minutes=minutes,
            hourly_rate_toman=rate,
            amount_toman=labor_amount(minutes, rate),
        ),
    ]


async def published_request(
    session: AsyncSession, policy: Policy, *, customer_key: str = "customer-sahar"
) -> Request:
    """A draft taken all the way to published, through the real domain services."""
    customer = await user(session, customer_key)
    request = await request_service.create_draft(
        session,
        customer_id=customer.id,
        city=CITY,
        district="تهرانسر",
        vehicle_details={"year": 1396},
        vehicle_code=VEHICLE,
        service_code=CLUTCH,
        symptoms="هنگام گاز دادن دور موتور بالا می‌رود ولی سرعت زیاد نمی‌شود.",
    )
    await session.commit()

    await request_service.pay_registration_fee(
        session,
        request_id=request.id,
        customer_id=customer.id,
        idempotency_key=f"pay-{uuid.uuid4().hex}",
        policy=policy,
    )
    await session.commit()

    version = await request_service.current_version(session, request)
    version.summary_facts = ["خودرو: پژو ۲۰۶ تیپ ۵", "نشانه: لغزش کلاچ"]
    version.summary_unknowns = []
    await session.commit()

    await request_service.confirm_summary(
        session,
        request_id=request.id,
        customer_id=customer.id,
        expected_revision=request.revision,
    )
    await session.commit()

    await request_service.publish(
        session,
        request_id=request.id,
        customer_id=customer.id,
        expected_revision=request.revision,
        policy=policy,
    )
    await session.commit()
    return request


async def submit_offer(
    session: AsyncSession,
    policy: Policy,
    request: Request,
    specialist_key: str,
    *,
    lines: list[LineItem] | None = None,
    offer_type: OfferType = OfferType.fixed,
    scenarios: list[dict[str, Any]] | None = None,
    scheduled_in_days: float = 2,
    valid_extra_hours: int = 48,
) -> Any:
    specialist = await user(session, specialist_key)
    profile = await specialist_profile(session, specialist.id)
    deadline = request.response_deadline or now()
    version = await offer_service.submit_offer(
        session,
        request_id=request.id,
        specialist_id=specialist.id,
        profile=profile,
        offer_type=offer_type,
        lines=lines if lines is not None else clutch_lines(),
        scenarios=scenarios or [],
        scheduled_at=deadline + timedelta(days=scheduled_in_days),
        valid_until=deadline + timedelta(hours=valid_extra_hours),
        estimated_minutes=180,
        warranty_note="ضمانت نمونهٔ ۶ ماهه",
        conditions_note=None,
        diagnostic_scope=None,
        diagnostic_fee_toman=None,
        diagnostic_fee_credited=False,
        policy=policy,
    )
    await session.commit()
    return version


async def accepted_collaboration(
    session: AsyncSession, policy: Policy, request: Request, specialist_key: str
) -> Selection:
    """Select, accept, then activate a first agreement with both approvals."""
    from sqlalchemy import select as sa_select

    from app.models import Offer, OfferVersion

    specialist = await user(session, specialist_key)
    offer = (
        await session.execute(
            sa_select(Offer).where(
                Offer.request_id == request.id, Offer.specialist_id == specialist.id
            )
        )
    ).scalar_one()
    version = await session.get(OfferVersion, offer.current_version_id)
    assert version is not None

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

    await selection_service.accept_selection(
        session,
        selection_id=selection.id,
        specialist_id=specialist.id,
        expected_revision=selection.revision,
        accepted_arbitration=True,
    )
    await session.commit()
    return selection


async def activate_agreement(
    session: AsyncSession,
    policy: Policy,
    request: Request,
    selection: Selection,
    *,
    lines: list[LineItem] | None = None,
    change_reason: str | None = "ثبت توافق بر مبنای پیشنهاد منتخب (نمونه)",
    evidence_ids: list[str] | None = None,
) -> Any:
    agreement = await agreement_service.propose_version(
        session,
        selection_id=selection.id,
        actor_id=selection.specialist_id,
        lines=lines if lines is not None else clutch_lines(),
        scenarios=[],
        scheduled_at=selection.scheduled_at,
        warranty_note="ضمانت نمونهٔ ۶ ماهه",
        change_reason=change_reason,
        evidence_ids=evidence_ids or ["ev-sample"],
        policy=policy,
    )
    await session.commit()

    await agreement_service.approve_version(
        session,
        agreement_id=agreement.id,
        actor_id=selection.specialist_id,
        expected_revision=agreement.revision,
    )
    await session.commit()
    await agreement_service.approve_version(
        session,
        agreement_id=agreement.id,
        actor_id=request.customer_id,
        expected_revision=agreement.revision,
    )
    await session.commit()
    return agreement


async def support_user(session: AsyncSession) -> User:
    from sqlalchemy import select

    return (
        await session.execute(select(User).where(User.role == Role.support))
    ).scalars().first()  # type: ignore[return-value]
