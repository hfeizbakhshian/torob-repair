"""Pure rules: line-item arithmetic and the offer ordering."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.comparison import ComparableOffer, SortKey, group_by_scenario, sort_offers
from app.domain.errors import DomainError
from app.domain.money import LineItem, compute_totals, labor_amount, require_unique_ids

BASE = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("minutes", "rate", "expected"),
    [
        (60, 400_000, 400_000),
        (45, 300_000, 225_000),
        (90, 333_333, 500_000),  # rounds half up to the nearest Toman
        (50, 111_111, 92_593),
        (0, 400_000, 0),
    ],
)
def test_labor_is_minutes_times_rate_over_sixty(minutes: int, rate: int, expected: int) -> None:
    assert labor_amount(minutes, rate) == expected


def test_customer_bought_part_counts_in_total_but_not_in_specialist_payable() -> None:
    lines = [
        LineItem(
            id="p1",
            type="part",
            title="کیت کلاچ",
            quantity=1,
            amount_toman=4_200_000,
            supplied_by="customer",
            paid_to="third_party",
        ),
        LineItem(
            id="l1",
            type="labor",
            title="اجرت",
            minutes=180,
            hourly_rate_toman=400_000,
            amount_toman=1_200_000,
        ),
    ]
    totals = compute_totals(lines)
    assert totals.total_toman == 5_400_000
    # The part the customer bought is never added to the specialist's side again.
    assert totals.specialist_payable_toman == 1_200_000


def test_unknown_amount_is_not_zero() -> None:
    lines = [
        LineItem(id="a", type="extra", title="هزینهٔ معلوم", amount_toman=100_000),
        LineItem(id="b", type="extra", title="هزینهٔ نامعلوم", amount_known=False),
    ]
    totals = compute_totals(lines)
    assert totals.total_toman is None
    assert totals.specialist_payable_toman is None
    assert totals.has_unknown_amount is True
    assert totals.unknown_line_ids == ["b"]


def test_shared_operation_is_counted_once() -> None:
    lines = [
        LineItem(
            id="l1",
            type="labor",
            title="بازکردن گیربکس",
            operation_code="gearbox_open",
            minutes=60,
            hourly_rate_toman=400_000,
            amount_toman=400_000,
        ),
        LineItem(
            id="l2",
            type="labor",
            title="بازکردن دوبارهٔ همان مجموعه",
            operation_code="gearbox_open",
            minutes=60,
            hourly_rate_toman=400_000,
            amount_toman=400_000,
        ),
    ]
    assert compute_totals(lines).total_toman == 400_000


def test_conditional_totals_are_per_scenario() -> None:
    lines = [
        LineItem(id="base", type="labor", title="بررسی", minutes=30,
                 hourly_rate_toman=400_000, amount_toman=200_000),
        LineItem(id="a", type="part", title="سناریوی الف", scenario_code="A",
                 quantity=1, amount_toman=1_000_000),
        LineItem(id="b", type="part", title="سناریوی ب", scenario_code="B",
                 quantity=1, amount_toman=3_000_000),
    ]
    assert compute_totals(lines, scenario_code="A").total_toman == 1_200_000
    assert compute_totals(lines, scenario_code="B").total_toman == 3_200_000


def test_negative_amount_is_rejected() -> None:
    with pytest.raises(ValueError):
        LineItem(id="x", type="extra", title="منفی", amount_toman=-1)


def test_duplicate_line_ids_are_rejected() -> None:
    lines = [
        LineItem(id="same", type="extra", title="یک", amount_toman=1),
        LineItem(id="same", type="extra", title="دو", amount_toman=2),
    ]
    with pytest.raises(DomainError):
        require_unique_ids(lines)


def _offers() -> list[ComparableOffer]:
    return [
        ComparableOffer(offer_id="c", offer_version_id="cv", total_toman=5_000_000,
                        scheduled_at=BASE + timedelta(days=1), score=None),
        ComparableOffer(offer_id="a", offer_version_id="av", total_toman=5_000_000,
                        scheduled_at=BASE + timedelta(days=2), score=90),
        ComparableOffer(offer_id="b", offer_version_id="bv", total_toman=None,
                        scheduled_at=BASE, score=100),
        ComparableOffer(offer_id="d", offer_version_id="dv", total_toman=4_000_000,
                        scheduled_at=BASE + timedelta(days=3), score=None),
    ]


def test_price_order_breaks_ties_by_score_then_time_then_id() -> None:
    order = [offer.offer_id for offer in sort_offers(_offers(), SortKey.price)]
    # d is cheapest; a and c tie on price so the scored one comes first; b has no known
    # total and lands at the end.
    assert order == ["d", "a", "c", "b"]


def test_unknown_score_is_not_treated_as_zero() -> None:
    order = [offer.offer_id for offer in sort_offers(_offers(), SortKey.score)]
    assert order[:2] == ["b", "a"]
    assert set(order[2:]) == {"c", "d"}


def test_order_is_independent_of_arrival_order() -> None:
    expected = [offer.offer_id for offer in sort_offers(_offers(), SortKey.price)]
    for _ in range(100):
        shuffled = _offers()
        random.shuffle(shuffled)
        assert [offer.offer_id for offer in sort_offers(shuffled, SortKey.price)] == expected


def test_repeating_a_comparison_never_swaps_two_offers() -> None:
    for key in SortKey:
        once = sort_offers(_offers(), key)
        twice = sort_offers(once, key)
        assert [offer.offer_id for offer in once] == [offer.offer_id for offer in twice]


def test_scenario_grouping_keeps_unscoped_amounts_separate() -> None:
    offers = [
        ComparableOffer(offer_id="a", offer_version_id="av", scenario_code="A",
                        total_toman=1_000_000, scheduled_at=BASE),
        ComparableOffer(offer_id="b", offer_version_id="bv", scenario_code=None,
                        total_toman=900_000, scheduled_at=BASE),
    ]
    groups = group_by_scenario(offers)
    assert set(groups) == {"A", None}
    assert [offer.offer_id for offer in groups["A"]] == ["a"]
