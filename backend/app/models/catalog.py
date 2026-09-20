"""Service templates, demo coverage and the immutable policy versions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class ServiceTemplate(Base, TimestampMixin):
    """One repair operation: its intake questions, reference labour time and part needs."""

    __tablename__ = "service_templates"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    title_fa: Mapped[str] = mapped_column(String(160), nullable=False)
    summary_fa: Mapped[str] = mapped_column(String(400), nullable=False)
    allowed_offer_types: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    intake_questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    required_facts: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    reference_labor_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_labor_source: Mapped[str] = mapped_column(
        String(160), default="نمونهٔ دمو", nullable=False
    )
    part_specs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Coverage(Base, TimestampMixin):
    """City × vehicle × service combinations the demo actually accepts."""

    __tablename__ = "coverage"

    id: Mapped[uuid.UUID] = uuid_pk()
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    vehicle_code: Mapped[str] = mapped_column(String(80), nullable=False)
    service_code: Mapped[str] = mapped_column(String(60), nullable=False)
    visit_mode: Mapped[str] = mapped_column(String(30), default="shop", nullable=False)
    is_supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "city", "vehicle_code", "service_code", "visit_mode", name="uq_coverage_combination"
        ),
    )


class PolicyVersion(Base, TimestampMixin):
    """An immutable snapshot of every tunable threshold, quota and deadline.

    Records that depend on policy store the version id they were decided under, so a later
    calibration never rewrites an earlier decision.
    """

    __tablename__ = "policy_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
