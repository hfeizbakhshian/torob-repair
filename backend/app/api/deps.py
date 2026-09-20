"""Request-scoped dependencies: the session, the principal, and the CSRF origin check."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.domain import auth
from app.domain.ai_service import AiService, build_provider
from app.domain.auth import Principal
from app.domain.errors import DomainError, ErrorCode, forbidden
from app.domain.policy_service import load_policy
from app.models.enums import Role
from app.policy import Policy

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


SessionDep = Annotated[AsyncSession, Depends(db_session)]


async def check_origin(request: Request) -> None:
    """Reject a state-changing request whose Origin is not the configured web origin.

    Forwarded headers are only meaningful from the local trusted proxy; Next.js never
    becomes an independent source of session or role.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        # Same-origin form posts and server-side proxy calls may omit it; the session
        # cookie is SameSite=Lax, which already blocks the cross-site case.
        return
    if origin != settings.web_origin:
        raise DomainError(
            ErrorCode.FORBIDDEN, "منبع این درخواست مجاز نیست."
        )


async def current_principal(
    request: Request, session: SessionDep, _: Annotated[None, Depends(check_origin)]
) -> Principal | None:
    token = request.cookies.get(settings.session_cookie_name)
    return await auth.resolve(session, token)


PrincipalDep = Annotated[Principal | None, Depends(current_principal)]


async def require_user(principal: PrincipalDep) -> Principal:
    if principal is None:
        raise forbidden("برای این اقدام باید وارد حساب نمونه شوید.")
    return principal


UserDep = Annotated[Principal, Depends(require_user)]


async def require_customer(user: UserDep) -> Principal:
    return auth.require_role(user, Role.customer)


async def require_specialist(user: UserDep) -> Principal:
    return auth.require_role(user, Role.specialist)


async def require_support(user: UserDep) -> Principal:
    return auth.require_role(user, Role.support)


CustomerDep = Annotated[Principal, Depends(require_customer)]
SpecialistDep = Annotated[Principal, Depends(require_specialist)]
SupportDep = Annotated[Principal, Depends(require_support)]


async def active_policy(session: SessionDep) -> Policy:
    return await load_policy(session, None)


PolicyDep = Annotated[Policy, Depends(active_policy)]

_ai_service: AiService | None = None


async def ai_service(policy: PolicyDep) -> AiService:
    """One provider client for the process; each call's settings stay independent."""
    global _ai_service
    if _ai_service is None or _ai_service.policy.version != policy.version:
        _ai_service = AiService(build_provider(), policy)
    return _ai_service


AiDep = Annotated[AiService, Depends(ai_service)]


def reset_ai_service() -> None:
    global _ai_service
    _ai_service = None
