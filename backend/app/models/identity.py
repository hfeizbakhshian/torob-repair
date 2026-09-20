"""Accounts, sessions and the specialist profile that decides request relevance."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, RevisionMixin, TimestampMixin, enum_type, uuid_pk
from app.models.enums import Role


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    role: Mapped[Role] = mapped_column(enum_type(Role, name="role"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    login_key: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    """Stable handle used by the demo account picker. Never a real phone number."""
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    specialist_profile: Mapped[SpecialistProfile | None] = relationship(
        back_populates="user", uselist=False
    )


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    """SHA-256 of the cookie value. The raw token is never stored."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SpecialistProfile(Base, TimestampMixin, RevisionMixin):
    __tablename__ = "specialist_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    shop_name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    district: Mapped[str] = mapped_column(String(80), nullable=False)
    service_codes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    vehicle_codes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    visit_modes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    """Only `shop` is in scope for the demo; the column exists so coverage stays data driven."""
    hourly_rate_toman: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_archive_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """Archive specialists back the seeded market reference and never bid on live requests."""

    user: Mapped[User] = relationship(back_populates="specialist_profile")

    __table_args__ = (UniqueConstraint("user_id", name="uq_specialist_profiles_user_id"),)
