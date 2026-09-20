"""Declarative base, shared column types and mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.clock import now

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# Amounts are whole Toman held in BIGINT but must survive a JSON round-trip through the
# browser, so the domain refuses anything outside JavaScript's safe integer range.
MAX_SAFE_INT = 9_007_199_254_740_991


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )


def money_column(*, nullable: bool = True) -> Mapped[int | None]:
    """A Toman amount. NULL means *unknown*, which is never the same as zero."""
    return mapped_column(BigInteger, nullable=nullable)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now, nullable=False
    )


class RevisionMixin:
    """Optimistic concurrency for anything a client may send `expectedRevision` for."""

    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def bump(self) -> int:
        self.revision += 1
        return self.revision


def enum_type(enum_cls: type, *, name: str) -> Any:
    """A VARCHAR + CHECK column for a StrEnum, so adding a value stays a plain migration."""
    from sqlalchemy import Enum as SAEnum

    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=40,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )
