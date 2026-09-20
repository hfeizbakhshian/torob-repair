"""The market reference: comparison groups built from finished repairs.

Only *completed* work with itemisation, cost evidence, both parties' confirmation, consent
for anonymous use and support approval may back a group. A quoted-but-never-done price, a
figure the model remembers, and an amount fixed by an AI ruling without those independent
confirmations, are all excluded. Nothing is ever dropped merely for being far from the
others — support must record a reason.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain.demo_control import is_demo_reference_ready
from app.models import ReferenceCase, ReferenceSnapshot
from app.models.enums import Party, ReferenceQuality
from app.policy import Policy


def group_key(
    *,
    city: str,
    vehicle_code: str,
    service_code: str,
    scenario_code: str | None,
    part_spec: str | None,
    part_condition: str,
    warranty_level: str,
    buyer: Party,
) -> str:
    """Exact grouping.

    The district alone never creates a new group, and who buys the part is part of the
    key so the whole-cost figures stay on the same basis.
    """
    parts = [
        city,
        vehicle_code,
        service_code,
        scenario_code or "-",
        part_spec or "-",
        part_condition,
        warranty_level,
        buyer.value,
    ]
    return "|".join(parts)


def _mean_toman(values: list[int]) -> int:
    total = Decimal(sum(values))
    mean = total / Decimal(len(values))
    return int(mean.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def build_snapshot(
    session: AsyncSession,
    key: str,
    policy: Policy,
    *,
    excluded_specialist_ids: set[str] | None = None,
) -> ReferenceSnapshot:
    """Freeze the statistics of one group at this moment.

    The snapshot is stored, so a review that used it can always be re-read exactly as it
    was decided.
    """
    window_start = now() - timedelta(days=policy.reference.window_days)
    rows = list(
        (
            await session.execute(
                select(ReferenceCase).where(
                    ReferenceCase.group_key == key,
                    ReferenceCase.completed_at >= window_start,
                )
            )
        ).scalars()
    )

    eligible = [
        case
        for case in rows
        if case.quality is not ReferenceQuality.self_reported
        and case.consent_for_anonymous_use
        and case.support_approved_at is not None
        and case.excluded_reason is None
        and not (
            excluded_specialist_ids and str(case.specialist_id) in excluded_specialist_ids
        )
    ]
    # Demo rows are counted and shown, but they can never make a real group ready.
    real = [case for case in eligible if not case.is_sample]
    is_sample_group = bool(eligible) and not real
    population = real if real else eligible

    reason: str | None = None
    ready = False
    if population:
        counts = Counter(str(case.specialist_id) for case in population)
        specialist_count = len(counts)
        share = Decimal(max(counts.values())) / Decimal(len(population))
        if len(population) < policy.reference.min_cases:
            reason = "تعداد پروندهٔ معتبر کمتر از حد لازم است."
        elif specialist_count < policy.reference.min_specialists:
            reason = "تعداد متخصص مستقل کمتر از حد لازم است."
        elif share > policy.reference.max_specialist_share:
            reason = "سهم یک متخصص از نمونه‌ها بیش از حد مجاز است."
        else:
            ready = not is_sample_group
            if is_sample_group:
                reason = "این گروه فقط با دادهٔ نمونه پر شده و مرجع واقعی محسوب نمی‌شود."
    else:
        specialist_count = 0
        share = Decimal(0)
        reason = "برای این گروه سابقهٔ معتبری ثبت نشده است."

    amounts = [case.total_toman for case in population]
    snapshot = ReferenceSnapshot(
        group_key=key,
        is_ready=ready,
        case_count=len(population),
        specialist_count=specialist_count,
        max_specialist_share=float(share),
        mean_toman=_mean_toman(amounts) if amounts else None,
        min_toman=min(amounts) if amounts else None,
        max_toman=max(amounts) if amounts else None,
        window_days=policy.reference.window_days,
        case_ids=[str(case.id) for case in population],
        is_sample=is_sample_group,
        not_ready_reason=reason,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def resolve_refund_policy_mode(
    session: AsyncSession, key: str, policy: Policy
) -> str:
    """Pick the refund policy in force for a request, at the moment it is paid for.

    `bootstrap` whenever the exact group has no ready reference — which, in the default
    demo, it does not. The demo flag can switch a case to the data-driven policy without
    pretending the sample rows are real market data.
    """
    if await is_demo_reference_ready(session):
        return "reference_based"
    snapshot = await build_snapshot(session, key, policy)
    return "reference_based" if snapshot.is_ready else "bootstrap"
