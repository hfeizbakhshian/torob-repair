"""Test fixtures.

Every test runs against the separate PostgreSQL test database named by
TEST_DATABASE_URL, never the demo data. Each test gets its own schema, created and dropped
for that run alone, so concurrent tests cannot see each other's rows and cleanup can never
reach beyond the run. The clock is injected, so no test ever waits for a real deadline, and
the AI provider is the mock by default, so the default suite makes no paid call.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import clock, db
from app.config import settings
from app.domain.policy_service import ensure_active_policy
from app.models import Base
from app.policy import DEFAULT_POLICY, Policy
from app.providers.payment import get_gateway


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine() -> AsyncIterator[object]:
    """A private schema per test. `DROP SCHEMA` only ever touches this run's own schema."""
    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(settings.sqlalchemy_url(testing=True), poolclass=None)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await admin.dispose()

    # The search path deliberately excludes `public`: with it, create_all(checkfirst=True)
    # would resolve the migrated tables in `public` and silently create nothing here,
    # leaving every test sharing one set of rows.
    scoped = create_async_engine(
        settings.sqlalchemy_url(testing=True),
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with scoped.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    db.configure(scoped)
    yield scoped
    await scoped.dispose()

    admin = create_async_engine(settings.sqlalchemy_url(testing=True), poolclass=None)
    async with admin.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()
    await db.dispose()


@pytest.fixture
async def session(engine: object) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)  # type: ignore[arg-type]
    async with maker() as session:
        await ensure_active_policy(session)
        await session.commit()
        yield session
        await session.commit()


@pytest.fixture(autouse=True)
def isolated_attachment_dir(tmp_path, monkeypatch):
    """Uploads go to a per-test temporary directory, never the demo file store."""
    monkeypatch.setattr(settings, "attachment_dir", tmp_path / "attachments")


@pytest.fixture
def policy() -> Policy:
    return DEFAULT_POLICY


@pytest.fixture(autouse=True)
def reset_clock_and_gateway():
    clock.reset()
    get_gateway().reset()
    yield
    clock.reset()
    get_gateway().reset()


@pytest.fixture
def advance():
    """Move the injected clock instead of sleeping."""

    def _advance(**kwargs: float) -> None:
        clock.advance(timedelta(**kwargs))

    return _advance


@pytest.fixture
async def client(engine: object) -> AsyncIterator[AsyncClient]:
    from app.api.deps import reset_ai_service
    from app.main import create_app

    reset_ai_service()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url=settings.web_origin, headers={"Origin": settings.web_origin}
    ) as http:
        yield http
