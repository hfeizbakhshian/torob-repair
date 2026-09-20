"""Request lifecycle: draft, coverage, sample payment, intake and publication."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit, jobs
from app.domain.errors import (
    forbidden,
    invalid_state,
    not_found,
    validation_error,
)
from app.domain.policy_service import ensure_active_policy
from app.domain.reference import group_key, resolve_refund_policy_mode
from app.models import (
    Coverage,
    IdempotencyRecord,
    Payment,
    Request,
    RequestVersion,
    ServiceTemplate,
)
from app.models.enums import (
    CloseReason,
    JobKind,
    Party,
    PaymentStatus,
    RefundPolicyMode,
    RequestStatus,
)
from app.policy import Policy
from app.providers.payment import get_gateway
from app.repository.locking import check_revision, lock_row


async def check_coverage(
    session: AsyncSession,
    *,
    city: str,
    vehicle_code: str,
    service_code: str,
    visit_mode: str = "shop",
) -> tuple[bool, str | None]:
    """Free and AI-free. Runs before any payment screen is shown."""
    row = (
        await session.execute(
            select(Coverage).where(
                Coverage.city == city,
                Coverage.vehicle_code == vehicle_code,
                Coverage.service_code == service_code,
                Coverage.visit_mode == visit_mode,
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.is_supported:
        return False, "این ترکیب شهر، خودرو و خدمت فعلاً در پوشش این دمو نیست."
    return True, None


async def get_request(session: AsyncSession, request_id: uuid.UUID) -> Request:
    request = await session.get(Request, request_id)
    if request is None:
        raise not_found("این درخواست پیدا نشد.")
    return request


async def current_version(session: AsyncSession, request: Request) -> RequestVersion:
    if request.current_version_id is None:
        raise invalid_state("این درخواست هنوز نسخهٔ دامنه ندارد.")
    version = await session.get(RequestVersion, request.current_version_id)
    if version is None:
        raise invalid_state("نسخهٔ دامنهٔ این درخواست پیدا نشد.")
    return version


async def create_draft(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    city: str,
    district: str,
    vehicle_code: str,
    vehicle_details: dict[str, Any],
    service_code: str,
    symptoms: str,
    visit_mode: str = "shop",
) -> Request:
    supported, message = await check_coverage(
        session,
        city=city,
        vehicle_code=vehicle_code,
        service_code=service_code,
        visit_mode=visit_mode,
    )
    if not supported:
        raise validation_error(message or "خارج از پوشش دمو است.", serviceCode=message or "")

    template = (
        await session.execute(
            select(ServiceTemplate).where(ServiceTemplate.code == service_code)
        )
    ).scalar_one_or_none()
    if template is None:
        raise not_found("قالب این خدمت تعریف نشده است.")

    request = Request(customer_id=customer_id, status=RequestStatus.draft)
    session.add(request)
    await session.flush()

    version = RequestVersion(
        request_id=request.id,
        version_number=1,
        city=city,
        district=district,
        vehicle_code=vehicle_code,
        vehicle_details=vehicle_details,
        service_code=service_code,
        visit_mode=visit_mode,
        symptoms=symptoms,
        allowed_offer_types=list(template.allowed_offer_types),
    )
    session.add(version)
    await session.flush()
    request.current_version_id = version.id

    await audit.record(
        session,
        "request_drafted",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        data={"serviceCode": service_code, "city": city},
    )
    return request


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def replay_or_reserve_idempotency(
    session: AsyncSession,
    *,
    scope: str,
    key: str | None,
    user_id: uuid.UUID,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the stored response for a replayed key, or `None` to run the operation.

    The same key with a different body is an error rather than a second effect.
    """
    if key is None:
        return None
    fingerprint = _fingerprint(payload)
    existing = (
        await session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
                IdempotencyRecord.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_fingerprint != fingerprint:
        raise validation_error(
            "این کلید تکرار قبلاً با محتوای دیگری استفاده شده است.",
            idempotencyKey="کلید تکراری با محتوای متفاوت",
        )
    return existing.response_payload


async def store_idempotency(
    session: AsyncSession,
    *,
    scope: str,
    key: str | None,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> None:
    if key is None:
        return
    session.add(
        IdempotencyRecord(
            scope=scope,
            key=key,
            user_id=user_id,
            request_fingerprint=_fingerprint(payload),
            response_payload=response,
        )
    )


async def pay_registration_fee(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    customer_id: uuid.UUID,
    idempotency_key: str,
    policy: Policy,
) -> Payment:
    """Charge the sample package fee and pin the refund policy that was in force.

    The result only ever comes from the gateway adapter; neither the client nor the model
    can declare a payment successful.
    """
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    if request.status is not RequestStatus.draft:
        raise invalid_state("پرداخت فقط روی پیش‌نویس درخواست انجام می‌شود.")

    succeeded = (
        await session.execute(
            select(Payment).where(
                Payment.request_id == request_id, Payment.status == PaymentStatus.succeeded
            )
        )
    ).scalar_one_or_none()
    if succeeded is not None:
        return succeeded

    # An Idempotency-Key is global. Replaying it for the same request returns the same
    # payment; reusing it for a different one is a client mistake and must produce the
    # documented validation error rather than a constraint violation.
    reused = (
        await session.execute(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if reused is not None:
        if reused.request_id != request_id or reused.customer_id != customer_id:
            raise validation_error(
                "این کلید تکرار قبلاً برای پرداخت دیگری استفاده شده است.",
                idempotencyKey="کلید تکراری با محتوای متفاوت",
            )
        return reused

    version = await current_version(session, request)
    policy_row = await ensure_active_policy(session)
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
    mode = RefundPolicyMode(await resolve_refund_policy_mode(session, key, policy))

    payment = Payment(
        request_id=request.id,
        customer_id=customer_id,
        amount_toman=policy.registration_fee_toman,
        idempotency_key=idempotency_key,
        policy_version_id=policy_row.id,
        refund_policy_mode=mode,
        is_sample=True,
    )
    session.add(payment)
    await session.flush()

    result = get_gateway().charge(idempotency_key, payment.amount_toman)
    payment.status = PaymentStatus.succeeded if result.succeeded else PaymentStatus.failed
    payment.gateway_reference = result.reference
    payment.settled_at = now()

    if result.succeeded:
        request.refund_policy_mode = mode
        request.policy_version_id = policy_row.id
        request.bump()

    await audit.record(
        session,
        "payment_settled",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=payment.id,
        data={
            "amountToman": payment.amount_toman,
            "status": payment.status.value,
            "refundPolicyMode": mode.value,
            "isSample": True,
        },
    )
    return payment


async def has_successful_payment(session: AsyncSession, request_id: uuid.UUID) -> bool:
    count = await session.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.request_id == request_id, Payment.status == PaymentStatus.succeeded)
    )
    return bool(count)


async def record_intake(
    session: AsyncSession,
    *,
    request: Request,
    answers: dict[str, Any],
    facts: list[str],
    unknowns: list[str],
    allowed_offer_types: list[str],
) -> RequestVersion:
    """Fold an AI turn's result into the draft's scope version. Free of charge by itself."""
    version = await current_version(session, request)
    if request.status is not RequestStatus.draft:
        raise invalid_state("تکمیل مشخصات فقط روی پیش‌نویس انجام می‌شود.")
    version.answers = {**version.answers, **answers}
    version.summary_facts = facts
    version.summary_unknowns = unknowns
    if allowed_offer_types:
        version.allowed_offer_types = allowed_offer_types
    version.summary_confirmed_at = None
    return version


async def confirm_summary(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    customer_id: uuid.UUID,
    expected_revision: int | None,
) -> Request:
    """A standalone button. It consumes no AI quota."""
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    check_revision(request, expected_revision)
    if request.status is not RequestStatus.draft:
        raise invalid_state("این درخواست دیگر در مرحلهٔ پیش‌نویس نیست.")

    version = await current_version(session, request)
    if not version.summary_facts:
        raise invalid_state("ابتدا خلاصهٔ درخواست باید ساخته شود.")
    version.summary_confirmed_at = now()
    request.bump()
    await audit.record(
        session,
        "summary_confirmed",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
    )
    return request


async def set_visit_window(
    session: AsyncSession, *, request: Request, policy: Policy, deadline: datetime
) -> None:
    """The visit window opens when the offer deadline closes and lasts at most 7 days."""
    request.visit_window_start = deadline
    request.visit_window_end = deadline + timedelta(days=policy.timing.visit_window_max_days)


async def publish(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    customer_id: uuid.UUID,
    expected_revision: int | None,
    policy: Policy,
) -> Request:
    """Open the request to every active, relevant specialist.

    Publication needs a confirmed summary and a settled sample payment; the 24-hour offer
    deadline is stamped from server time and is never auto-extended.
    """
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    check_revision(request, expected_revision)
    if request.status is not RequestStatus.draft:
        raise invalid_state("این درخواست قبلاً منتشر شده است.")

    version = await current_version(session, request)
    if version.summary_confirmed_at is None:
        raise invalid_state("پیش از انتشار باید خلاصهٔ درخواست را تأیید کنید.")
    if not await has_successful_payment(session, request.id):
        raise invalid_state("پرداخت آزمایشی ثبت درخواست هنوز موفق نشده است.")

    published_at = now()
    request.published_at = published_at
    request.response_deadline = published_at + timedelta(hours=policy.timing.offer_window_hours)
    request.status = RequestStatus.open
    await set_visit_window(
        session, request=request, policy=policy, deadline=request.response_deadline
    )
    request.bump()

    await jobs.schedule(
        session,
        JobKind.close_offer_window,
        request.response_deadline,
        subject_id=request.id,
        dedupe_key=f"close_offer_window:{request.id}",
    )
    await audit.record(
        session,
        "request_published",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        data={
            "responseDeadline": request.response_deadline.isoformat(),
            "visitWindowEnd": request.visit_window_end.isoformat()
            if request.visit_window_end
            else None,
        },
    )
    return request


async def add_typo_note(
    session: AsyncSession,
    *,
    request: Request,
    customer_id: uuid.UUID,
    note: str,
) -> RequestVersion:
    """A wording fix.

    It is recorded as a note with an event and deliberately does *not* create a new scope
    version, so the offers already made stay valid. A material change of vehicle, service
    or described problem needs a brand-new request instead.
    """
    version = await current_version(session, request)
    version.typo_notes = [
        *version.typo_notes,
        {"at": now().isoformat(), "note": note[:400]},
    ]
    await audit.record(
        session,
        "request_typo_note",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        reason=note[:400],
    )
    return version


async def abandon_before_publish(
    session: AsyncSession, *, request_id: uuid.UUID, customer_id: uuid.UUID
) -> Request:
    """Give up on a draft. The customer may ask for the package fee back and the case closes."""
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    if request.status is not RequestStatus.draft:
        raise invalid_state("این مسیر فقط برای درخواست منتشرنشده است.")
    request.status = RequestStatus.cancelled
    request.close_reason = CloseReason.abandoned_before_publish
    request.closed_at = now()
    request.bump()
    await audit.record(
        session,
        "request_abandoned",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
    )
    return request
