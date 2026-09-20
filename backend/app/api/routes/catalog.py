"""Service templates, coverage and the package terms shown before any payment."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import PolicyDep, SessionDep
from app.domain.reference import group_key, resolve_refund_policy_mode
from app.domain.requests import check_coverage
from app.models import ServiceTemplate
from app.models.enums import Party, RefundPolicyMode
from app.schemas.api import (
    CoverageQuery,
    CoverageResult,
    PolicySummary,
    ServiceTemplateOut,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])

_BOOTSTRAP_SUMMARY = (
    "در این پرونده مرجع قیمتی کافی وجود ندارد (حالت bootstrap)؛ بنابراین درخواست "
    "بازپرداخت هزینهٔ ثبت درخواست با کنترل مالکیت پرداخت و نبود استرداد قبلی پذیرفته "
    "می‌شود و نیازی به اثبات گرانی یا انتظار ۲۴ ساعته ندارد."
)
_REFERENCE_SUMMARY = (
    "برای این پرونده مرجع قیمتی آماده است؛ اعتراض قیمتی با سوابق مشابه انجام‌شده "
    "بررسی می‌شود و وجود حتی یک پیشنهاد معتبر و منصفانه، بازپرداخت قیمتی را منتفی "
    "می‌کند. نبود پیشنهاد معتبر تا پایان مهلت، مسیر بازپرداخت خودکار دارد."
)


@router.get("/services", response_model=list[ServiceTemplateOut])
async def services(session: SessionDep) -> list[ServiceTemplateOut]:
    rows = await session.execute(select(ServiceTemplate).order_by(ServiceTemplate.code))
    return [ServiceTemplateOut.model_validate(row) for row in rows.scalars()]


@router.post("/coverage", response_model=CoverageResult)
async def coverage(payload: CoverageQuery, session: SessionDep) -> CoverageResult:
    """Free and AI-free; it runs before the payment screen is ever shown."""
    supported, message = await check_coverage(
        session,
        city=payload.city,
        vehicle_code=payload.vehicle_code,
        service_code=payload.service_code,
        visit_mode=payload.visit_mode,
    )
    return CoverageResult(supported=supported, message=message)


@router.post("/package-terms", response_model=PolicySummary)
async def package_terms(
    payload: CoverageQuery, session: SessionDep, policy: PolicyDep
) -> PolicySummary:
    """The fixed fee, the per-stage turn cap and the refund policy, shown before paying."""
    key = group_key(
        city=payload.city,
        vehicle_code=payload.vehicle_code,
        service_code=payload.service_code,
        scenario_code=None,
        part_spec=None,
        part_condition="new",
        warranty_level="standard",
        buyer=Party.specialist,
    )
    mode = RefundPolicyMode(await resolve_refund_policy_mode(session, key, policy))
    return PolicySummary(
        version=policy.version,
        registration_fee_toman=policy.registration_fee_toman,
        max_turns_per_stage=policy.ai.stages["customer"].max_turns,
        offer_window_hours=policy.timing.offer_window_hours,
        refund_policy_mode=mode,
        refund_summary=(
            _BOOTSTRAP_SUMMARY if mode is RefundPolicyMode.bootstrap else _REFERENCE_SUMMARY
        ),
    )
