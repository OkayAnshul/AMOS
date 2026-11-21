"""fetch_page's provenance is this run's own stored web_search rows (ADR-018)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.auth import Actor
from amos.database.engine import session_scope
from amos.database.models import Run, ToolCallRow
from amos.database.repository import RunRepository
from amos.observability import set_current_run_id
from amos.tools.builtin.web import RecordedProvenance


async def _run_with_search(
    factory: async_sessionmaker[AsyncSession], actor: Actor, urls: list[str], status: str = "ok"
) -> uuid.UUID:
    async with session_scope(factory) as session:
        run = await RunRepository(session, actor).create_run(goal="g", request_id="r")
        session.add(
            ToolCallRow(
                id=uuid.uuid4(),
                run_id=run.id,
                call_id="c",
                tool_name="web_search",
                arguments={"query": "q"},
                output={"results": [{"url": u, "title": "t"} for u in urls]},
                status=status,
            )
        )
        return run.id


async def test_only_this_runs_successful_searches_count(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    mine = await _run_with_search(db_factory, factory_actor, ["https://a.example/1"])
    other = await _run_with_search(db_factory, factory_actor, ["https://b.example/2"])
    failed = await _run_with_search(db_factory, factory_actor, ["https://c.example/3"], "error")
    try:
        provenance = RecordedProvenance(db_factory)
        set_current_run_id(str(mine))
        assert await provenance.urls() == {"https://a.example/1"}, "another run's results leaked"
        set_current_run_id(str(failed))
        assert await provenance.urls() == set(), "a failed search proves nothing"
        set_current_run_id(None)
        assert await provenance.urls() == set(), "outside a run, nothing can be fetched"
    finally:
        set_current_run_id(None)
        async with session_scope(db_factory) as session:
            await session.execute(delete(Run).where(Run.id.in_([mine, other, failed])))
