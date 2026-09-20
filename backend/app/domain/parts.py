"""Torob part links, recorded seller prices, and the invoice overprice check.

Everything here is entered by hand. The server never fetches a Torob URL, never claims
live stock, and never presents a recorded figure as a verified live price.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit
from app.domain.errors import forbidden, invalid_state, validation_error
from app.domain.money import LineItem
from app.models import (
    ExpenseVersion,
    PartOption,
    PartPriceCheck,
    PartPriceSnapshot,
    Request,
)
from app.models.enums import LineType, Party, PartSourceKind, PriceCheckVerdict
from app.policy import Policy
from app.repository.locking import lock_row

ALLOWED_HOSTS = {"torob.com", "www.torob.com"}
MANUAL_SOURCE_LABEL = "واردشده توسط کاربر؛ تأیید زنده نشده است"


def search_url(query: str) -> str:
    """The button that opens a Torob search. No scraping, no hidden API."""
    return f"https://torob.com/search/?query={quote(query, safe='')}"


def validate_product_url(raw_url: str) -> str:
    """Accept only HTTPS links whose host is exactly torob.com or www.torob.com."""
    parsed = urlparse(raw_url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise validation_error(
            "فقط لینک HTTPS از torob.com پذیرفته می‌شود.",
            productUrl="میزبان لینک باید دقیقاً torob.com یا www.torob.com باشد.",
        )
    return raw_url.strip()


async def record_part_option(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    part_title: str,
    part_number: str | None,
    brand: str | None,
    condition: str,
    warranty_note: str | None,
    product_url: str,
    seller_name: str | None,
    price_toman: int | None,
    delivery_note: str | None,
    delivery_cost_toman: int | None,
) -> PartOption:
    option = PartOption(
        request_id=request_id,
        recorded_by=actor_id,
        part_title=part_title,
        part_number=part_number,
        brand=brand,
        condition=condition,
        warranty_note=warranty_note,
        product_url=validate_product_url(product_url),
        seller_name=seller_name,
        price_toman=price_toman,
        delivery_note=delivery_note,
        delivery_cost_toman=delivery_cost_toman,
        observed_at=now(),
        source_kind=PartSourceKind.manual,
    )
    session.add(option)
    await session.flush()
    await audit.record(
        session,
        "part_option_recorded",
        request_id=request_id,
        actor_id=actor_id,
        subject_id=option.id,
        data={"sourceKind": option.source_kind.value},
    )
    return option


async def confirm_compatibility(
    session: AsyncSession, *, option_id: uuid.UUID, specialist_id: uuid.UUID
) -> PartOption:
    """Choosing a part needs the specialist to vouch for its specification."""
    option = await lock_row(session, PartOption, option_id)
    option.compatibility_confirmed_by = specialist_id
    option.compatibility_confirmed_at = now()
    await audit.record(
        session,
        "part_compatibility_confirmed",
        request_id=option.request_id,
        actor_id=specialist_id,
        subject_id=option.id,
    )
    return option


async def record_price_snapshot(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    expense_version_id: uuid.UUID | None,
    line_id: str | None,
    product_url: str,
    seller_name: str,
    brand: str | None,
    part_number: str | None,
    condition: str,
    warranty_note: str | None,
    unit_price_toman: int,
    delivery_cost_toman: int | None,
    payment_terms: str | None,
    evidence_note: str | None,
    observed_at_override: object | None = None,
    source_kind: PartSourceKind = PartSourceKind.manual,
) -> PartPriceSnapshot:
    """One independent seller price. It only counts once support has vouched for it."""
    snapshot = PartPriceSnapshot(
        request_id=request_id,
        expense_version_id=expense_version_id,
        line_id=line_id,
        product_url=validate_product_url(product_url),
        seller_name=seller_name,
        brand=brand,
        part_number=part_number,
        condition=condition,
        warranty_note=warranty_note,
        unit_price_toman=unit_price_toman,
        delivery_cost_toman=delivery_cost_toman,
        payment_terms=payment_terms,
        observed_at=observed_at_override or now(),  # type: ignore[arg-type]
        evidence_note=evidence_note,
        source_kind=source_kind,
    )
    session.add(snapshot)
    await session.flush()
    await audit.record(
        session,
        "part_price_snapshot_recorded",
        request_id=request_id,
        actor_id=actor_id,
        subject_id=snapshot.id,
        data={"sellerName": seller_name, "unitPriceToman": unit_price_toman},
    )
    return snapshot


async def verify_price_snapshot(
    session: AsyncSession,
    *,
    snapshot_id: uuid.UUID,
    support_id: uuid.UUID,
    approve: bool,
    reason: str | None,
) -> PartPriceSnapshot:
    """Support vouches for a source. One party typing a number is never enough."""
    snapshot = await lock_row(session, PartPriceSnapshot, snapshot_id)
    if approve:
        snapshot.verified_by_support_at = now()
        snapshot.verified_by = support_id
        snapshot.rejected_reason = None
    else:
        if not reason:
            raise validation_error(
                "برای رد منبع باید دلیل ثبت شود.", reason="دلیل الزامی است."
            )
        snapshot.verified_by_support_at = None
        snapshot.rejected_reason = reason
    await audit.record(
        session,
        "part_price_source_reviewed",
        request_id=snapshot.request_id,
        actor_id=support_id,
        actor_role="support",
        subject_id=snapshot.id,
        reason=reason,
        data={"approved": approve},
    )
    return snapshot


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    pair = Decimal(ordered[middle - 1] + ordered[middle]) / Decimal(2)
    return int(pair.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _comparable(snapshot: PartPriceSnapshot, line: LineItem, purchase_at: object) -> bool:
    """Like-for-like: same part number, brand, condition and warranty basis."""
    if line.spec and snapshot.part_number and line.spec != snapshot.part_number:
        return False
    return snapshot.condition == "new"


async def evaluate_invoice_price(
    session: AsyncSession,
    *,
    expense_version_id: uuid.UUID,
    line: LineItem,
    policy: Policy,
    established: bool,
) -> PartPriceCheck:
    """Compare one invoiced part line against the recorded Torob prices.

    A part the customer bought themselves never produces a negative event. Without at
    least three verified, like-for-like, recently observed seller prices the result is
    "not assessable" rather than a penalty. A price *below* the reference has no negative
    effect. `established` says whether the basis is settled — a customer-confirmed receipt
    or an adjudicated line; until then the UI shows "needs review" and no score event is
    written.
    """
    version = await lock_row(session, ExpenseVersion, expense_version_id)
    from app.models import Expense, Selection  # local import avoids a cycle

    expense = await session.get(Expense, version.expense_id)
    selection = await session.get(Selection, expense.selection_id) if expense else None
    request_id = selection.request_id if selection else None
    if request_id is None:
        raise invalid_state("این نسخهٔ مخارج به پرونده‌ای متصل نیست.")

    payer = Party.customer if line.supplied_by is Party.customer else Party.specialist
    policy_row_id = (await session.get(Request, request_id)).policy_version_id

    def _store(
        verdict: PriceCheckVerdict,
        *,
        median: int | None,
        ratio: Decimal | None,
        comparable: bool | None,
        note: str | None,
        snapshot_ids: list[str],
    ) -> PartPriceCheck:
        version_number = 1
        check = PartPriceCheck(
            request_id=request_id,
            expense_version_id=expense_version_id,
            line_id=line.id,
            version_number=version_number,
            invoice_unit_price_toman=line.unit_rate_toman or line.amount_toman,
            median_reference_toman=median,
            snapshot_ids=snapshot_ids,
            ratio=ratio,
            comparable=comparable,
            equivalence_note=note,
            verdict=verdict,
            payer=payer,
            policy_version_id=policy_row_id,
            established_at=now() if (established and verdict is not
                                     PriceCheckVerdict.not_assessable) else None,
        )
        session.add(check)
        return check

    if line.type is not LineType.part or line.supplied_by is Party.customer:
        return _store(
            PriceCheckVerdict.not_assessable,
            median=None,
            ratio=None,
            comparable=None,
            note="قطعهٔ تأمین‌شده توسط مشتری در این بررسی امتیاز منفی نمی‌سازد.",
            snapshot_ids=[],
        )

    unit_price = line.unit_rate_toman
    if unit_price is None and line.amount_toman is not None and line.quantity:
        unit_price = line.amount_toman // line.quantity
    if unit_price is None:
        return _store(
            PriceCheckVerdict.not_assessable,
            median=None,
            ratio=None,
            comparable=None,
            note="قیمت واحد فاکتور معلوم نیست.",
            snapshot_ids=[],
        )

    max_age = timedelta(hours=policy.price.invoice_price_max_age_hours)
    candidates = list(
        (
            await session.execute(
                select(PartPriceSnapshot).where(
                    PartPriceSnapshot.request_id == request_id,
                    PartPriceSnapshot.line_id == line.id,
                    PartPriceSnapshot.verified_by_support_at.is_not(None),
                )
            )
        ).scalars()
    )
    usable = [
        snapshot
        for snapshot in candidates
        if _comparable(snapshot, line, None)
        and (version.created_at - snapshot.observed_at) <= max_age
        and snapshot.observed_at <= version.created_at
    ]
    sellers = {snapshot.seller_name for snapshot in usable}

    if len(sellers) < policy.price.invoice_min_reference_sellers:
        return _store(
            PriceCheckVerdict.not_assessable,
            median=None,
            ratio=None,
            comparable=False,
            note=(
                "دادهٔ کافی یا هم‌ارزی قابل اتکا برای مقایسه وجود ندارد؛ "
                "این مورد قابل بررسی نیست."
            ),
            snapshot_ids=[str(s.id) for s in usable],
        )

    median = _median([snapshot.unit_price_toman for snapshot in usable])
    ratio = Decimal(unit_price) / Decimal(median) if median else None
    overpriced = ratio is not None and ratio > policy.price.invoice_overprice_factor
    check = _store(
        PriceCheckVerdict.overpriced if overpriced else PriceCheckVerdict.within_range,
        median=median,
        ratio=ratio,
        comparable=True,
        note=None,
        snapshot_ids=[str(snapshot.id) for snapshot in usable],
    )
    await session.flush()

    if overpriced and check.established_at is not None:
        await audit.record(
            session,
            "invoice_overprice",
            request_id=request_id,
            actor_role="system",
            subject_id=check.id,
            data={
                "lineId": line.id,
                "invoiceUnitPriceToman": unit_price,
                "medianReferenceToman": median,
                "ratio": str(ratio),
                "snapshotIds": check.snapshot_ids,
            },
        )
    return check


async def list_price_checks(
    session: AsyncSession, request_id: uuid.UUID
) -> list[PartPriceCheck]:
    rows = await session.execute(
        select(PartPriceCheck)
        .where(PartPriceCheck.request_id == request_id, PartPriceCheck.is_superseded.is_(False))
        .order_by(PartPriceCheck.created_at)
    )
    return list(rows.scalars())


async def require_support(is_support: bool) -> None:
    if not is_support:
        raise forbidden("این اقدام فقط برای پشتیبان مجاز است.")
