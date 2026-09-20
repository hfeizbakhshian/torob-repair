"""Demo sign-in, sessions and the permission rules.

Role and ownership are always read from the server-side session. A `role` field in a
request body can never grant anything.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.config import settings
from app.domain.errors import forbidden, not_found
from app.models import Session, SpecialistProfile, User
from app.models.enums import Role

TOKEN_BYTES = 32


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    """The authenticated actor for one request."""

    user_id: uuid.UUID
    role: Role
    display_name: str
    session_id: uuid.UUID

    @property
    def is_customer(self) -> bool:
        return self.role is Role.customer

    @property
    def is_specialist(self) -> bool:
        return self.role is Role.specialist

    @property
    def is_support(self) -> bool:
        return self.role is Role.support


async def list_demo_accounts(session: AsyncSession) -> list[User]:
    """The sign-in picker. Available only while APP_MODE=demo."""
    if not settings.is_demo:
        raise forbidden("ورود نمایشی فقط در حالت دمو فعال است.")
    rows = await session.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.role, User.display_name)
    )
    return list(rows.scalars())


async def sign_in(session: AsyncSession, login_key: str) -> tuple[Session, str]:
    """Turn a seeded account id into a fresh random session. Returns the raw token once."""
    if not settings.is_demo:
        raise forbidden("ورود نمایشی فقط در حالت دمو فعال است.")

    user = (
        await session.execute(
            select(User).where(User.login_key == login_key, User.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if user is None:
        raise not_found("چنین حساب نمونه‌ای وجود ندارد.")

    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    record = Session(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=now() + timedelta(hours=settings.session_ttl_hours),
    )
    session.add(record)
    await session.flush()
    return record, raw_token


async def resolve(session: AsyncSession, raw_token: str | None) -> Principal | None:
    """Look up a live session, or `None` when there is no usable one."""
    if not raw_token:
        return None
    row = (
        await session.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == hash_token(raw_token))
        )
    ).first()
    if row is None:
        return None

    record, user = row
    if record.revoked_at is not None or record.expires_at <= now() or not user.is_active:
        return None
    return Principal(
        user_id=user.id,
        role=user.role,
        display_name=user.display_name,
        session_id=record.id,
    )


async def sign_out(session: AsyncSession, session_id: uuid.UUID) -> None:
    record = await session.get(Session, session_id)
    if record is not None and record.revoked_at is None:
        record.revoked_at = now()


async def specialist_profile(
    session: AsyncSession, user_id: uuid.UUID
) -> SpecialistProfile:
    profile = (
        await session.execute(
            select(SpecialistProfile).where(SpecialistProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise forbidden("برای این اقدام باید حساب متخصص داشته باشید.")
    return profile


def require_role(principal: Principal | None, *roles: Role) -> Principal:
    if principal is None:
        raise forbidden("برای این اقدام باید وارد حساب شوید.")
    if principal.role not in roles:
        raise forbidden("این اقدام برای نقش شما مجاز نیست.")
    return principal
