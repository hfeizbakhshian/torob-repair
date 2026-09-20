"""AI accounting, payments, the durable job queue and the audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_type, money_column, uuid_pk
from app.models.enums import (
    AiPurpose,
    AiRunStatus,
    AiStage,
    JobKind,
    JobStatus,
    PaymentStatus,
    RefundPolicyMode,
    RefundReason,
    RefundStatus,
)


class AiRun(Base, TimestampMixin):
    """One model attempt: what was reserved, what was actually consumed, what came back."""

    __tablename__ = "ai_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stage: Mapped[AiStage] = mapped_column(enum_type(AiStage, name="ai_stage"), nullable=False)
    purpose: Mapped[AiPurpose] = mapped_column(
        enum_type(AiPurpose, name="ai_purpose"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    input_version_key: Mapped[str] = mapped_column(String(120), nullable=False)
    """Identifies the exact input the answer belongs to; a late answer for an older key is
    kept as `stale` and never applied."""
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[AiRunStatus] = mapped_column(
        enum_type(AiRunStatus, name="ai_run_status"),
        default=AiRunStatus.reserved,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    counts_as_successful_turn: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    reserved_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Billable output, reasoning included. Reasoning detail is never added on top again."""
    usage_is_estimated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    estimated_cost_toman: Mapped[int | None] = money_column()
    input_rate_per_million: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_rate_per_million: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """Validated, presentable output only. Raw messages and internal reasoning are dropped."""
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetReservation(Base, TimestampMixin):
    """A committed hold on stage quota, taken before the network call is made."""

    __tablename__ = "budget_reservations"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stage: Mapped[AiStage] = mapped_column(enum_type(AiStage, name="ai_stage"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), default="case", nullable=False)
    """`case` is billed to the customer package; `operations` budgets are free to them."""
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unresolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """A timeout with unknown usage keeps the hold until the outcome is settled."""


class AiDailySpend(Base, TimestampMixin):
    """Installation-wide live spend per day, so the cap covers every case and retry."""

    __tablename__ = "ai_daily_spend"

    id: Mapped[uuid.UUID] = uuid_pk()
    day: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    spent_toman: Mapped[int] = mapped_column(default=0, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Payment(Base, TimestampMixin):
    """The sample registration fee. The result only ever comes from the gateway adapter."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount_toman: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        enum_type(PaymentStatus, name="payment_status"),
        default=PaymentStatus.created,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    gateway_reference: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id"), nullable=True
    )
    refund_policy_mode: Mapped[RefundPolicyMode] = mapped_column(
        enum_type(RefundPolicyMode, name="refund_policy_mode"), nullable=False
    )
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_payments_one_success_per_request",
            "request_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )


class Refund(Base, TimestampMixin):
    """Entitlement and transfer are two separate events; one full refund per payment."""

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = uuid_pk()
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount_toman: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        enum_type(RefundStatus, name="refund_status"),
        default=RefundStatus.requested,
        nullable=False,
        index=True,
    )
    reason: Mapped[RefundReason | None] = mapped_column(
        enum_type(RefundReason, name="refund_reason"), nullable=True
    )
    policy_mode: Mapped[RefundPolicyMode] = mapped_column(
        enum_type(RefundPolicyMode, name="refund_policy_mode"), nullable=False
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id"), nullable=True
    )
    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    reference_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reference_snapshots.id"), nullable=True
    )
    offers_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    transfer_reference: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    transfer_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transferred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_rounds_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reopen_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Job(Base, TimestampMixin):
    """The durable queue. Reserved with SELECT … FOR UPDATE SKIP LOCKED and a lease."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[JobKind] = mapped_column(enum_type(JobKind, name="job_kind"), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, name="job_status"), default=JobStatus.pending, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_jobs_pending_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key is not null and status in ('pending', 'leased')"),
        ),
    )


class AuditEvent(Base, TimestampMixin):
    """Product metrics and the accountability trail. Never holds secrets or private text."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class IdempotencyRecord(Base, TimestampMixin):
    """Replays an identical request; a different body under the same key is an error."""

    __tablename__ = "idempotency_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, default=200, nullable=False)

    __table_args__ = (UniqueConstraint("scope", "key", "user_id", name="uq_idempotency_scope_key"),)
