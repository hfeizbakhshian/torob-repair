"""Row-level locking helpers.

Selection, approval, budget reservation and payment all take a short transaction with the
relevant row locked. A lock is never held across a network call to the model or the
payment adapter.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import not_found, version_conflict
from app.models.base import Base


async def lock_row[T: Base](
    session: AsyncSession, model: type[T], row_id: uuid.UUID
) -> T:
    """Load one row `FOR UPDATE`, or raise the shared not-found error."""
    result = await session.execute(
        select(model).where(model.id == row_id).with_for_update()  # type: ignore[attr-defined]
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise not_found()
    return row


async def lock_row_optional[T: Base](
    session: AsyncSession, model: type[T], row_id: uuid.UUID
) -> T | None:
    result = await session.execute(
        select(model).where(model.id == row_id).with_for_update()  # type: ignore[attr-defined]
    )
    return result.scalar_one_or_none()


def check_revision(row: object, expected: int | None) -> None:
    """Enforce `expectedRevision` on every sensitive change."""
    current = getattr(row, "revision", None)
    if expected is None or current is None:
        return
    if expected != current:
        raise version_conflict(current)
