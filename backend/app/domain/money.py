"""Line items and money.

Amounts are whole Toman integers. `None` means *unknown* and is never treated as zero:
a total that contains an unknown line is itself unknown.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.errors import validation_error
from app.models.base import MAX_SAFE_INT
from app.models.enums import LineType, Party


def to_camel(value: str) -> str:
    head, *rest = value.split("_")
    return head + "".join(word.capitalize() for word in rest)


class ApiModel(BaseModel):
    """Base for everything on the wire: snake_case in Python, camelCase in JSON."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )


def check_amount(value: int | None, field: str) -> int | None:
    """Reject negatives and anything a browser could not hold as an exact integer."""
    if value is None:
        return None
    if value < 0:
        raise validation_error("مبلغ نمی‌تواند منفی باشد.", **{field: "مبلغ منفی مجاز نیست."})
    if value > MAX_SAFE_INT:
        raise validation_error(
            "مبلغ واردشده بیش از حد بزرگ است.", **{field: "مبلغ خارج از محدودهٔ مجاز است."}
        )
    return value


def labor_amount(minutes: int, hourly_rate_toman: int) -> int:
    """Agreed minutes × hourly rate ÷ 60, rounded half-up to the nearest Toman.

    Longer actual work never becomes a larger charge by itself; the agreed minutes are
    what this is computed from.
    """
    if minutes < 0 or hourly_rate_toman < 0:
        raise validation_error("زمان و نرخ اجرت نمی‌توانند منفی باشند.")
    exact = (Decimal(minutes) * Decimal(hourly_rate_toman)) / Decimal(60)
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class LineItem(ApiModel):
    """One row of an offer, an agreement, an expense report or a claim.

    `paid_to` is what separates the two totals: the whole service cost includes a part the
    customer bought themselves, while the amount payable to the specialist does not.
    """

    id: str = Field(min_length=1, max_length=60)
    type: LineType
    title: str = Field(min_length=1, max_length=200)
    spec: str | None = Field(default=None, max_length=400)
    operation_code: str | None = Field(default=None, max_length=60)
    """Shared identifier for one operation, so opening and closing the same assembly twice
    is not counted twice."""
    scenario_code: str | None = Field(default=None, max_length=60)
    quantity: int | None = Field(default=None, ge=0, le=1000)
    minutes: int | None = Field(default=None, ge=0, le=100_000)
    unit_rate_toman: int | None = Field(default=None, ge=0)
    hourly_rate_toman: int | None = Field(default=None, ge=0)
    supplied_by: Party = Party.specialist
    paid_to: Literal["specialist", "third_party"] = "specialist"
    amount_toman: int | None = Field(default=None, ge=0)
    amount_known: bool = True
    note: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _check(self) -> LineItem:
        if not self.amount_known and self.amount_toman is not None:
            raise ValueError("قلمی که مبلغش نامعلوم است نباید مبلغ داشته باشد.")
        if self.amount_known and self.amount_toman is None:
            raise ValueError("مبلغ این قلم باید مشخص باشد یا صریحاً نامعلوم اعلام شود.")
        if self.amount_toman is not None:
            check_amount(self.amount_toman, "amountToman")
        if self.type is LineType.labor and self.minutes is None:
            raise ValueError("ردیف اجرت باید زمان کار داشته باشد.")
        if self.type is LineType.part and self.quantity is None:
            raise ValueError("ردیف قطعه باید تعداد داشته باشد.")
        return self

    @property
    def counts_toward_specialist(self) -> bool:
        return self.paid_to == "specialist"


class Totals(ApiModel):
    """A pair of totals, each `None` when any contributing line is unknown."""

    total_toman: int | None = None
    specialist_payable_toman: int | None = None
    has_unknown_amount: bool = False
    unknown_line_ids: list[str] = Field(default_factory=list)


def compute_totals(lines: list[LineItem], *, scenario_code: str | None = None) -> Totals:
    """Sum a set of lines.

    When `scenario_code` is given, only lines belonging to that scenario (or to no
    scenario at all) are counted, which is how conditional offers are compared like for
    like. A line whose operation_code repeats is counted once.
    """
    total = 0
    payable = 0
    unknown: list[str] = []
    seen_operations: set[str] = set()

    for line in lines:
        if scenario_code is not None and line.scenario_code not in (None, scenario_code):
            continue
        if line.operation_code is not None:
            if line.operation_code in seen_operations:
                continue
            seen_operations.add(line.operation_code)
        if line.amount_toman is None:
            unknown.append(line.id)
            continue
        total += line.amount_toman
        if line.counts_toward_specialist:
            payable += line.amount_toman

    has_unknown = bool(unknown)
    return Totals(
        total_toman=None if has_unknown else total,
        specialist_payable_toman=None if has_unknown else payable,
        has_unknown_amount=has_unknown,
        unknown_line_ids=unknown,
    )


def parse_lines(raw: list[dict[str, Any]]) -> list[LineItem]:
    return [LineItem.model_validate(item) for item in raw]


def dump_lines(lines: list[LineItem]) -> list[dict[str, Any]]:
    return [line.model_dump(mode="json", by_alias=True) for line in lines]


def require_unique_ids(lines: list[LineItem]) -> None:
    seen: set[str] = set()
    for line in lines:
        if line.id in seen:
            raise validation_error(f"شناسهٔ قلم «{line.id}» تکراری است.")
        seen.add(line.id)
