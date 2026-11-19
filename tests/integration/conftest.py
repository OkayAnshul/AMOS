"""Database fixtures.

These are the only tests that touch a real database. They are skipped when one
is not reachable, so `pytest` still works with no container running — the same
principle as the live API tests (N-14): the default suite must never require
infrastructure.

Isolation is by transaction rollback rather than truncation. Each test runs
inside a transaction that is rolled back afterwards, so tests cannot see each
other's rows and nothing needs cleaning up. It is also much faster than
recreating a schema per test.

**The tests have their own database, `amos_test`** (V1.5), created and migrated
on first use. They used to share `amos` with the demo, and a rolled-back insert
is not free: it leaves a dead entry in every index, which is how an empty
`chunks` table came to carry a 78 MB vector index. Commit-for-real tests
(`db_factory`) leaked rows into it as well.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import make_url
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.getenv(
    "AMOS_TEST_DATABASE_URL",
    "postgresql+asyncpg://amos:amos@localhost:5432/amos_test",
)
#: The server's maintenance database: reachable before the test database exists.
_ADMIN_URL = make_url(TEST_DATABASE_URL).set(database="postgres")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_prepared = False


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
        engine = create_async_engine(_ADMIN_URL, poolclass=NullPool)
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


async def _prepare_test_database() -> None:
    """Create the test database if missing and migrate it to head, once per run.

    Migrating through `alembic upgrade head` rather than `create_all` keeps the
    schema-drift test honest: the tests run against what the migrations build.
    """
    global _prepared
    if _prepared:
        return
    name = make_url(TEST_DATABASE_URL).database
    admin = create_async_engine(_ADMIN_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                sa_text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                await connection.execute(sa_text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=_REPO_ROOT,
        env={**os.environ, "AMOS_DATABASE_URL": TEST_DATABASE_URL},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"migrating {name} failed:\n{output.decode()}")
    _prepared = True


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
    await _prepare_test_database()
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


@pytest_asyncio.fixture
async def actor(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """A user owning everything a test creates (V1.4).

    Isolation is now a property of every query, so a test without an owner
    cannot construct a repository at all — which is the point of ADR-013's
    choice to take the actor at construction rather than per method.
    """
    from amos.auth import create_user

    created, _key = await create_user(db_session, f"test-{uuid.uuid4().hex[:8]}")
    return created


@pytest_asyncio.fixture
async def other_actor(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """A second user, for the tests that matter most: the ones asserting one
    user cannot see another's data."""
    from amos.auth import create_user

    created, _key = await create_user(db_session, f"other-{uuid.uuid4().hex[:8]}")
    return created


@pytest_asyncio.fixture
async def factory_actor(db_factory: async_sessionmaker[AsyncSession]):  # type: ignore[no-untyped-def]
    """An actor for the committing `db_factory` tests, removed afterwards.

    Carries the plaintext key as `.api_key`, because a test that drives the HTTP
    API needs to *present* it — and the key is unrecoverable after creation by
    design, so the fixture is the only place it exists.
    """
    from sqlalchemy import delete

    from amos.auth import create_user
    from amos.database.engine import session_scope
    from amos.database.models import User

    async with session_scope(db_factory) as session:
        created, key = await create_user(session, f"worker-{uuid.uuid4().hex[:8]}")
    object.__setattr__(created, "api_key", key)
    try:
        yield created
    finally:
        async with session_scope(db_factory) as session:
            await session.execute(delete(User).where(User.id == created.id))


@pytest.fixture
def auth(factory_actor):  # type: ignore[no-untyped-def]
    """Headers that authenticate as `factory_actor`."""
    return {"Authorization": f"Bearer {factory_actor.api_key}"}
