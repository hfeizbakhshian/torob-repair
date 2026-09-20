"""Dispute, binding adjudication, settlement, market reference and performance score."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    RevisionMixin,
    TimestampMixin,
    enum_type,
    money_column,
    uuid_pk,
)
from app.models.enums import (
    DisputeStatus,
    EvaluationStatus,
    Party,
    ReferenceQuality,
    SettlementSource,
)


class Dispute(Base, TimestampMixin, RevisionMixin):
    """One dispute process per case, attached to a specific accepted collaboration."""

    __tablename__ = "disputes"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("selections.id"), nullable=False, unique=True
    )
    opened_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[DisputeStatus] = mapped_column(
        enum_type(DisputeStatus, name="dispute_status"),
        default=DisputeStatus.dispute_open,
        nullable=False,
        index=True,
    )
    claim_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    statement_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    customer_closed_statements_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    specialist_closed_statements_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_rounds_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    support_evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    """Supplementary material support attached while the ruling waited for evidence.
    Support completes the record; it never issues or rewrites the ruling."""
    budget_extensions_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dispute_snapshots.id", use_alter=True, name="fk_disputes_current_snapshot"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("request_id", name="uq_disputes_request"),)


class DisputeStatement(Base, TimestampMixin):
    """One party's structured statement bundle, versioned until the input is locked."""

    __tablename__ = "dispute_statements"

    id: Mapped[uuid.UUID] = uuid_pk()
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    party: Mapped[Party] = mapped_column(enum_type(Party, name="party"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    item_positions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "dispute_id", "party", "version_number", name="uq_dispute_statements_version"
        ),
    )


class DisputeSnapshot(Base, TimestampMixin):
    """The frozen input the model is allowed to see. A later edit expires it."""

    __tablename__ = "dispute_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    disputed_item_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    undisputed_item_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("dispute_id", "round_number", name="uq_dispute_snapshots_round"),
    )


class DisputeResolutionProposal(Base, TimestampMixin):
    """A voluntary settlement offer. Only two approvals of the same version apply it."""

    __tablename__ = "dispute_resolution_proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    proposed_by: Mapped[Party] = mapped_column(enum_type(Party, name="party"), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    total_toman: Mapped[int | None] = money_column()
    specialist_payable_toman: Mapped[int | None] = money_column()
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    specialist_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "dispute_id", "version_number", name="uq_dispute_resolution_proposals_version"
        ),
    )


class DisputeDecision(Base, TimestampMixin):
    """The AI ruling. Exactly one applied decision per dispute."""

    __tablename__ = "dispute_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dispute_snapshots.id"), nullable=False
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    """`decided` or `needs_evidence`; only `decided` may be applied."""
    line_decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_dispute_decisions_one_applied",
            "dispute_id",
            unique=True,
            postgresql_where=text("applied_at is not null"),
        ),
    )


class Settlement(Base, TimestampMixin):
    """The binding outcome: what is owed and where the number came from.

    In the MVP applying a settlement means recording and notifying it. No money moves.
    """

    __tablename__ = "settlements"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("selections.id"), nullable=False)
    dispute_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("disputes.id"), nullable=True)
    source: Mapped[SettlementSource] = mapped_column(
        enum_type(SettlementSource, name="settlement_source"), nullable=False
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dispute_decisions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dispute_resolution_proposals.id"), nullable=True
    )
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    total_toman: Mapped[int] = mapped_column(nullable=False)
    specialist_payable_toman: Mapped[int] = mapped_column(nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("selection_id", name="uq_settlements_selection"),)


class ReferenceCase(Base, TimestampMixin):
    """A finished repair that may back the market reference, with its quality level."""

    __tablename__ = "reference_cases"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requests.id", ondelete="SET NULL"), nullable=True
    )
    group_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    vehicle_code: Mapped[str] = mapped_column(String(80), nullable=False)
    service_code: Mapped[str] = mapped_column(String(60), nullable=False)
    scenario_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    part_spec: Mapped[str | None] = mapped_column(String(160), nullable=True)
    part_condition: Mapped[str] = mapped_column(String(30), default="new", nullable=False)
    warranty_level: Mapped[str] = mapped_column(String(40), default="standard", nullable=False)
    buyer: Mapped[Party] = mapped_column(enum_type(Party, name="party"), nullable=False)
    total_toman: Mapped[int] = mapped_column(nullable=False)
    part_toman: Mapped[int | None] = money_column()
    labor_toman: Mapped[int | None] = money_column()
    quality: Mapped[ReferenceQuality] = mapped_column(
        enum_type(ReferenceQuality, name="reference_quality"), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consent_for_anonymous_use: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    support_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """Demo rows never make a real group ready; they only drive the demo policy flag."""
    excluded_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ReferenceSnapshot(Base, TimestampMixin):
    """Frozen statistics for a comparison group at the moment a review used them."""

    __tablename__ = "reference_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    group_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    is_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    specialist_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_specialist_share: Mapped[float | None] = mapped_column(nullable=True)
    mean_toman: Mapped[int | None] = money_column()
    min_toman: Mapped[int | None] = money_column()
    max_toman: Mapped[int | None] = money_column()
    window_days: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    case_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    not_ready_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)


class Evaluation(Base, TimestampMixin, RevisionMixin):
    """Cost-transparency judgement over the whole price chain of one case."""

    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[EvaluationStatus] = mapped_column(
        enum_type(EvaluationStatus, name="evaluation_status"),
        default=EvaluationStatus.queued,
        nullable=False,
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    change_verdicts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    has_unjustified_increase: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_invoice_overprice: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_assessable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id"), nullable=True
    )
    model_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id"), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    excluded_by_appeal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    appeals: Mapped[list[Appeal]] = relationship(back_populates="evaluation")


class Appeal(Base, TimestampMixin):
    """The specialist's single objection to one evaluation, with support's handling."""

    __tablename__ = "appeals"

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    evaluation: Mapped[Evaluation] = relationship(back_populates="appeals")

    __table_args__ = (UniqueConstraint("evaluation_id", name="uq_appeals_evaluation"),)
