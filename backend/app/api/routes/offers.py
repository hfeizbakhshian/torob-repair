"""Submitting offers, the specialist dashboard and the customer's comparison view."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import PolicyDep, SessionDep, SpecialistDep, UserDep
from app.api.serializers import offer_out, request_out
from app.domain import offers as service
from app.domain.auth import specialist_profile
from app.domain.comparison import ComparableOffer, SortKey, group_by_scenario, sort_offers
from app.domain.errors import forbidden, not_found
from app.domain.money import LineItem, Totals, compute_totals
from app.models import Offer, OfferVersion, Request
from app.models.enums import Role
from app.schemas.api import (
    OfferComparisonOut,
    OfferOut,
    RequestOut,
    ScenarioGroupOut,
    SubmitOfferInput,
)

router = APIRouter(tags=["offers"])

UNPRICED_NOTE = (
    "این پیشنهادها مبلغ کل معلوم ندارند و وارد رتبه‌بندی قیمتی نمی‌شوند؛ "
    "شرایط آن‌ها را جداگانه ببینید."
)


@router.get("/specialist/requests", response_model=list[RequestOut])
async def relevant_requests(
    session: SessionDep, specialist: SpecialistDep
) -> list[RequestOut]:
    """Only the technical summary, district and time window — never a rival's offer."""
    profile = await specialist_profile(session, specialist.user_id)
    pairs = await service.list_relevant_requests(session, profile)
    return [await request_out(session, request) for request, _ in pairs]


@router.get("/specialist/offers", response_model=list[OfferOut])
async def my_offers(
    session: SessionDep, specialist: SpecialistDep, policy: PolicyDep
) -> list[OfferOut]:
    rows = (
        await session.execute(
            select(Offer, OfferVersion)
            .join(OfferVersion, OfferVersion.id == Offer.current_version_id)
            .where(Offer.specialist_id == specialist.user_id)
            .order_by(Offer.created_at.desc())
        )
    ).all()
    result: list[OfferOut] = []
    for offer, version in rows:
        request = await session.get(Request, offer.request_id)
        if request is None:
            continue
        result.append(await offer_out(session, offer, version, request, policy))
    return result


@router.post("/requests/{request_id}/offers", response_model=OfferOut, status_code=201)
async def submit_offer(
    request_id: uuid.UUID,
    payload: SubmitOfferInput,
    session: SessionDep,
    specialist: SpecialistDep,
    policy: PolicyDep,
) -> OfferOut:
    """Price is free. Only structural defects block submission."""
    profile = await specialist_profile(session, specialist.user_id)
    version = await service.submit_offer(
        session,
        request_id=request_id,
        specialist_id=specialist.user_id,
        profile=profile,
        offer_type=payload.offer_type,
        lines=list(payload.lines),
        scenarios=[
            scenario.model_dump(mode="json", by_alias=True) for scenario in payload.scenarios
        ],
        scheduled_at=payload.scheduled_at,
        valid_until=payload.valid_until,
        estimated_minutes=payload.estimated_minutes,
        warranty_note=payload.warranty_note,
        conditions_note=payload.conditions_note,
        diagnostic_scope=payload.diagnostic_scope,
        diagnostic_fee_toman=payload.diagnostic_fee_toman,
        diagnostic_fee_credited=payload.diagnostic_fee_credited,
        policy=policy,
        expected_revision=payload.expected_revision,
    )
    offer = await session.get(Offer, version.offer_id)
    request = await session.get(Request, request_id)
    if offer is None or request is None:
        raise not_found()
    return await offer_out(session, offer, version, request, policy)


@router.post("/offers/{offer_id}/withdraw", response_model=OfferOut)
async def withdraw_offer(
    offer_id: uuid.UUID,
    session: SessionDep,
    specialist: SpecialistDep,
    policy: PolicyDep,
    expected_revision: int | None = Query(default=None),
) -> OfferOut:
    offer = await service.withdraw_offer(
        session,
        offer_id=offer_id,
        specialist_id=specialist.user_id,
        expected_revision=expected_revision,
    )
    version = await session.get(OfferVersion, offer.current_version_id)
    request = await session.get(Request, offer.request_id)
    if version is None or request is None:
        raise not_found()
    return await offer_out(session, offer, version, request, policy)


@router.get("/requests/{request_id}/offers", response_model=OfferComparisonOut)
async def compare_offers(
    request_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    policy: PolicyDep,
    sort: Literal["price", "time", "score"] = Query(default="price"),
) -> OfferComparisonOut:
    """The customer's comparison, grouped per shared scenario and ordered deterministically."""
    request = await session.get(Request, request_id)
    if request is None:
        raise not_found("این درخواست پیدا نشد.")
    if user.role is not Role.support and request.customer_id != user.user_id:
        raise forbidden("مقایسهٔ پیشنهادها فقط برای مشتری همین پرونده است.")

    pairs = await service.list_offers(session, request_id)
    rendered = {
        str(version.id): await offer_out(session, offer, version, request, policy)
        for offer, version in pairs
    }

    comparables: list[ComparableOffer] = []
    scenario_totals: dict[tuple[str, str | None], Totals] = {}
    for offer, version in pairs:
        rendered_offer = rendered[str(version.id)]
        scenarios = version.scenarios or [{}]
        lines = [LineItem.model_validate(line) for line in version.lines]
        for scenario in scenarios:
            code = scenario.get("code") if isinstance(scenario, dict) else None
            # What this scenario costs is the sum of its own lines. A declared figure that
            # the items do not add up to must not be what the ranking believes.
            totals = compute_totals(lines, scenario_code=code) if lines else None
            scenario_totals[(str(version.id), code)] = totals or Totals()
            total = (
                totals.total_toman
                if totals is not None
                else (
                    scenario.get("totalToman")
                    if isinstance(scenario, dict) and scenario.get("code")
                    else version.total_toman
                )
            )
            comparables.append(
                ComparableOffer(
                    offer_id=str(offer.id),
                    offer_version_id=str(version.id),
                    scenario_code=code,
                    total_toman=total,
                    scheduled_at=version.scheduled_at,
                    score=rendered_offer.specialist.score,
                )
            )

    groups: list[ScenarioGroupOut] = []
    for code, items in group_by_scenario(comparables).items():
        ordered = sort_offers(items, SortKey(sort))
        seen: set[str] = set()
        offers: list[OfferOut] = []
        for item in ordered:
            if item.offer_version_id in seen:
                continue
            seen.add(item.offer_version_id)
            totals = scenario_totals.get((item.offer_version_id, code))
            offers.append(
                rendered[item.offer_version_id].model_copy(
                    update={
                        "scenario_total_toman": totals.total_toman if totals else None,
                        "scenario_specialist_payable_toman": (
                            totals.specialist_payable_toman if totals else None
                        ),
                    }
                )
                if code is not None
                else rendered[item.offer_version_id]
            )
        unpriced = all(item.total_toman is None for item in items)
        # Specialists name their scenarios in Persian; the code is only the join key.
        titled = next(
            (
                str(scenario["title"])
                for offer in offers
                for scenario in offer.version.scenarios
                if scenario.get("code") == code and scenario.get("title")
            ),
            None,
        )
        groups.append(
            ScenarioGroupOut(
                scenario_code=code,
                title=titled or code or "پیشنهادهای بدون سناریو",
                offers=offers,
                note=UNPRICED_NOTE if unpriced else None,
            )
        )

    groups.sort(key=lambda group: (group.scenario_code is None, group.scenario_code or ""))
    return OfferComparisonOut(sort_key=sort, scenario_groups=groups)


def _line_items(raw: list[dict[str, object]]) -> list[LineItem]:
    return [LineItem.model_validate(item) for item in raw]
