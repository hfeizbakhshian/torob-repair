"""Refunding the registration package fee.

What is refundable is the package fee of this case only — never labour, parts, a diagnostic
charge or any specialist subscription. Asking for a review is free and does not depend on
the chat quota. Entitlement and transfer are two separate events: only an approved refund
may move to a transfer, and a rejected one never creates transfer work.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit, jobs, offers as offers_service
from app.domain.errors import forbidden, invalid_state, not_found
from app.models import Offer, OfferVersion, Payment, Refund, Request
from app.models.enums import (
    JobKind,
    PaymentStatus,
    RefundPolicyMode,
    RefundReason,
    RefundStatus,
    RequestStatus,
)
from app.policy import Policy
from app.providers.payment import get_gateway
from app.repository.locking import lock_row


async def _successful_payment(session: AsyncSession, request_id: uuid.UUID) -> Payment:
    payment = (
        await session.execute(
            select(Payment).where(
                Payment.request_id == request_id, Payment.status == PaymentStatus.succeeded
            )
        )
    ).scalar_one_or_none()
    if payment is None:
        # No successful original payment means there is nothing to refund at all.
        raise invalid_state("پرداخت موفقی برای این پرونده ثبت نشده است.")
    return payment


async def snapshot_offers(
    session: AsyncSession, request: Request
) -> list[dict[str, Any]]:
    """Freeze the valid, selectable offer versions at the moment offers stopped.

    An offer withdrawn or refused before this point, or one that was never performable,
    does not block a refund. A later natural expiry of an offer the customer genuinely had
    the chance to pick does not create a fresh entitlement either.
    """
    at = request.offers_closed_at or request.response_deadline or now()
    rows = (
        await session.execute(
            select(Offer, OfferVersion)
            .join(OfferVersion, OfferVersion.offer_id == Offer.id)
            .where(Offer.request_id == request.id)
        )
    ).all()

    snapshot: list[dict[str, Any]] = []
    for offer, version in rows:
        selectable, reason = offers_service.is_version_selectable(version, offer, request, at=at)
        if version.rejected_capability_at is not None:
            selectable, reason = False, "متخصص توان انجام این کار را رد کرده است."
        snapshot.append(
            {
                "offerId": str(offer.id),
                "offerVersionId": str(version.id),
                "offerType": version.offer_type.value,
                "totalToman": version.total_toman,
                "scheduledAt": version.scheduled_at.isoformat(),
                "wasSelectable": selectable,
                "reason": reason,
            }
        )
    return snapshot


async def request_refund(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    customer_id: uuid.UUID,
    note: str | None,
    policy: Policy,
) -> Refund:
    """Open a free review. One full refund per payment, guaranteed by a unique constraint."""
    request = await lock_row(session, Request, request_id)
    if request.customer_id != customer_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    payment = await _successful_payment(session, request_id)

    existing = (
        await session.execute(
            select(Refund).where(Refund.payment_id == payment.id).with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    refund = Refund(
        payment_id=payment.id,
        request_id=request_id,
        customer_id=customer_id,
        amount_toman=payment.amount_toman,
        status=RefundStatus.requested,
        policy_mode=payment.refund_policy_mode,
        policy_version_id=payment.policy_version_id,
        customer_note=note,
        offers_snapshot=await snapshot_offers(session, request),
    )
    session.add(refund)
    await session.flush()

    await audit.record(
        session,
        "refund_requested",
        request_id=request_id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=refund.id,
        data={"policyMode": refund.policy_mode.value},
    )
    await _decide(session, refund=refund, request=request, policy=policy)
    return refund


async def _decide(
    session: AsyncSession, *, refund: Refund, request: Request, policy: Policy
) -> Refund:
    """Apply the decision order from the spec.

    1. No valid offer by the deadline and no successful selection → automatic entitlement,
       with no AI price analysis, under either policy.
    2. A bootstrap case → accepted on ownership and no prior refund alone; no 24-hour wait,
       no proof of overcharging, no AI approval, and a fair offer does not block it.
    3. Otherwise → a price complaint that needs the reference-based review.
    """
    deadline_passed = (
        request.response_deadline is not None and request.response_deadline <= now()
    )
    never_selected = request.status in (
        RequestStatus.open,
        RequestStatus.closed_unselected,
        RequestStatus.draft,
        RequestStatus.cancelled,
    )
    had_selectable = any(item["wasSelectable"] for item in refund.offers_snapshot)

    if request.close_reason is not None and request.status is RequestStatus.cancelled:
        if not request.published_at:
            return await _approve(
                session, refund, RefundReason.abandoned_before_publish, "پرونده پیش از انتشار رها شد."
            )

    if deadline_passed and never_selected and not had_selectable:
        return await _approve(
            session,
            refund,
            RefundReason.no_valid_offer,
            "تا پایان مهلت، پیشنهاد معتبری برای انتخاب وجود نداشت.",
        )

    if refund.policy_mode is RefundPolicyMode.bootstrap:
        return await _approve(
            session,
            refund,
            RefundReason.bootstrap_policy,
            "در حالت bootstrap، درخواست مشتری بدون بررسی قیمتی AI پذیرفته می‌شود.",
        )

    # Reference-based policy: the review starts when offers stop being accepted; an
    # earlier request simply waits in the queue.
    refund.status = RefundStatus.reviewing
    starts_at = request.offers_closed_at or request.response_deadline or now()
    refund.review_started_at = max(now(), starts_at)
    refund.review_deadline = refund.review_started_at + timedelta(
        hours=policy.timing.refund_review_hours
    )
    await jobs.schedule(
        session,
        JobKind.run_refund_review,
        refund.review_started_at,
        subject_id=refund.id,
        dedupe_key=f"refund_review:{refund.id}",
    )
    await audit.record(
        session,
        "refund_review_queued",
        request_id=refund.request_id,
        actor_role="system",
        subject_id=refund.id,
        data={"reviewDeadline": refund.review_deadline.isoformat()},
    )
    return refund


async def _approve(
    session: AsyncSession, refund: Refund, reason: RefundReason, note: str
) -> Refund:
    refund.status = RefundStatus.approved
    refund.reason = reason
    refund.decided_at = now()
    refund.decision_note = note
    await jobs.schedule(
        session,
        JobKind.refund_transfer,
        now(),
        subject_id=refund.id,
        dedupe_key=f"refund_transfer:{refund.id}",
    )
    await audit.record(
        session,
        "refund_approved",
        request_id=refund.request_id,
        actor_role="system",
        subject_id=refund.id,
        reason=reason.value,
        data={"amountToman": refund.amount_toman},
    )
    return refund


async def reject(
    session: AsyncSession, *, refund_id: uuid.UUID, note: str, policy: Policy
) -> Refund:
    """A rejection creates no transfer work of any kind."""
    refund = await lock_row(session, Refund, refund_id)
    refund.status = RefundStatus.rejected
    refund.decided_at = now()
    refund.decision_note = note
    refund.reopen_deadline = now() + timedelta(days=policy.timing.refund_reopen_days)
    await audit.record(
        session,
        "refund_rejected",
        request_id=refund.request_id,
        actor_role="system",
        subject_id=refund.id,
        reason=note,
    )
    return refund


async def approve_by_review(
    session: AsyncSession, *, refund_id: uuid.UUID, reason: RefundReason, note: str
) -> Refund:
    refund = await lock_row(session, Refund, refund_id)
    if refund.status not in (RefundStatus.reviewing, RefundStatus.needs_review):
        raise invalid_state("این بازپرداخت در مرحلهٔ بررسی نیست.")
    return await _approve(session, refund, reason, note)


async def run_transfer(session: AsyncSession, *, refund_id: uuid.UUID) -> Refund:
    """Move an approved refund through the simulated transfer.

    The same transfer reference is reused on retry so the gateway cannot pay twice, and the
    total refunded can never exceed what was paid.
    """
    refund = await lock_row(session, Refund, refund_id)
    if refund.status not in (RefundStatus.approved, RefundStatus.transfer_failed):
        return refund
    payment = await session.get(Payment, refund.payment_id)
    if payment is None or payment.status is not PaymentStatus.succeeded:
        raise invalid_state("پرداخت اصلی موفق نبوده و بازپرداختی ساخته نمی‌شود.")
    if refund.amount_toman > payment.amount_toman:
        raise invalid_state("مبلغ بازپرداخت از مبلغ پرداخت‌شده بیشتر است.")

    if refund.transfer_reference is None:
        refund.transfer_reference = f"rf-{refund.id.hex[:16]}"
    refund.status = RefundStatus.transfer_pending
    refund.transfer_attempts += 1

    result = get_gateway().refund(refund.transfer_reference, refund.amount_toman)
    if result.succeeded:
        refund.status = RefundStatus.paid
        refund.transferred_at = now()
    else:
        refund.status = RefundStatus.transfer_failed

    await audit.record(
        session,
        "refund_transfer_result",
        request_id=refund.request_id,
        actor_role="system",
        subject_id=refund.id,
        data={
            "status": refund.status.value,
            "attempts": refund.transfer_attempts,
            "isSample": True,
        },
    )
    return refund


async def request_reopen(
    session: AsyncSession,
    *,
    refund_id: uuid.UUID,
    customer_id: uuid.UUID,
    note: str,
    policy: Policy,
) -> Refund:
    """One free re-review with new evidence, within 7 days of a rejection.

    Beyond that it is recorded for support rather than looping the model again.
    """
    refund = await lock_row(session, Refund, refund_id)
    if refund.customer_id != customer_id:
        raise forbidden("این بازپرداخت متعلق به حساب شما نیست.")
    if refund.status is not RefundStatus.rejected:
        raise invalid_state("بازبینی فقط برای نتیجهٔ رد ممکن است.")
    if refund.reopen_deadline is not None and now() > refund.reopen_deadline:
        raise invalid_state("مهلت ۷ روزهٔ درخواست بازبینی گذشته است.")
    if refund.review_rounds_used >= 1:
        raise invalid_state(
            "یک بازبینی برای این نتیجه انجام شده است؛ ادامه از مسیر پشتیبانی پیگیری می‌شود."
        )

    refund.review_rounds_used += 1
    refund.status = RefundStatus.needs_review
    refund.customer_note = note
    refund.review_started_at = now()
    refund.review_deadline = now() + timedelta(hours=policy.timing.refund_review_hours)
    await jobs.schedule(
        session,
        JobKind.run_refund_review,
        now(),
        subject_id=refund.id,
        dedupe_key=f"refund_review:{refund.id}:{refund.review_rounds_used}",
    )
    await audit.record(
        session,
        "refund_reopen_requested",
        request_id=refund.request_id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=refund.id,
        reason=note,
    )
    return refund


async def get_refund(session: AsyncSession, refund_id: uuid.UUID) -> Refund:
    refund = await session.get(Refund, refund_id)
    if refund is None:
        raise not_found("این بازپرداخت پیدا نشد.")
    return refund
