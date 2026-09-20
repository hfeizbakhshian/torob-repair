"""Offers: who may see a request, what makes a bid valid, and how versions work.

Price is free. The rules below are structural — a missing field, a scope mismatch or an
appointment outside the visit window blocks submission, but an amount being high or low
never does. There is no AI approval step anywhere in this path.
"""

from __future__ import annotations

import uuid
from datetime import datetime as datetime_t
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit
from app.domain.errors import forbidden, invalid_state, not_found, validation_error
from app.domain.money import LineItem, compute_totals, require_unique_ids
from app.models import (
    Offer,
    OfferVersion,
    Request,
    RequestVersion,
    Selection,
    SpecialistProfile,
)
from app.models.enums import (
    OfferStatus,
    OfferType,
    RequestStatus,
    SelectionStatus,
)
from app.policy import Policy
from app.repository.locking import check_revision, lock_row

RELEVANT_STATUSES = {RequestStatus.open, RequestStatus.selecting}


def is_relevant(profile: SpecialistProfile, version: RequestVersion) -> bool:
    """Relevance is a shared service, vehicle, city and visit mode.

    District proximity is only a comparison factor shown to the customer; it never
    decides who gets to see a request.
    """
    return (
        profile.is_active
        and not profile.is_archive_only
        and profile.city == version.city
        and version.service_code in profile.service_codes
        and version.vehicle_code in profile.vehicle_codes
        and version.visit_mode in profile.visit_modes
    )


async def list_relevant_requests(
    session: AsyncSession, profile: SpecialistProfile
) -> list[tuple[Request, RequestVersion]]:
    """The specialist dashboard. Refreshed by polling; nothing is sent outside the system."""
    rows = (
        await session.execute(
            select(Request, RequestVersion)
            .join(RequestVersion, RequestVersion.id == Request.current_version_id)
            .where(Request.status.in_(RELEVANT_STATUSES))
            .order_by(Request.published_at.desc())
        )
    ).all()
    return [(req, ver) for req, ver in rows if is_relevant(profile, ver)]


def _validate_structure(
    *,
    offer_type: OfferType,
    version: RequestVersion,
    lines: list[LineItem],
    scenarios: list[dict[str, object]],
    diagnostic_fee_toman: int | None,
    diagnostic_scope: str | None,
) -> None:
    if offer_type.value not in version.allowed_offer_types:
        raise validation_error(
            "این نوع پیشنهاد برای درخواست فعلی مجاز نیست.",
            offerType="نوع پیشنهاد با نوع خدمت درخواست‌شده هم‌خوان نیست.",
        )
    require_unique_ids(lines)

    if offer_type is OfferType.diagnostic:
        if diagnostic_fee_toman is None:
            raise validation_error(
                "هزینهٔ عیب‌یابی باید معلوم باشد.",
                diagnosticFeeToman="مبلغ عیب‌یابی الزامی است.",
            )
        if not diagnostic_scope:
            raise validation_error(
                "دامنهٔ عیب‌یابی باید مشخص شود.",
                diagnosticScope="شرح دامنهٔ بررسی الزامی است.",
            )
        return

    if offer_type is OfferType.conditional:
        if not scenarios:
            raise validation_error(
                "پیشنهاد مشروط باید حداقل یک سناریو داشته باشد.",
                scenarios="سناریو الزامی است.",
            )
        priced = [
            s
            for s in scenarios
            if isinstance(s, dict)
            and s.get("code")
            and isinstance(s.get("totalToman"), int)
        ]
        if not priced:
            raise validation_error(
                "هر سناریو باید کد و مبلغ معلوم داشته باشد.",
                scenarios="حداقل یک سناریوی دارای مبلغ معلوم لازم است.",
            )
        return

    totals = compute_totals(lines)
    if totals.total_toman is None:
        raise validation_error(
            "پیشنهاد قطعی باید مبلغ کل معلوم داشته باشد.",
            lines="هزینهٔ پایهٔ کاملاً نامعلوم، پیشنهاد معتبر نیست.",
        )


def _validate_timing(
    *, request: Request, scheduled_at: datetime_t, valid_until: datetime_t, policy: Policy
) -> None:
    current = now()
    if scheduled_at <= current:
        raise validation_error(
            "زمان مراجعه باید در آینده باشد.", scheduledAt="نوبت گذشته قابل ثبت نیست."
        )
    if request.visit_window_start and scheduled_at < request.visit_window_start:
        raise validation_error(
            "زمان مراجعه باید داخل بازهٔ اعلام‌شده باشد.",
            scheduledAt="نوبت پیش از آغاز بازهٔ مراجعه است.",
        )
    if request.visit_window_end and scheduled_at > request.visit_window_end:
        raise validation_error(
            "زمان مراجعه باید داخل بازهٔ اعلام‌شده باشد.",
            scheduledAt="نوبت پس از پایان بازهٔ مراجعه است.",
        )
    if request.response_deadline is not None:
        minimum = request.response_deadline + timedelta(
            hours=policy.timing.offer_validity_min_hours_after_deadline
        )
        if valid_until < minimum:
            raise validation_error(
                "اعتبار پیشنهاد باید حداقل تا ۲۴ ساعت پس از پایان مهلت باشد.",
                validUntil="مدت اعتبار کمتر از حد لازم است.",
            )


async def submit_offer(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    specialist_id: uuid.UUID,
    profile: SpecialistProfile,
    offer_type: OfferType,
    lines: list[LineItem],
    scenarios: list[dict[str, object]],
    scheduled_at: datetime_t,
    valid_until: datetime_t,
    estimated_minutes: int | None,
    warranty_note: str | None,
    conditions_note: str | None,
    diagnostic_scope: str | None,
    diagnostic_fee_toman: int | None,
    diagnostic_fee_credited: bool,
    policy: Policy,
    expected_revision: int | None = None,
) -> OfferVersion:
    """Create or revise this specialist's single standing offer on a request."""
    request = await lock_row(session, Request, request_id)
    if request.status not in RELEVANT_STATUSES:
        raise invalid_state("این درخواست پذیرای پیشنهاد نیست.")
    if request.offers_closed_at is not None:
        raise invalid_state("مهلت دریافت پیشنهاد برای این درخواست بسته شده است.")
    if request.response_deadline is not None and request.response_deadline <= now():
        raise invalid_state("مهلت ۲۴ساعتهٔ دریافت پیشنهاد به پایان رسیده است.")

    version = await session.get(RequestVersion, request.current_version_id)
    if version is None:
        raise not_found("نسخهٔ دامنهٔ درخواست پیدا نشد.")
    if not is_relevant(profile, version):
        raise forbidden("این درخواست با تخصص، خودرو یا شهر شما مرتبط نیست.")

    _validate_structure(
        offer_type=offer_type,
        version=version,
        lines=lines,
        scenarios=scenarios,
        diagnostic_fee_toman=diagnostic_fee_toman,
        diagnostic_scope=diagnostic_scope,
    )
    _validate_timing(
        request=request, scheduled_at=scheduled_at, valid_until=valid_until, policy=policy
    )

    offer = (
        await session.execute(
            select(Offer)
            .where(
                Offer.request_id == request_id,
                Offer.specialist_id == specialist_id,
                Offer.status.in_([OfferStatus.active, OfferStatus.selected]),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if offer is None:
        offer = Offer(request_id=request_id, specialist_id=specialist_id)
        session.add(offer)
        await session.flush()
        next_number = 1
    else:
        check_revision(offer, expected_revision)
        if offer.status is OfferStatus.selected:
            raise invalid_state(
                "در زمان انتظار پذیرش، فقط می‌توانید انتخاب مشتری را بپذیرید یا رد کنید."
            )
        await _guard_pending_selection(session, request_id, specialist_id)
        next_number = (
            await session.scalar(
                select(OfferVersion.version_number)
                .where(OfferVersion.offer_id == offer.id)
                .order_by(OfferVersion.version_number.desc())
                .limit(1)
            )
            or 0
        ) + 1

    totals = compute_totals(lines)
    offer_version = OfferVersion(
        offer_id=offer.id,
        request_version_id=version.id,
        version_number=next_number,
        offer_type=offer_type,
        lines=[line.model_dump(mode="json", by_alias=True) for line in lines],
        scenarios=list(scenarios),
        total_toman=totals.total_toman,
        specialist_payable_toman=totals.specialist_payable_toman,
        warranty_note=warranty_note,
        conditions_note=conditions_note,
        diagnostic_scope=diagnostic_scope,
        diagnostic_fee_toman=diagnostic_fee_toman,
        diagnostic_fee_credited=diagnostic_fee_credited,
        estimated_minutes=estimated_minutes,
        scheduled_at=scheduled_at,
        valid_until=valid_until,
    )
    session.add(offer_version)
    await session.flush()
    offer.current_version_id = offer_version.id
    offer.status = OfferStatus.active
    offer.bump()

    await audit.record(
        session,
        "offer_submitted" if next_number == 1 else "offer_revised",
        request_id=request_id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=offer.id,
        data={
            "offerType": offer_type.value,
            "versionNumber": next_number,
            "totalToman": totals.total_toman,
        },
    )
    return offer_version


async def _guard_pending_selection(
    session: AsyncSession, request_id: uuid.UUID, specialist_id: uuid.UUID
) -> None:
    pending = (
        await session.execute(
            select(Selection).where(
                Selection.request_id == request_id,
                Selection.specialist_id == specialist_id,
                Selection.status == SelectionStatus.pending,
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise invalid_state(
            "انتخاب مشتری در انتظار پاسخ شماست؛ ابتدا آن را بپذیرید یا رد کنید."
        )


async def withdraw_offer(
    session: AsyncSession,
    *,
    offer_id: uuid.UUID,
    specialist_id: uuid.UUID,
    expected_revision: int | None,
) -> Offer:
    """Allowed before selection. A later increase must go through the agreement-change path."""
    offer = await lock_row(session, Offer, offer_id)
    if offer.specialist_id != specialist_id:
        raise forbidden("این پیشنهاد متعلق به شما نیست.")
    check_revision(offer, expected_revision)
    if offer.status is OfferStatus.selected:
        raise invalid_state("پیشنهاد انتخاب‌شده را نمی‌توان پس گرفت.")
    await _guard_pending_selection(session, offer.request_id, specialist_id)

    offer.status = OfferStatus.withdrawn
    offer.bump()
    if offer.current_version_id is not None:
        current = await session.get(OfferVersion, offer.current_version_id)
        if current is not None:
            current.withdrawn_at = now()

    await audit.record(
        session,
        "offer_withdrawn",
        request_id=offer.request_id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=offer.id,
    )
    return offer


def is_version_selectable(
    version: OfferVersion, offer: Offer, request: Request, *, at: datetime_t | None = None
) -> tuple[bool, str | None]:
    """Whether a customer may still pick this exact offer version right now.

    Price validity and appointment validity are separate: an offer can be financially
    valid and still unselectable because its appointment has passed.
    """
    moment = at or now()
    if offer.status not in (OfferStatus.active, OfferStatus.selected):
        return False, "این پیشنهاد دیگر در دسترس نیست."
    if version.withdrawn_at is not None:
        return False, "این پیشنهاد پس گرفته شده است."
    if version.valid_until <= moment:
        return False, "اعتبار این پیشنهاد به پایان رسیده است."
    if version.scheduled_at <= moment:
        return False, "زمان مراجعهٔ این پیشنهاد گذشته است."
    if request.visit_window_end and version.scheduled_at > request.visit_window_end:
        return False, "زمان مراجعه خارج از بازهٔ اعلام‌شده است."
    return True, None


async def list_offers(
    session: AsyncSession, request_id: uuid.UUID
) -> list[tuple[Offer, OfferVersion]]:
    rows = (
        await session.execute(
            select(Offer, OfferVersion)
            .join(OfferVersion, OfferVersion.id == Offer.current_version_id)
            .where(Offer.request_id == request_id)
        )
    ).all()
    return [(offer, version) for offer, version in rows]


async def count_selectable_offers(
    session: AsyncSession, request: Request
) -> int:
    pairs = await list_offers(session, request.id)
    return sum(
        1 for offer, version in pairs if is_version_selectable(version, offer, request)[0]
    )
