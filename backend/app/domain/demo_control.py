"""Demo-only controls: the injected clock and the scenario flags.

The clock offset is stored in the database so a server restart does not rewind the demo.
Nothing here touches the system clock or any file outside the project.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.config import settings
from app.domain.errors import forbidden
from app.models import DemoSetting

CLOCK_OFFSET_KEY = "clock_offset_seconds"
DEMO_REFERENCE_READY_KEY = "demo_reference_ready"
"""Independent flag. It only enables the data-driven refund scenario in the demo; sample
rows never make a real comparison group ready."""


def require_demo_mode() -> None:
    if not settings.is_demo:
        raise forbidden("کنترل دمو فقط در نصب نمایشی فعال است.")


async def _get(session: AsyncSession, key: str) -> dict[str, Any] | None:
    row = (
        await session.execute(select(DemoSetting).where(DemoSetting.key == key))
    ).scalar_one_or_none()
    return row.value if row else None


async def _set(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    row = (
        await session.execute(select(DemoSetting).where(DemoSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        session.add(DemoSetting(key=key, value=value))
    else:
        row.value = value
    # Autoflush is off, so a read later in this same request would not see the change.
    await session.flush()


async def load_clock_offset(session: AsyncSession) -> timedelta:
    stored = await _get(session, CLOCK_OFFSET_KEY)
    seconds = float(stored["seconds"]) if stored else 0.0
    offset = timedelta(seconds=seconds)
    clock.set_offset(offset)
    global _last_refresh
    _last_refresh = time.monotonic()
    return offset


_last_refresh = 0.0
REFRESH_INTERVAL_SECONDS = 2.0


async def refresh_clock_offset(session: AsyncSession, *, force: bool = False) -> None:
    """Re-read the demo clock from the database.

    The offset is shared state: the API process moves it, and the worker has to see the
    move or a deadline that has "passed" for the UI would never fire. Each process
    re-reads it at most every couple of seconds rather than on every single request.
    """
    if not settings.is_demo:
        return
    now = time.monotonic()
    if not force and (now - _last_refresh) < REFRESH_INTERVAL_SECONDS:
        return
    await load_clock_offset(session)


async def advance_clock(session: AsyncSession, delta: timedelta) -> timedelta:
    """Move the demo clock forward only. Support-only, demo-install only."""
    require_demo_mode()
    if delta < timedelta(0):
        raise forbidden("ساعت دمو فقط به جلو حرکت می‌کند.")
    new_offset = clock.offset() + delta
    clock.set_offset(new_offset)
    await _set(session, CLOCK_OFFSET_KEY, {"seconds": new_offset.total_seconds()})
    return new_offset


async def reset_clock(session: AsyncSession) -> None:
    require_demo_mode()
    clock.reset()
    await _set(session, CLOCK_OFFSET_KEY, {"seconds": 0.0})


async def is_demo_reference_ready(session: AsyncSession) -> bool:
    stored = await _get(session, DEMO_REFERENCE_READY_KEY)
    return bool(stored and stored.get("enabled"))


async def set_demo_reference_ready(session: AsyncSession, enabled: bool) -> None:
    require_demo_mode()
    await _set(session, DEMO_REFERENCE_READY_KEY, {"enabled": bool(enabled)})
