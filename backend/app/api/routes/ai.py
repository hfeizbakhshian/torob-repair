"""The AI endpoints: the three conversation stages and the budget view."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import AiDep, CustomerDep, PolicyDep, SessionDep, SpecialistDep, UserDep
from app.config import settings
from app.domain import ai_flows
from app.domain.ai_service import AiOutcome
from app.domain.errors import forbidden, not_found
from app.domain.requests import get_request
from app.models import AiRun, BudgetReservation, Selection
from app.models.enums import AiPurpose, AiRunStatus, AiStage
from app.schemas.api import (
    AiBudgetOut,
    AiRunOut,
    AiUsageOut,
    AnswersInput,
    CaseQuestionInput,
    ExtractExpensesInput,
)

router = APIRouter(tags=["ai"])

DEMO_NOTICE = (
    "این پاسخ با ارائه‌دهندهٔ نمایشی ساخته شده است و اجرای مدل واقعی نیست."
)
LIVE_NOTICE = "پاسخ از مدل پیکربندی‌شده دریافت شده است."


def _to_out(outcome: AiOutcome) -> AiRunOut:
    return AiRunOut(
        run_id=outcome.run_id,
        status=outcome.status.value,
        payload=outcome.payload,
        error_code=outcome.error_code,
        message=outcome.message,
        is_demo_response=settings.ai_mode == "mock",
    )


@router.get("/requests/{request_id}/ai/questions", response_model=AiRunOut | None)
async def last_questions(
    request_id: uuid.UUID, session: SessionDep, customer: CustomerDep
) -> AiRunOut | None:
    """The questions already asked, so leaving the page does not strand the draft.

    Reading back an answer that was already paid for costs no turn and runs no model.
    """
    request = await get_request(session, request_id)
    if request.customer_id != customer.user_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    run = (
        await session.execute(
            select(AiRun)
            .where(
                AiRun.request_id == request_id,
                AiRun.purpose == AiPurpose.clarify_questions,
                AiRun.status == AiRunStatus.succeeded,
            )
            .order_by(AiRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None or run.output_payload is None:
        return None
    return AiRunOut(
        run_id=run.id,
        status=run.status.value,
        payload=run.output_payload,
        is_demo_response=settings.ai_mode == "mock",
    )


@router.post("/requests/{request_id}/ai/questions", response_model=AiRunOut)
async def ask_questions(
    request_id: uuid.UUID, session: SessionDep, customer: CustomerDep, ai: AiDep
) -> AiRunOut:
    """Stage one, turn one: every necessary question at once, mostly multiple choice."""
    request = await get_request(session, request_id)
    if request.customer_id != customer.user_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    await session.commit()
    outcome = await ai_flows.clarify_request(
        ai, request_id=request_id, actor_id=customer.user_id
    )
    return _to_out(outcome)


@router.post("/requests/{request_id}/ai/summary", response_model=AiRunOut)
async def summarise(
    request_id: uuid.UUID,
    payload: AnswersInput,
    session: SessionDep,
    customer: CustomerDep,
    ai: AiDep,
) -> AiRunOut:
    """Stage one, turn two: the summary and what is still unknown."""
    request = await get_request(session, request_id)
    if request.customer_id != customer.user_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    await session.commit()
    outcome = await ai_flows.summarise_request(
        ai, request_id=request_id, actor_id=customer.user_id, answers=payload.answers
    )
    return _to_out(outcome)


@router.post("/requests/{request_id}/ai/comparison", response_model=AiRunOut)
async def explain_comparison(
    request_id: uuid.UUID, session: SessionDep, customer: CustomerDep, ai: AiDep
) -> AiRunOut:
    """Auxiliary task, at most once per case."""
    request = await get_request(session, request_id)
    if request.customer_id != customer.user_id:
        raise forbidden("این درخواست متعلق به حساب شما نیست.")
    await session.commit()
    outcome = await ai_flows.explain_comparison(
        ai, request_id=request_id, actor_id=customer.user_id
    )
    return _to_out(outcome)


@router.post("/selections/{selection_id}/ai/ask", response_model=AiRunOut)
async def ask_about_case(
    selection_id: uuid.UUID,
    payload: CaseQuestionInput,
    session: SessionDep,
    user: UserDep,
    ai: AiDep,
) -> AiRunOut:
    """Stage two: the assistant shared by the customer and the selected specialist.

    Both draw on one quota, so the second person does not double the allowance.
    """
    selection = await session.get(Selection, selection_id)
    if selection is None:
        raise not_found("همکاری پیدا نشد.")
    await session.commit()
    outcome = await ai_flows.answer_case_question(
        ai,
        selection_id=selection_id,
        actor_id=user.user_id,
        question=payload.question,
    )
    return _to_out(outcome)


@router.post("/selections/{selection_id}/ai/extract-expenses", response_model=AiRunOut)
async def extract_expenses(
    selection_id: uuid.UUID,
    payload: ExtractExpensesInput,
    session: SessionDep,
    specialist: SpecialistDep,
    ai: AiDep,
) -> AiRunOut:
    """Turn free text into line items. The specialist reviews the result afterwards."""
    selection = await session.get(Selection, selection_id)
    if selection is None:
        raise not_found("همکاری پیدا نشد.")
    if selection.specialist_id != specialist.user_id:
        raise forbidden("ثبت مخارج بر عهدهٔ متخصص منتخب است.")
    request_id = selection.request_id
    await session.commit()
    outcome = await ai_flows.extract_expenses(
        ai,
        request_id=request_id,
        selection_id=selection_id,
        actor_id=specialist.user_id,
        text=payload.text,
    )
    return _to_out(outcome)


@router.get("/requests/{request_id}/ai/usage", response_model=AiUsageOut)
async def usage(
    request_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    ai: AiDep,
    policy: PolicyDep,
) -> AiUsageOut:
    """What the case has spent, per stage, with the caps it is measured against."""
    request = await get_request(session, request_id)
    if not user.is_support and request.customer_id != user.user_id:
        selection = (
            await session.execute(
                select(Selection).where(Selection.request_id == request_id)
            )
        ).scalars().first()
        if selection is None or selection.specialist_id != user.user_id:
            raise forbidden("مشاهدهٔ مصرف این پرونده مجاز نیست.")

    stages: list[AiBudgetOut] = []
    for name, quota in policy.ai.stages.items():
        stage = AiStage(name)
        turns = await session.scalar(
            select(func.count())
            .select_from(AiRun)
            .where(
                AiRun.request_id == request_id,
                AiRun.stage == stage,
                AiRun.counts_as_successful_turn.is_(True),
            )
        )
        used_in = 0
        used_out = 0
        rows = (
            await session.execute(
                select(AiRun, BudgetReservation)
                .join(BudgetReservation, BudgetReservation.ai_run_id == AiRun.id)
                .where(
                    AiRun.request_id == request_id,
                    BudgetReservation.stage == stage,
                    BudgetReservation.scope == "case",
                    BudgetReservation.released_at.is_(None),
                )
            )
        ).all()
        for run, reservation in rows:
            if run.actual_input_tokens is not None and not reservation.unresolved:
                used_in += run.actual_input_tokens
                used_out += run.actual_output_tokens or reservation.output_tokens
            else:
                used_in += reservation.input_tokens
                used_out += reservation.output_tokens

        stages.append(
            AiBudgetOut(
                stage=name,
                turns_used=int(turns or 0),
                max_turns=quota.max_turns,
                input_tokens_used=used_in,
                input_tokens_limit=quota.input_tokens,
                output_tokens_used=used_out,
                output_tokens_limit=quota.output_tokens,
            )
        )

    calls = await session.scalar(
        select(func.count())
        .select_from(AiRun)
        .where(AiRun.request_id == request_id, AiRun.attempt == 1)
    )
    return AiUsageOut(
        provider=ai.provider.name,
        model=ai.provider.model,
        is_demo=settings.ai_mode == "mock",
        stages=stages,
        case_calls_used=int(calls or 0),
        case_calls_limit=policy.ai.max_normal_calls_per_case,
        note=DEMO_NOTICE if settings.ai_mode == "mock" else LIVE_NOTICE,
    )
