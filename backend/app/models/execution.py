"""Work execution: expenses, receipts, attachments, parts and the Torob price check."""

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
    Numeric,
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
    PartSourceKind,
    Party,
    PriceCheckVerdict,
    ReceiptStatus,
)


class Expense(Base, TimestampMixin, RevisionMixin):
    """The specialist's expense report for one collaboration, versioned on every edit."""

    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = uuid_pk()
    selection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("selections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expense_versions.id", use_alter=True, name="fk_expenses_current_version"),
        nullable=True,
    )

    versions: Mapped[list[ExpenseVersion]] = relationship(
        back_populates="expense",
        foreign_keys="ExpenseVersion.expense_id",
        order_by="ExpenseVersion.version_number",
    )

    __table_args__ = (UniqueConstraint("selection_id", name="uq_expenses_selection"),)


class ExpenseVersion(Base, TimestampMixin):
    __tablename__ = "expense_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_toman: Mapped[int | None] = money_column()
    specialist_payable_toman: Mapped[int | None] = money_column()
    extracted_by_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    specialist_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The specialist must review AI-extracted lines before the customer ever sees them."""
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    expense: Mapped[Expense] = relationship(back_populates="versions", foreign_keys=[expense_id])

    __table_args__ = (
        UniqueConstraint("expense_id", "version_number", name="uq_expense_versions_number"),
    )


class ReceiptReview(Base, TimestampMixin):
    """The customer's explicit verdict on one exact expense version."""

    __tablename__ = "receipt_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    expense_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ReceiptStatus] = mapped_column(
        enum_type(ReceiptStatus, name="receipt_status"),
        default=ReceiptStatus.pending,
        nullable=False,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("expense_version_id", name="uq_receipt_reviews_expense_version"),
    )


class Attachment(Base, TimestampMixin):
    """A private evidence file. Stored outside the web root under a random name."""

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expense_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expense_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(60), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """Flagged when the same hash already exists on this request — never silently dropped."""
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Completion(Base, TimestampMixin, RevisionMixin):
    """The closing record: final invoice, who approved it and on what basis."""

    __tablename__ = "completions"

    id: Mapped[uuid.UUID] = uuid_pk()
    selection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("selections.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invoice_total_toman: Mapped[int | None] = money_column()
    invoice_specialist_payable_toman: Mapped[int | None] = money_column()
    based_on_expense_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expense_versions.id"), nullable=True
    )
    based_on_agreement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agreement_versions.id"), nullable=True
    )
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    customer_reported_mismatch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    satisfaction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Optional 1–5 customer rating, reported separately from the cost-transparency score."""
    satisfaction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index(
            "ck_completions_satisfaction_range",
            "satisfaction_score",
            postgresql_where=text("satisfaction_score is not null"),
        ),
    )


class PartOption(Base, TimestampMixin):
    """A manually recorded Torob product link. The server never fetches the URL."""

    __tablename__ = "part_options"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    part_title: Mapped[str] = mapped_column(String(200), nullable=False)
    part_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    condition: Mapped[str] = mapped_column(String(30), default="new", nullable=False)
    warranty_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    seller_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    price_toman: Mapped[int | None] = money_column()
    delivery_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_cost_toman: Mapped[int | None] = money_column()
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_kind: Mapped[PartSourceKind] = mapped_column(
        enum_type(PartSourceKind, name="part_source_kind"),
        default=PartSourceKind.manual,
        nullable=False,
    )
    compatibility_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    compatibility_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PartPriceSnapshot(Base, TimestampMixin):
    """One independent Torob seller price, as observed and vouched for by support."""

    __tablename__ = "part_price_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expense_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expense_versions.id"), nullable=True
    )
    line_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    seller_name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    part_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    condition: Mapped[str] = mapped_column(String(30), default="new", nullable=False)
    warranty_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_price_toman: Mapped[int] = mapped_column(nullable=False)
    delivery_cost_toman: Mapped[int | None] = money_column()
    payment_terms: Mapped[str | None] = mapped_column(String(160), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[PartSourceKind] = mapped_column(
        enum_type(PartSourceKind, name="part_source_kind"),
        default=PartSourceKind.manual,
        nullable=False,
    )
    verified_by_support_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PartPriceCheck(Base, TimestampMixin):
    """The versioned result of comparing one invoiced part line against Torob prices."""

    __tablename__ = "part_price_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expense_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_versions.id"), nullable=False
    )
    line_id: Mapped[str] = mapped_column(String(60), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    invoice_unit_price_toman: Mapped[int | None] = money_column()
    median_reference_toman: Mapped[int | None] = money_column()
    snapshot_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    ratio: Mapped[Any | None] = mapped_column(Numeric(10, 4), nullable=True)
    comparable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    equivalence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[PriceCheckVerdict] = mapped_column(
        enum_type(PriceCheckVerdict, name="price_check_verdict"), nullable=False
    )
    payer: Mapped[Party] = mapped_column(enum_type(Party, name="party"), nullable=False)
    """A part the customer bought themselves never produces a negative event."""
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id"), nullable=True
    )
    established_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set once the basis is settled — a customer-confirmed receipt or an adjudicated line."""
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "expense_version_id", "line_id", "version_number", name="uq_part_price_checks_line"
        ),
        Index(
            "uq_part_price_checks_one_event_per_line",
            "request_id",
            "line_id",
            unique=True,
            postgresql_where=text("established_at is not null and verdict = 'overpriced'"),
        ),
    )
