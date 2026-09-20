"""Deterministic offer ordering.

Every sort here is a *total* order: the final tiebreaker is the offer id, so comparing the
same two offers twice can never swap them, and the result does not depend on the order the
offers happened to arrive in. An unknown key always sorts last in its own position — an
unknown score is never silently read as zero.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import NamedTuple

from app.domain.money import ApiModel


class SortKey(StrEnum):
    price = "price"
    time = "time"
    score = "score"


class ComparableOffer(ApiModel):
    """The minimum an offer must expose to take part in the comparison."""

    offer_id: str
    offer_version_id: str
    scenario_code: str | None = None
    total_toman: int | None = None
    """`None` means the cost is not fully known; those offers land at the end of the group."""
    scheduled_at: datetime
    score: int | None = None
    """`None` means the specialist has no score with enough history behind it."""


class _Ranked(NamedTuple):
    unknown_primary: int
    primary: tuple[int, ...]
    rest: tuple[int | float | str, ...]


def _price_key(offer: ComparableOffer) -> tuple[int, int]:
    return (1, 0) if offer.total_toman is None else (0, offer.total_toman)


def _score_key(offer: ComparableOffer) -> tuple[int, int]:
    # Descending score: negate so the shared ascending sort still reads correctly.
    return (1, 0) if offer.score is None else (0, -offer.score)


def _time_key(offer: ComparableOffer) -> tuple[float]:
    return (offer.scheduled_at.timestamp(),)


def sort_offers(
    offers: list[ComparableOffer], key: SortKey = SortKey.price
) -> list[ComparableOffer]:
    """Order one comparable group.

    price: known total ascending → score (with history) descending, unscored after →
           earlier appointment → offer id.
    time:  appointment ascending → price → offer id.
    score: known score descending, unknown after → price → appointment → offer id.
    """

    def sort_key(offer: ComparableOffer) -> tuple[object, ...]:
        match key:
            case SortKey.price:
                return (*_price_key(offer), *_score_key(offer), *_time_key(offer), offer.offer_id)
            case SortKey.time:
                return (*_time_key(offer), *_price_key(offer), offer.offer_id)
            case SortKey.score:
                return (*_score_key(offer), *_price_key(offer), *_time_key(offer), offer.offer_id)

    return sorted(offers, key=sort_key)


def group_by_scenario(offers: list[ComparableOffer]) -> dict[str | None, list[ComparableOffer]]:
    """Split conditional offers into per-scenario groups.

    A conditional amount that names no scenario is not comparable and never enters a price
    ranking; it is returned under the `None` key so the UI can show it separately.
    """
    groups: dict[str | None, list[ComparableOffer]] = {}
    for offer in offers:
        groups.setdefault(offer.scenario_code, []).append(offer)
    return groups
