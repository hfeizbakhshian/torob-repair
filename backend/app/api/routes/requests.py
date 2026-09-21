"""Creating, paying for, completing and publishing a request."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header
from sqlalchemy import select

from app.api.deps import CustomerDep, PolicyDep, SessionDep, UserDep
from app.api.serializers import payment_out, request_out
from app.domain import requests as service
from app.domain.errors import forbidden, not_found
from app.domain.selection import active_selection
from app.models import Payment, Request
from app.models.enums import PaymentStatus, Role
from app.schemas.api import (
    CreateRequestInput,
    PayInput,
    PaymentOut,
    RequestOut,
    RevisionInput,
    TypoNoteInput,
)

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post("", response_model=RequestOut, status_code=201)
async def create_request(
    payload: CreateRequestInput, session: SessionDep, customer: CustomerDep
) -> RequestOut:
    request = await service.create_draft(
        session,
        customer_id=customer.user_id,
        city=payload.city,
        district=payload.district,
        vehicle_code=payload.vehicle_code,
        vehicle_details=payload.vehicle_details,
        service_code=payload.service_code,
        symptoms=payload.symptoms,
        visit_mode=payload.visit_mode,
    )
    return await request_out(session, request)


@router.get("", response_model=list[RequestOut])
async def my_requests(session: SessionDep, customer: CustomerDep) -> list[RequestOut]:
    rows = await session.execute(
        select(Request)
        .where(Request.customer_id == customer.user_id, Request.deleted_at.is_(None))
        .order_by(Request.created_at.desc())
    )
    return [await request_out(session, row) for row in rows.scalars()]


async def _visible_request(session: SessionDep, request_id: uuid.UUID, user: UserDep) -> Request:
    request = await session.get(Request, request_id)
    if request is None:
        raise not_found("این درخواست پیدا نشد.")
    if user.role is Role.support:
        return request
    if request.deleted_at is not None:
        raise not_found("این درخواست پیدا نشد.")
    if request.customer_id == user.user_id:
        return request
    # A specialist sees a case only while it is open to them, or once they are the
    # selected one; a former collaborator keeps no access to a new one.
    selection = await active_selection(session, request_id)
    if selection is not None and selection.specialist_id == user.user_id:
        return request
    raise forbidden("دسترسی به این پرونده برای حساب شما مجاز نیست.")


@router.get("/{request_id}", response_model=RequestOut)
async def get_request(
    request_id: uuid.UUID, session: SessionDep, user: UserDep
) -> RequestOut:
    request = await _visible_request(session, request_id, user)
    return await request_out(session, request)


@router.post("/{request_id}/pay", response_model=PaymentOut)
async def pay(
    request_id: uuid.UUID,
    payload: PayInput,
    session: SessionDep,
    customer: CustomerDep,
    policy: PolicyDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PaymentOut:
    """Charge the sample package fee. The gateway decides the result, not the client."""
    key = idempotency_key or payload.idempotency_key
    payment = await service.pay_registration_fee(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        idempotency_key=key,
        policy=policy,
    )
    return payment_out(payment)


@router.get("/{request_id}/payment", response_model=PaymentOut | None)
async def get_payment(
    request_id: uuid.UUID, session: SessionDep, customer: CustomerDep
) -> PaymentOut | None:
    request = await service.get_request(session, request_id)
    if request.customer_id != customer.user_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    payment = (
        await session.execute(
            select(Payment)
            .where(Payment.request_id == request_id)
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return payment_out(payment) if payment else None


@router.delete("/{request_id}", status_code=204)
async def delete_request(
    request_id: uuid.UUID,
    session: SessionDep,
    customer: CustomerDep,
    expected_revision: int | None = None,
) -> None:
    """Remove the request from the customer's list. Nothing is erased."""
    await service.delete_request(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        expected_revision=expected_revision,
    )
    await session.commit()


@router.post("/{request_id}/confirm-summary", response_model=RequestOut)
async def confirm_summary(
    request_id: uuid.UUID,
    payload: RevisionInput,
    session: SessionDep,
    customer: CustomerDep,
) -> RequestOut:
    """A standalone button that consumes no AI quota."""
    request = await service.confirm_summary(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        expected_revision=payload.expected_revision,
    )
    return await request_out(session, request)


@router.post("/{request_id}/publish", response_model=RequestOut)
async def publish(
    request_id: uuid.UUID,
    payload: RevisionInput,
    session: SessionDep,
    customer: CustomerDep,
    policy: PolicyDep,
) -> RequestOut:
    request = await service.publish(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        expected_revision=payload.expected_revision,
        policy=policy,
    )
    return await request_out(session, request)


@router.post("/{request_id}/typo-note", response_model=RequestOut)
async def typo_note(
    request_id: uuid.UUID,
    payload: TypoNoteInput,
    session: SessionDep,
    customer: CustomerDep,
) -> RequestOut:
    """A wording fix: recorded as a note, it does not invalidate existing offers."""
    request = await service.get_request(session, request_id)
    if request.customer_id != customer.user_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    await service.add_typo_note(
        session, request=request, customer_id=customer.user_id, note=payload.note
    )
    return await request_out(session, request)


@router.post("/{request_id}/abandon", response_model=RequestOut)
async def abandon(
    request_id: uuid.UUID, session: SessionDep, customer: CustomerDep
) -> RequestOut:
    request = await service.abandon_before_publish(
        session, request_id=request_id, customer_id=customer.user_id
    )
    return await request_out(session, request)


@router.get("/{request_id}/payments", response_model=list[PaymentOut])
async def payments(
    request_id: uuid.UUID, session: SessionDep, user: UserDep
) -> list[PaymentOut]:
    await _visible_request(session, request_id, user)
    rows = await session.execute(
        select(Payment)
        .where(Payment.request_id == request_id, Payment.status != PaymentStatus.created)
        .order_by(Payment.created_at)
    )
    return [payment_out(row) for row in rows.scalars()]
