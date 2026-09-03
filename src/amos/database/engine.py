"""Async engine and session lifecycle.

One engine per process, created at startup. The engine owns a connection pool,
so creating them per request would defeat pooling entirely — the expensive part
of a query becomes the TCP handshake and authentication.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from amos.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.require_database_url(),
        echo=settings.db_echo,
        pool_size=5,
        max_overflow=5,
        # Recycle before common idle timeouts so a pooled connection is not
        # handed out already dead.
        pool_recycle=1800,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,  # objects stay usable after commit
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A transaction that commits on success and rolls back on any exception.

    Explicit rather than implicit: a half-written run is worse than no run, so
    the failure path must be a rollback, not whatever the session happened to
    have flushed.
    """
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
