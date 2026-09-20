"""Injectable clock.

Every deadline in the product is evaluated against this clock so that tests and the
demo control panel can move time forward without touching the system clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_offset = timedelta(0)


def now() -> datetime:
    """Current server time in UTC, including any demo/test offset."""
    return datetime.now(UTC) + _offset


def offset() -> timedelta:
    return _offset


def advance(delta: timedelta) -> datetime:
    """Move the demo clock forward. Never accepts a negative delta."""
    global _offset
    if delta < timedelta(0):
        raise ValueError("the demo clock only moves forward")
    _offset += delta
    return now()


def set_offset(delta: timedelta) -> None:
    global _offset
    _offset = delta


def reset() -> None:
    set_offset(timedelta(0))
