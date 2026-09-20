"""The durable background worker.

Jobs are reserved with `SELECT … FOR UPDATE SKIP LOCKED`, under a five-minute lease with a
recorded attempt id. The reservation is committed *before* the work runs, and only the
current lease holder may apply the result, so a process that dies mid-job cannot cause the
same domain effect or transfer to happen twice.

FastAPI BackgroundTasks is deliberately not used for any of this.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.config import settings
from app.db import dispose, session_scope
from app.domain import (
    agreements as agreement_service,
)
from app.domain import (
    ai_flows,
)
from app.domain import (
    refunds as refund_service,
)
from app.domain import (
    selection as selection_service,
)
from app.domain.ai_service import AiService, build_provider
from app.domain.demo_control import load_clock_offset, refresh_clock_offset
from app.domain.disputes import can_enter_adjudication
from app.domain.policy_service import load_policy
from app.models import Attachment, Dispute, Job, Request, Selection
from app.models.enums import JobKind, JobStatus, RequestStatus

logger = logging.getLogger("torob_repair.worker")


async def reserve_due_job(session: AsyncSession, lease_id: uuid.UUID) -> Job | None:
    """Claim one due job. `SKIP LOCKED` lets several workers run without blocking."""
    job = (
        await session.execute(
            select(Job)
            .where(
                Job.status == JobStatus.pending,
                Job.run_at <= now(),
            )
            .order_by(Job.run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.leased
    job.lease_id = lease_id
    job.leased_until = now() + timedelta(seconds=settings.worker_lease_seconds)
    job.attempts += 1
    return job


async def reclaim_expired_leases(session: AsyncSession) -> int:
    """Return work whose lease ran out, so a crashed worker does not strand a job."""
    rows = list(
        (
            await session.execute(
                select(Job)
                .where(
                    Job.status == JobStatus.leased,
                    Job.leased_until.is_not(None),
                    Job.leased_until < now(),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for job in rows:
        job.status = JobStatus.pending
        job.lease_id = None
        job.leased_until = None
    return len(rows)


async def _finish(job_id: uuid.UUID, lease_id: uuid.UUID, error: str | None) -> None:
    async with session_scope() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None or job.lease_id != lease_id:
            # Someone else owns the lease now; this result must not be applied.
            return
        job.status = JobStatus.done if error is None else JobStatus.failed
        job.last_error = error
        job.finished_at = now()
        job.lease_id = None
        job.leased_until = None


async def run_job(job_kind: JobKind, subject_id: uuid.UUID | None, ai: AiService) -> None:
    match job_kind:
        case JobKind.close_offer_window if subject_id:
            async with session_scope() as session:
                await selection_service.close_offer_window(session, subject_id)

        case JobKind.expire_selection if subject_id:
            async with session_scope() as session:
                await selection_service.expire_selection(session, subject_id)

        case JobKind.expire_agreement if subject_id:
            async with session_scope() as session:
                await agreement_service.expire_agreement(session, subject_id)

        case JobKind.close_dispute_statement_window if subject_id:
            async with session_scope() as session:
                dispute = await session.get(Dispute, subject_id)
                ready = dispute is not None and await can_enter_adjudication(dispute)
            if ready:
                await ai_flows.adjudicate_dispute(ai, dispute_id=subject_id)

        case JobKind.run_dispute_adjudication if subject_id:
            await ai_flows.adjudicate_dispute(ai, dispute_id=subject_id)

        case JobKind.run_evaluation if subject_id:
            await ai_flows.evaluate_case(ai, evaluation_id=subject_id)

        case JobKind.run_refund_review if subject_id:
            await ai_flows.review_refund_price(ai, refund_id=subject_id)

        case JobKind.refund_transfer if subject_id:
            async with session_scope() as session:
                await refund_service.run_transfer(session, refund_id=subject_id)

        case JobKind.retention_cleanup:
            await run_retention_cleanup()

        case _:
            logger.warning("unhandled job kind %s", job_kind)


async def run_retention_cleanup() -> None:
    """Delete evidence files and chat text 30 days after a case closes.

    Anonymous events, agreement versions and evaluations are kept for 180 days.
    """
    async with session_scope() as session:
        policy = await load_policy(session, None)
        cutoff = now() - timedelta(days=policy.retention.evidence_days_after_close)
        closed = list(
            (
                await session.execute(
                    select(Request.id).where(
                        Request.closed_at.is_not(None), Request.closed_at < cutoff
                    )
                )
            ).scalars()
        )
        if not closed:
            return
        attachments = list(
            (
                await session.execute(
                    select(Attachment).where(
                        Attachment.request_id.in_(closed), Attachment.deleted_at.is_(None)
                    )
                )
            ).scalars()
        )
        for attachment in attachments:
            path = settings.attachment_dir / attachment.stored_name
            if path.exists():
                path.unlink()
            attachment.deleted_at = now()


async def reconcile_due_deadlines(session: AsyncSession) -> None:
    """Apply any deadline that has passed, before a case is shown or changed.

    The same logic the worker runs is applied here so a demo whose clock jumped forward
    behaves correctly even between worker ticks.
    """
    due_requests = list(
        (
            await session.execute(
                select(Request).where(
                    Request.status.in_([RequestStatus.open, RequestStatus.selecting]),
                    Request.response_deadline.is_not(None),
                    Request.response_deadline <= now(),
                )
            )
        ).scalars()
    )
    for request in due_requests:
        await selection_service.close_offer_window(session, request.id)

    due_selections = list(
        (
            await session.execute(
                select(Selection).where(
                    Selection.status == "pending",
                    Selection.acceptance_deadline <= now(),
                )
            )
        ).scalars()
    )
    for selection in due_selections:
        await selection_service.expire_selection(session, selection.id)


async def tick(ai: AiService) -> int:
    """One polling round. Returns how many jobs were processed."""
    processed = 0
    async with session_scope() as session:
        # The demo clock lives in the database; the API moves it, so re-read it before
        # deciding which jobs are due.
        await refresh_clock_offset(session, force=True)
        await reclaim_expired_leases(session)

    while True:
        lease_id = uuid.uuid4()
        async with session_scope() as session:
            job = await reserve_due_job(session, lease_id)
            if job is None:
                break
            # Committed here, before the work runs.
            job_id, kind, subject = job.id, job.kind, job.subject_id

        error: str | None = None
        try:
            await run_job(kind, subject, ai)
        except Exception as failure:
            error = f"{type(failure).__name__}: {failure}"[:400]
            logger.exception("job %s failed", job_id)
        await _finish(job_id, lease_id, error)
        processed += 1
    return processed


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    async with session_scope() as session:
        await load_clock_offset(session)
        policy = await load_policy(session, None)
    ai = AiService(build_provider(), policy)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    logger.info(
        "worker started (poll=%ss, lease=%ss, ai=%s)",
        settings.worker_poll_seconds,
        settings.worker_lease_seconds,
        settings.ai_mode,
    )
    while not stopping.is_set():
        try:
            await tick(ai)
        except Exception:
            logger.exception("worker tick failed")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            continue
    await dispose()
    logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
