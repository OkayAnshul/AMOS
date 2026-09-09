"""Do the ORM models match the database?

This file exists because they did not. Three columns reached the database through
hand-written migrations while the corresponding `str.replace` patches to
`models.py` silently failed, and nothing noticed for two milestones — the V0.6
code kept working because `amos.memory.episodic` uses raw SQL, so the missing
model attribute was never accessed.

Migrations and models are two descriptions of one schema. Nothing was comparing
them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.database.models import Base

pytestmark = pytest.mark.asyncio

#: Columns that exist in the database and deliberately not in the models.
#: `vector` has no SQLAlchemy core type, so these are read and written through
#: raw SQL. Listing them explicitly keeps the check meaningful — a blanket
#: "ignore anything missing" would defeat the purpose.
DELIBERATELY_UNMAPPED: dict[str, set[str]] = {
    "runs": {"goal_embedding"},
    "chunks": {"embedding"},
    "memories": {"embedding"},
}

#: Tables managed entirely by raw SQL migrations, with no ORM model.
UNMAPPED_TABLES = {"alembic_version", "documents", "chunks", "memories"}


async def _columns(session: AsyncSession, table: str) -> set[str]:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return {row[0] for row in result}


@pytest.mark.parametrize("table", sorted(Base.metadata.tables))
async def test_models_match_the_database_schema(db_session: AsyncSession, table: str) -> None:
    database = await _columns(db_session, table)
    assert database, f"table '{table}' is in the models but not in the database"

    model = {column.name for column in Base.metadata.tables[table].columns}
    allowed = DELIBERATELY_UNMAPPED.get(table, set())

    missing_from_model = database - model - allowed
    missing_from_database = model - database

    assert not missing_from_model, (
        f"{table}: columns in the database but not in the model: {sorted(missing_from_model)}"
    )
    assert not missing_from_database, (
        f"{table}: columns in the model but not in the database: "
        f"{sorted(missing_from_database)} — is a migration missing?"
    )


async def test_every_database_table_is_accounted_for(db_session: AsyncSession) -> None:
    """A table with no model and no entry in UNMAPPED_TABLES is a table someone
    forgot about."""
    result = await db_session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    database_tables = {row[0] for row in result}
    unaccounted = database_tables - set(Base.metadata.tables) - UNMAPPED_TABLES
    assert not unaccounted, f"tables with no model and no exemption: {sorted(unaccounted)}"


async def test_the_speculative_task_claim_column_is_gone(
    db_session: AsyncSession,
) -> None:
    """V0.4 added `tasks.claimed_at` for a V0.8 that then claimed at run level
    instead. Removed rather than carried, and pinned here so it does not creep
    back as "we might need it"."""
    assert "claimed_at" not in await _columns(db_session, "tasks")
    assert "claimed_at" in await _columns(db_session, "runs")
