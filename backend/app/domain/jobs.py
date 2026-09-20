"""Scheduling work on the durable PostgreSQL queue."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job
from app.models.enums import JobKind, JobStatus


async def schedule(
    session: AsyncSession,
    kind: JobKind,
    run_at: datetime,
    *,
    subject_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> Job | None:
    """Queue a job.

    `dedupe_key` is enforced by a partial unique index over pending and leased rows, so a
    duplicate schedule is a no-op rather than a second run of the same effect.
    """
    if dedupe_key is not None:
        existing = (
            await session.execute(
                select(Job).where(
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_([JobStatus.pending, JobStatus.leased]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.run_at = min(existing.run_at, run_at)
            return existing

    job = Job(
        kind=kind,
        subject_id=subject_id,
        payload=payload or {},
        run_at=run_at,
        dedupe_key=dedupe_key,
    )
    session.add(job)
    await session.flush()
    return job


async def cancel_pending(
    session: AsyncSession, kind: JobKind, subject_id: uuid.UUID
) -> None:
    """Drop not-yet-leased work for a subject that no longer needs it."""
    rows = (
        await session.execute(
            select(Job).where(
                Job.kind == kind,
                Job.subject_id == subject_id,
                Job.status == JobStatus.pending,
            )
        )
    ).scalars()
    for job in rows:
        job.status = JobStatus.done
        job.last_error = "cancelled: no longer applicable"
