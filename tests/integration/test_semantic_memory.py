"""Semantic memory: exact recall and deterministic contradiction resolution.

These live in `integration/` rather than `unit/` because the behaviour under test
**is the SQL**. Supersession, the partial index and the exact-vs-similar
distinction cannot be verified against a fake store without merely testing the
fake. Skipped automatically when no database is running.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.memory.semantic import SemanticMemory, normalise_subject
from amos.rag.embeddings import FakeEmbeddings

pytestmark = pytest.mark.asyncio


def memory(session: AsyncSession) -> SemanticMemory:
    return SemanticMemory(session, FakeEmbeddings(dimensions=1536))


# ---------- subject normalisation ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("User's Name", "user_s_name"),
        ("user name", "user_name"),
        ("USER_NAME", "user_name"),
        ("  spaced  out  ", "spaced_out"),
        ("preferred-language", "preferred_language"),
    ],
)
async def test_subjects_normalise_to_stable_keys(raw: str, expected: str) -> None:
    """Without this, the same fact stored with different capitalisation produces
    two 'current' values and contradiction resolution silently stops working."""
    assert normalise_subject(raw) == expected


async def test_empty_subject_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await memory(db_session).remember("!!!", "content")


# ---------- exact recall ----------


async def test_remember_then_recall_exactly(db_session: AsyncSession) -> None:
    store = memory(db_session)
    await store.remember("user_name", "Anshul")

    fact = await store.recall_exact("user_name")
    assert fact is not None
    assert fact.content == "Anshul"


async def test_recall_is_case_and_format_insensitive(db_session: AsyncSession) -> None:
    store = memory(db_session)
    await store.remember("Preferred Language", "Python")
    assert (await store.recall_exact("preferred_language")) is not None
    assert (await store.recall_exact("PREFERRED LANGUAGE")) is not None


async def test_unknown_subject_returns_none(db_session: AsyncSession) -> None:
    assert await memory(db_session).recall_exact("never_stored") is None


# ---------- contradiction resolution ----------


async def test_a_new_fact_supersedes_the_old_one(db_session: AsyncSession) -> None:
    """Newest wins — a rule, not a judgement, and specifically not the model's."""
    store = memory(db_session)
    await store.remember("user_city", "Bhubaneswar")
    await store.remember("user_city", "Bangalore")

    current = await store.recall_exact("user_city")
    assert current is not None
    assert current.content == "Bangalore"


async def test_exactly_one_current_fact_per_subject(db_session: AsyncSession) -> None:
    """The invariant the partial index depends on."""
    store = memory(db_session)
    for value in ("a", "b", "c", "d"):
        await store.remember("changing_fact", value)

    result = await db_session.execute(
        sql_text(
            "SELECT count(*) FROM memories "
            "WHERE subject = 'changing_fact' AND superseded_by IS NULL"
        )
    )
    assert result.scalar_one() == 1


async def test_superseded_facts_are_kept_not_deleted(db_session: AsyncSession) -> None:
    """A changed fact stays auditable, and a bad write stays recoverable."""
    store = memory(db_session)
    await store.remember("role", "student")
    await store.remember("role", "engineer")

    history = await store.history("role")
    assert [f.content for f in history] == ["engineer", "student"]
    assert history[0].superseded is False
    assert history[1].superseded is True


async def test_different_subjects_do_not_interfere(db_session: AsyncSession) -> None:
    store = memory(db_session)
    await store.remember("user_name", "Anshul")
    await store.remember("user_city", "Bangalore")
    await store.remember("user_name", "A.")

    assert (await store.recall_exact("user_city")).content == "Bangalore"  # type: ignore[union-attr]
    assert (await store.recall_exact("user_name")).content == "A."  # type: ignore[union-attr]


# ---------- similarity, the secondary path ----------


async def test_similar_recall_finds_related_facts(db_session: AsyncSession) -> None:
    store = memory(db_session)
    await store.remember("favourite_language", "the user prefers Python for backend work")
    await store.remember("favourite_food", "the user likes ramen and gyoza")

    hits = await store.recall_similar("Python backend programming language", min_score=0.0)
    assert hits
    assert hits[0].subject == "favourite_language"


async def test_similarity_never_returns_superseded_facts(db_session: AsyncSession) -> None:
    """The reason facts are not stored in a vector index alone: a vector search
    has no notion of 'current', so an old value would keep resurfacing."""
    store = memory(db_session)
    await store.remember("deadline", "the project deadline is in March")
    await store.remember("deadline", "the project deadline is in June")

    hits = await store.recall_similar("when is the deadline", min_score=0.0)
    contents = [h.content for h in hits]
    assert any("June" in c for c in contents)
    assert not any("March" in c for c in contents)


async def test_count_reports_only_current_facts(db_session: AsyncSession) -> None:
    """Asserts a delta, not an absolute.

    The first version compared `count_current()` to a fixed number and broke the
    moment a live demo stored a real fact — the rollback fixture isolates this
    test's writes, not everyone else's rows. A test over a shared table must
    measure its own effect.
    """
    store = memory(db_session)
    before = await store.count_current()

    await store.remember("count_test_a", "1")
    await store.remember("count_test_a", "2")  # supersedes, so no net increase
    await store.remember("count_test_b", "1")

    assert await store.count_current() == before + 2


async def test_provenance_records_which_run_learned_the_fact(
    db_session: AsyncSession,
) -> None:
    """`source_run_id` was NULL for every row until this was wired.

    The tool is constructed at startup, before any run exists, so the run id
    cannot be a constructor argument — it comes from a contextvar set by
    RunService. Provenance is one of the three reasons
    docs/09-memory-architecture.md gives for memory being relational at all, so a
    permanently-NULL column quietly removed a third of the justification.
    """
    from amos.database.repository import RunRepository

    run = await RunRepository(db_session).create_run(goal="g", request_id="r")
    store = memory(db_session)
    await store.remember("traced_fact", "a fact", source_run_id=run.id)

    result = await db_session.execute(
        sql_text("SELECT source_run_id FROM memories WHERE subject = 'traced_fact'")
    )
    assert result.scalar_one() == run.id


async def test_the_contextvar_supplies_the_run_id_when_none_is_passed(
    db_session: AsyncSession,
) -> None:
    """The path that actually runs in production: RememberFactTool is built at
    startup with no run id and reads the current one."""
    import uuid as _uuid

    from amos.memory.tools import RememberFactTool
    from amos.observability import set_current_run_id

    run_id = _uuid.uuid4()
    set_current_run_id(str(run_id))
    try:
        tool = RememberFactTool(None, None)
        assert tool._source_run_id() == run_id
    finally:
        set_current_run_id(None)
