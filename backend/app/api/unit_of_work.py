"""One database session per request, committed *before* the response is returned.

Leaving the commit to a `yield` dependency is not enough: FastAPI closes those after the
response has already been produced, so a client that acts immediately — signing in and
navigating straight away — can send its next request before the first one's write is
visible. That looks exactly like a sign-in that does not stick.

Owning the session in middleware puts the commit inside the request, ahead of the
response. The middleware sits outside the exception handlers, so by the time `call_next`
returns, a DomainError has already become a 4xx response; anything at or above 400 rolls
back, everything below commits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import settings
from app.db import get_sessionmaker

STATE_KEY = "db_session"


class UnitOfWorkMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        async with get_sessionmaker()() as session:
            setattr(request.state, STATE_KEY, session)
            if settings.is_demo:
                # The worker may also be moving the demo clock; pick up its position.
                from app.domain.demo_control import refresh_clock_offset

                await refresh_clock_offset(session)
            try:
                response = await call_next(request)
            except Exception:
                await session.rollback()
                raise
            if response.status_code >= 400:
                await session.rollback()
            else:
                await session.commit()
            return response


def session_from(request: Request) -> AsyncSession:
    session: AsyncSession | None = getattr(request.state, STATE_KEY, None)
    if session is None:  # pragma: no cover - the middleware is always installed
        raise RuntimeError("unit-of-work middleware is not installed")
    return session
