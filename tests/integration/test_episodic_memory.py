"""Episodic memory: finding past runs by goal similarity.

Note there is no `episodes` table — an episode IS a run, so these tests exercise
columns on `runs`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from amos.database.models import Run
from amos.database.repository import RunRepository
from amos.memory.episodic import EpisodicMemory
from amos.rag.embeddings import FakeEmbeddings

pytestmark = pytest.mark.asyncio


def episodic(session: AsyncSession) -> EpisodicMemory:
    return EpisodicMemory(session, FakeEmbeddings(dimensions=1536))


async def seed(session: AsyncSession, goal: str, lesson: str | None = None) -> uuid.UUID:
    run = await RunRepository(session).create_run(goal=goal, request_id="r")
    await episodic(session).record(run.id, goal, lesson)
    return run.id


async def test_similar_past_goal_is_found(db_session: AsyncSession) -> None:
    await seed(
        db_session,
        "calculate percentage of a number using the calculator",
        "2/2 tasks succeeded; tools used: calculator",
    )
    await seed(db_session, "explain the security model for reading files")

    hits = await episodic(db_session).recall_similar(
        "calculate a percentage of some number", min_score=0.0
    )
    assert hits
    assert "percentage" in hits[0].goal


async def test_the_lesson_is_recalled_with_the_episode(db_session: AsyncSession) -> None:
    """A past goal without what happened is not experience, just history."""
    await seed(db_session, "index the documentation corpus", "3/3 tasks succeeded")

    hits = await episodic(db_session).recall_similar(
        "index the documentation corpus", min_score=0.0
    )
    assert hits[0].lesson == "3/3 tasks succeeded"


async def test_the_current_run_is_excluded(db_session: AsyncSession) -> None:
    """V0.3 writes the run row before executing, so without this the most
    similar past experience is always the run asking the question."""
    goal = "a goal that is being asked about right now"
    run_id = await seed(db_session, goal)

    included = await episodic(db_session).recall_similar(goal, min_score=0.0)
    excluded = await episodic(db_session).recall_similar(goal, min_score=0.0, exclude_run_id=run_id)

    assert any(h.run_id == run_id for h in included)
    assert all(h.run_id != run_id for h in excluded)


async def test_runs_without_an_embedding_are_not_returned(
    db_session: AsyncSession,
) -> None:
    """A run that was never recorded as an episode is not experience."""
    await RunRepository(db_session).create_run(goal="never embedded", request_id="r")
    hits = await episodic(db_session).recall_similar("never embedded", min_score=0.0)
    assert all(h.goal != "never embedded" for h in hits)


async def test_min_score_excludes_unrelated_experience(db_session: AsyncSession) -> None:
    await seed(db_session, "completely unrelated topic about cooking pasta")
    hits = await episodic(db_session).recall_similar(
        "quantum chromodynamics lattice gauge theory", min_score=0.9
    )
    assert hits == []


async def test_episode_records_the_run_outcome(db_session: AsyncSession) -> None:
    run_id = await seed(db_session, "a goal with a known outcome")
    hits = await episodic(db_session).recall_similar("a goal with a known outcome", min_score=0.0)
    episode = next(h for h in hits if h.run_id == run_id)
    assert episode.status == "RECEIVED"

    await db_session.execute(delete(Run).where(Run.id == run_id))
