"""The demo control panel: the injected clock, the scenario flags and the gateway switch.

Support-only, and only in a demo installation. Nothing here touches the system clock or any
file outside the project.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import select

from app import clock
from app.api.deps import SessionDep, SupportDep
from app.api.serializers import evaluation_out
from app.config import settings
from app.domain import demo_control
from app.domain.errors import not_found
from app.models import Evaluation
from app.providers.payment import Outcome, get_gateway
from app.schemas.api import (
    AdvanceClockInput,
    DemoFlagInput,
    DemoStateOut,
    EvaluationOut,
    GatewayOutcomeInput,
)

router = APIRouter(prefix="/demo", tags=["demo"])


async def _state(session: SessionDep) -> DemoStateOut:
    return DemoStateOut(
        app_mode=settings.app_mode,
        ai_mode=settings.ai_mode,
        server_time=clock.now(),
        clock_offset_seconds=clock.offset().total_seconds(),
        demo_reference_ready=await demo_control.is_demo_reference_ready(session),
    )


@router.get("/state", response_model=DemoStateOut)
async def state(session: SessionDep, _: SupportDep) -> DemoStateOut:
    return await _state(session)


@router.post("/advance-clock", response_model=DemoStateOut)
async def advance_clock(
    payload: AdvanceClockInput, session: SessionDep, _: SupportDep
) -> DemoStateOut:
    """Move the demo clock forward only, so deadlines can be reached without waiting."""
    await demo_control.advance_clock(session, timedelta(seconds=payload.seconds))
    return await _state(session)


@router.post("/reset-clock", response_model=DemoStateOut)
async def reset_clock(session: SessionDep, _: SupportDep) -> DemoStateOut:
    await demo_control.reset_clock(session)
    return await _state(session)


@router.post("/reference-ready", response_model=DemoStateOut)
async def set_reference_ready(
    payload: DemoFlagInput, session: SessionDep, _: SupportDep
) -> DemoStateOut:
    """Switch the demo to the data-driven refund policy.

    This flag is deliberately independent of the reference statistics: the sample rows are
    still not real market data and never make a real group ready.
    """
    await demo_control.set_demo_reference_ready(session, payload.enabled)
    return await _state(session)


@router.post("/gateway-outcome", status_code=200)
async def queue_gateway_outcome(
    payload: GatewayOutcomeInput, _: SupportDep
) -> dict[str, str]:
    """Force the next simulated payment or transfer to fail, to exercise the retry path."""
    demo_control.require_demo_mode()
    get_gateway().queue_outcome(Outcome(payload.outcome))
    return {"queued": payload.outcome}


@router.get("/evaluations/{request_id}", response_model=EvaluationOut)
async def evaluation_for(
    request_id: uuid.UUID, session: SessionDep, _: SupportDep
) -> EvaluationOut:
    evaluation = (
        await session.execute(
            select(Evaluation).where(
                Evaluation.request_id == request_id, Evaluation.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if evaluation is None:
        raise not_found("ارزیابی برای این پرونده ثبت نشده است.")
    return evaluation_out(evaluation)
