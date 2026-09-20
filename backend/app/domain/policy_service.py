"""Loading and pinning the active policy version."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PolicyVersion
from app.policy import DEFAULT_POLICY, Policy, policy_from_values


async def ensure_active_policy(session: AsyncSession) -> PolicyVersion:
    """Return the active policy row, creating it from the code defaults if absent."""
    row = (
        await session.execute(select(PolicyVersion).where(PolicyVersion.is_active.is_(True)))
    ).scalar_one_or_none()
    if row is not None:
        return row

    existing = (
        await session.execute(
            select(PolicyVersion).where(PolicyVersion.version == DEFAULT_POLICY.version)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.is_active = True
        return existing

    row = PolicyVersion(
        version=DEFAULT_POLICY.version,
        label=DEFAULT_POLICY.label,
        values=DEFAULT_POLICY.to_jsonable(),
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def load_policy(session: AsyncSession, policy_version_id: uuid.UUID | None) -> Policy:
    """Rebuild the exact policy a record was decided under.

    A record without a pinned version falls back to the active one; the values are read
    from the stored snapshot, never from the current code defaults.
    """
    if policy_version_id is None:
        row = await ensure_active_policy(session)
    else:
        found = await session.get(PolicyVersion, policy_version_id)
        row = found if found is not None else await ensure_active_policy(session)
    return policy_from_values(row.values)
