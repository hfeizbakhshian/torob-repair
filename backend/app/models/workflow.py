"""Request → offer → selection → agreement: the versioned spine of a case."""

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
    AgreementStatus,
    CloseReason,
    OfferStatus,
    OfferType,
    Party,
    RefundPolicyMode,
    RequestStatus,
    SelectionStatus,
)


def text_status_live() -> Any:
    """Partial-index predicate: an offer that can still be picked or is already picked."""
    return text("status in ('active', 'selected')")


def text_selection_open() -> Any:
    return text("status in ('pending', 'accepted')")


def text_agreement_active() -> Any:
    return text("status = 'active'")


class Request(Base, TimestampMixin, RevisionMixin):
    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[RequestStatus] = mapped_column(
        enum_type(RequestStatus, name="request_status"),
        default=RequestStatus.draft,
        nullable=False,
        index=True,
    )
    close_reason: Mapped[CloseReason | None] = mapped_column(
        enum_type(CloseReason, name="close_reason"), nullable=True
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("request_versions.id", use_alter=True, name="fk_requests_current_version"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """Hidden from the customer's own list. The row, its payments and its audit trail
    stay, because a settled package fee still has a refund path."""
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    offers_closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When new offers stopped being accepted: mutual acceptance or the deadline, whichever
    came first. A rejected selection reopens selection but never reopens this window."""
    visit_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    visit_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refund_policy_mode: Mapped[RefundPolicyMode] = mapped_column(
        enum_type(RefundPolicyMode, name="refund_policy_mode"),
        default=RefundPolicyMode.bootstrap,
        nullable=False,
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id"), nullable=True
    )
    specialist_replacements_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[RequestVersion]] = relationship(
        back_populates="request",
        foreign_keys="RequestVersion.request_id",
        order_by="RequestVersion.version_number",
    )


class RequestVersion(Base, TimestampMixin):
    """The scope an offer was made against. A material change needs a brand-new request."""

    __tablename__ = "request_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    district: Mapped[str] = mapped_column(String(80), nullable=False)
    vehicle_code: Mapped[str] = mapped_column(String(80), nullable=False)
    vehicle_details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    service_code: Mapped[str] = mapped_column(String(60), nullable=False)
    visit_mode: Mapped[str] = mapped_column(String(30), default="shop", nullable=False)
    symptoms: Mapped[str] = mapped_column(Text, nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    allowed_offer_types: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    summary_facts: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    summary_unknowns: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    summary_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    typo_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    """Wording fixes recorded as notes; they never invalidate the offers made on this version."""

    request: Mapped[Request] = relationship(back_populates="versions", foreign_keys=[request_id])

    __table_args__ = (
        UniqueConstraint("request_id", "version_number", name="uq_request_versions_number"),
    )


class Offer(Base, TimestampMixin, RevisionMixin):
    """One specialist's standing bid on a request, with its own version history."""

    __tablename__ = "offers"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[OfferStatus] = mapped_column(
        enum_type(OfferStatus, name="offer_status"), default=OfferStatus.active, nullable=False
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offer_versions.id", use_alter=True, name="fk_offers_current_version"),
        nullable=True,
    )

    versions: Mapped[list[OfferVersion]] = relationship(
        back_populates="offer",
        foreign_keys="OfferVersion.offer_id",
        order_by="OfferVersion.version_number",
    )

    __table_args__ = (
        Index(
            "uq_offers_one_live_per_specialist",
            "request_id",
            "specialist_id",
            unique=True,
            postgresql_where=text_status_live(),
        ),
    )


class OfferVersion(Base, TimestampMixin):
    __tablename__ = "offer_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("request_versions.id"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_type: Mapped[OfferType] = mapped_column(
        enum_type(OfferType, name="offer_type"), nullable=False
    )
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    scenarios: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    """Conditional offers compare per shared scenario; a bare amount never enters the ranking."""
    total_toman: Mapped[int | None] = money_column()
    specialist_payable_toman: Mapped[int | None] = money_column()
    warranty_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    conditions_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostic_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostic_fee_toman: Mapped[int | None] = money_column()
    diagnostic_fee_credited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_capability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set when the specialist refused the selection because they cannot do the work; the
    refund review re-checks this version's validity."""

    offer: Mapped[Offer] = relationship(back_populates="versions", foreign_keys=[offer_id])

    __table_args__ = (
        UniqueConstraint("offer_id", "version_number", name="uq_offer_versions_number"),
    )


class Selection(Base, TimestampMixin, RevisionMixin):
    __tablename__ = "selections"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    offer_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offer_versions.id"), nullable=False
    )
    status: Mapped[SelectionStatus] = mapped_column(
        enum_type(SelectionStatus, name="selection_status"),
        default=SelectionStatus.pending,
        nullable=False,
    )
    acceptance_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arbitration_terms_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Policy version of the binding-arbitration clause both sides saw before acceptance."""
    customer_accepted_terms_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    specialist_accepted_terms_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    work_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """The appointment currently in force; moving it needs both approvals on an agreement."""

    __table_args__ = (
        Index(
            "uq_selections_one_open_per_request",
            "request_id",
            unique=True,
            postgresql_where=text_selection_open(),
        ),
    )


class AgreementVersion(Base, TimestampMixin, RevisionMixin):
    """A proposed or active scope-and-cost contract. Two explicit approvals activate it."""

    __tablename__ = "agreement_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    selection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("selections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agreement_versions.id"), nullable=True
    )
    base_offer_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offer_versions.id"), nullable=True
    )
    """Only the first agreement carries this; the evaluation chain starts at that offer."""
    status: Mapped[AgreementStatus] = mapped_column(
        enum_type(AgreementStatus, name="agreement_status"),
        default=AgreementStatus.proposed,
        nullable=False,
    )
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    scenarios: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    total_toman: Mapped[int | None] = money_column()
    specialist_payable_toman: Mapped[int | None] = money_column()
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    warranty_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_terms: Mapped[str] = mapped_column(Text, nullable=False)
    arbitration_clause: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    proposed_by: Mapped[Party] = mapped_column(enum_type(Party, name="party"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approvals: Mapped[list[Approval]] = relationship(
        back_populates="agreement", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("selection_id", "version_number", name="uq_agreement_versions_number"),
        Index(
            "uq_agreement_one_active_per_selection",
            "selection_id",
            unique=True,
            postgresql_where=text_agreement_active(),
        ),
    )


class Approval(Base, TimestampMixin):
    """One party's approval of one exact agreement version. Silence is never approval."""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = uuid_pk()
    agreement_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    party: Mapped[Party] = mapped_column(enum_type(Party, name="party"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agreement: Mapped[AgreementVersion] = relationship(back_populates="approvals")

    __table_args__ = (
        UniqueConstraint("agreement_version_id", "party", name="uq_approvals_version_party"),
    )
