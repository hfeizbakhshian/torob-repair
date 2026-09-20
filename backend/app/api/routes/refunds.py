"""Refund of the registration package fee."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CustomerDep, PolicyDep, SessionDep, UserDep
from app.api.serializers import refund_out
from app.domain import refunds as service
from app.domain.errors import forbidden
from app.models import Refund
from app.schemas.api import RefundInput, RefundOut

router = APIRouter(tags=["refunds"])


@router.post("/requests/{request_id}/refund", response_model=RefundOut, status_code=201)
async def request_refund(
    request_id: uuid.UUID,
    payload: RefundInput,
    session: SessionDep,
    customer: CustomerDep,
    policy: PolicyDep,
) -> RefundOut:
    """Free to ask for, independent of the chat quota, one per payment."""
    refund = await service.request_refund(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        note=payload.note,
        policy=policy,
    )
    return refund_out(refund)


@router.get("/requests/{request_id}/refund", response_model=RefundOut | None)
async def get_refund(
    request_id: uuid.UUID, session: SessionDep, user: UserDep
) -> RefundOut | None:
    refund = (
        await session.execute(select(Refund).where(Refund.request_id == request_id))
    ).scalar_one_or_none()
    if refund is None:
        return None
    if not user.is_support and refund.customer_id != user.user_id:
        raise forbidden("این بازپرداخت متعلق به حساب شما نیست.")
    return refund_out(refund)


@router.post("/refunds/{refund_id}/reopen", response_model=RefundOut)
async def reopen(
    refund_id: uuid.UUID,
    payload: RefundInput,
    session: SessionDep,
    customer: CustomerDep,
    policy: PolicyDep,
) -> RefundOut:
    """One free re-review with new evidence, within 7 days of a rejection."""
    refund = await service.request_reopen(
        session,
        refund_id=refund_id,
        customer_id=customer.user_id,
        note=payload.note or "",
        policy=policy,
    )
    return refund_out(refund)
