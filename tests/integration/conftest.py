"""Database fixtures.

These are the only tests that touch a real database. They are skipped when one
is not reachable, so `pytest` still works with no container running — the same
principle as the live API tests (N-14): the default suite must never require
infrastructure.

Isolation is by transaction rollback rather than truncation. Each test runs
inside a transaction that is rolled back afterwards, so tests cannot see each
other's rows and nothing needs cleaning up. It is also much faster than
recreating a schema per test.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.getenv(
    "AMOS_TEST_DATABASE_URL",
    "postgresql+asyncpg://amos:amos@localhost:5432/amos",
)


async def _database_reachable(attempts: int = 5, delay: float = 1.0) -> bool:
    """Can we actually run a query?

    Opening a connection is not the same as the server being ready — a Postgres
    container that has just started accepts connections while still
    initialising, so a bare `connect()` can succeed and the next statement fail.
    This ran a query and still saw one flaky failure immediately after
    `podman start`, so it retries briefly rather than reporting a cold container
    as "no database".
    """
    for attempt in range(attempts):
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(sa_text("SELECT 1"))
            return True
        except Exception:
            if attempt == attempts - 1:
                return False
            await asyncio.sleep(delay)
        finally:
            await engine.dispose()
    return False


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[object]:
    """A fresh engine per test.

    Deliberately function-scoped. asyncpg connections are bound to the event
    loop that created them, and pytest-asyncio gives each test its own loop — a
    session-scoped engine therefore hands out connections belonging to a dead
    loop ("attached to a different loop"). NullPool keeps nothing between tests.
    """
    if not await _database_reachable():
        pytest.skip(
            "No database at "
            f"{TEST_DATABASE_URL.split('@')[-1]} — start it with `podman-compose up -d`"
        )
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: object) -> AsyncIterator[AsyncSession]:
    """A session whose work is always rolled back.

    The session is bound to an outer transaction that this fixture owns. The
    code under test may commit — that commit lands inside the outer transaction,
    which is then rolled back, so the test's writes are visible to itself and to
    nothing else.
    """
    connection = await db_engine.connect()  # type: ignore[attr-defined]
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def db_factory(db_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A real session factory, for testing RunService end to end.

    Rows written here genuinely commit, so the test cleans up after itself.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)  # type: ignore[arg-type]
    yield factory
