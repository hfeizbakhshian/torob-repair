"""The FastAPI application.

Everything is mounted under `/api`. The browser talks to the Next.js origin, which rewrites
`/api/...` to this service without applying any product rule of its own, so no open CORS
policy and no direct browser access to this port is needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import errors
from app.api.routes import (
    ai,
    auth,
    catalog,
    collaboration,
    demo,
    disputes,
    execution,
    offers,
    refunds,
    requests,
    support,
)
from app.config import settings
from app.db import dispose, session_scope
from app.domain.demo_control import load_clock_offset
from app.domain.policy_service import ensure_active_policy


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with session_scope() as session:
        await ensure_active_policy(session)
        # The demo clock offset is restored from the database so a restart does not
        # silently rewind a demo that was already moved forward.
        await load_clock_offset(session)
    yield
    await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ترب تعمیر — API",
        version="0.1.0",
        description=(
            "API نمایشی «ترب تعمیر». دادهٔ نمونه، پرداخت شبیه‌سازی‌شده و پاسخ AI نمایشی "
            "در این نصب صریحاً برچسب‌گذاری می‌شوند."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    errors.install(app)

    app.include_router(auth.router, prefix="/api")
    app.include_router(catalog.router, prefix="/api")
    app.include_router(requests.router, prefix="/api")
    app.include_router(offers.router, prefix="/api")
    app.include_router(collaboration.router, prefix="/api")
    app.include_router(execution.router, prefix="/api")
    app.include_router(disputes.router, prefix="/api")
    app.include_router(refunds.router, prefix="/api")
    app.include_router(ai.router, prefix="/api")
    app.include_router(support.router, prefix="/api")
    if settings.is_demo:
        app.include_router(demo.router, prefix="/api")

    @app.get("/api/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "appMode": settings.app_mode, "aiMode": settings.ai_mode}

    return app


app = create_app()
