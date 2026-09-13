"""One user cannot see another's data (V1.4, ADR-013).

These are the tests the milestone exists for. Everything else in V1.4 is
plumbing; **isolation is the claim**, and it is a claim that fails silently — a
missing `WHERE user_id = ...` does not raise, does not log, and returns *more*
data rather than less. It looks exactly like a working feature.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from amos.auth import Actor, authenticate, create_user, hash_api_key, key_from_header
from amos.database.repository import RunRepository
from amos.memory.semantic import SemanticMemory
from amos.rag.embeddings import FakeEmbeddings


def memory_for(session: AsyncSession, actor: Actor) -> SemanticMemory:
    return SemanticMemory(session, FakeEmbeddings(dimensions=1536), actor)


# ---------- runs ----------


async def test_one_user_cannot_read_anothers_run(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    """The headline. A run is what someone asked and what came back."""
    mine = await RunRepository(db_session, actor).create_run(goal="secret goal", request_id="r")

    seen = await RunRepository(db_session, other_actor).get_trace(mine.id)

    assert seen is None, "a run leaked across users"
    assert await RunRepository(db_session, actor).get_trace(mine.id) is not None


async def test_listing_runs_shows_only_your_own(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    await RunRepository(db_session, actor).create_run(goal="mine", request_id="r")
    await RunRepository(db_session, other_actor).create_run(goal="theirs", request_id="r")

    mine = await RunRepository(db_session, actor).list_recent(limit=50)
    theirs = await RunRepository(db_session, other_actor).list_recent(limit=50)

    assert [r.goal_text for r in mine] == ["mine"]
    assert [r.goal_text for r in theirs] == ["theirs"]


async def test_an_idempotency_key_is_scoped_to_its_user(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    """Two users may independently choose the same key.

    Theirs is a key in *their* namespace. A global one would leak the existence
    of another user's request — and, worse, would hand them that user's run.
    """
    key = f"shared-{uuid.uuid4()}"
    mine = await RunRepository(db_session, actor).create_run(
        goal="mine", request_id="r", idempotency_key=key
    )

    found = await RunRepository(db_session, other_actor).find_by_idempotency_key(key)
    assert found is None

    assert (await RunRepository(db_session, actor).find_by_idempotency_key(key)).id == mine.id


# ---------- memories ----------


async def test_one_user_cannot_recall_anothers_fact(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    await memory_for(db_session, actor).remember("favourite_language", "Python")

    assert await memory_for(db_session, other_actor).recall_exact("favourite_language") is None
    assert await memory_for(db_session, actor).recall_exact("favourite_language") is not None


async def test_similarity_recall_does_not_cross_users(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    """The dangerous one. Exact lookup missing a filter returns nothing and looks
    broken; *similarity* missing a filter returns somebody else's fact ranked
    highly, and looks like it worked.
    """
    await memory_for(db_session, actor).remember("user_name", "Anshul")

    hits = await memory_for(db_session, other_actor).recall_similar(
        "what is my name", min_score=0.0
    )

    assert hits == []


async def test_the_same_subject_is_independent_per_user(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    """Supersession must not reach across users — otherwise one user storing a
    fact silently retires another's."""
    await memory_for(db_session, actor).remember("user_name", "Anshul")
    await memory_for(db_session, other_actor).remember("user_name", "Someone Else")

    assert (await memory_for(db_session, actor).recall_exact("user_name")).content == "Anshul"
    assert (
        await memory_for(db_session, other_actor).recall_exact("user_name")
    ).content == "Someone Else"


async def test_history_does_not_cross_users(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    await memory_for(db_session, actor).remember("plan", "first")
    await memory_for(db_session, actor).remember("plan", "second")

    assert await memory_for(db_session, other_actor).history("plan") == []
    assert len(await memory_for(db_session, actor).history("plan")) == 2


async def test_counting_memories_is_per_user(
    db_session: AsyncSession, actor: Actor, other_actor: Actor
) -> None:
    await memory_for(db_session, actor).remember("a", "1")
    await memory_for(db_session, actor).remember("b", "2")
    await memory_for(db_session, other_actor).remember("c", "3")

    assert await memory_for(db_session, actor).count_current() == 2
    assert await memory_for(db_session, other_actor).count_current() == 1


# ---------- credentials ----------


async def test_a_valid_key_authenticates(db_session: AsyncSession) -> None:
    created, key = await create_user(db_session, f"alice-{uuid.uuid4().hex[:6]}")

    assert (await authenticate(db_session, key)) == created


async def test_a_wrong_key_authenticates_as_nobody(db_session: AsyncSession) -> None:
    await create_user(db_session, f"bob-{uuid.uuid4().hex[:6]}")

    assert await authenticate(db_session, "not-a-real-key") is None
    assert await authenticate(db_session, None) is None
    assert await authenticate(db_session, "") is None


async def test_the_plaintext_key_is_never_stored(db_session: AsyncSession) -> None:
    """A database that leaks should not also hand over access."""
    from sqlalchemy import select

    from amos.database.models import User

    _created, key = await create_user(db_session, f"carol-{uuid.uuid4().hex[:6]}")

    stored = (
        await db_session.execute(
            select(User.api_key_hash).where(User.api_key_hash == hash_api_key(key))
        )
    ).scalar_one()
    assert stored != key
    assert len(stored) == 64  # sha256 hex


async def test_two_users_never_share_a_key(db_session: AsyncSession) -> None:
    _a, key_a = await create_user(db_session, f"d-{uuid.uuid4().hex[:6]}")
    _b, key_b = await create_user(db_session, f"e-{uuid.uuid4().hex[:6]}")

    assert key_a != key_b


@pytest.mark.parametrize(
    "header",
    ["", "   ", "Bearer", "Bearer  ", "Basic abc", "abc", "bearer", None],
)
def test_malformed_authorization_headers_yield_no_key(header: str | None) -> None:
    assert key_from_header(header) is None


def test_a_well_formed_header_yields_the_key() -> None:
    assert key_from_header("Bearer abc123") == "abc123"
    # Case-insensitive scheme, per RFC 7235.
    assert key_from_header("bearer abc123") == "abc123"


# ---------- the structural guard ----------


def test_every_run_query_in_the_repository_is_scoped() -> None:
    """Isolation must not be losable by writing a query the ordinary way.

    A bare `select(Run)` would compile, pass review, and return every user's
    rows — so the absence of one is asserted rather than hoped for.

    Asserted on the *statement*, not on a helper's name: what matters is that
    every query over runs carries the owner filter, however it is spelled.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "src" / "amos" / "database" / "repository.py"
    ).read_text()

    unscoped = [
        line.strip()
        for line in source.splitlines()
        if "select(Run)" in line and "Run.user_id" not in line
    ]
    assert not unscoped, f"unscoped queries over runs: {unscoped}"
    assert "select(Run).where(Run.user_id" in source, (
        "no scoped run query found at all — isolation is not enforced anywhere"
    )


def test_every_memories_statement_filters_by_user() -> None:
    """Semantic memory is raw SQL, so the same guarantee needs the same guard.

    The subtle case is `recall_similar`: an exact lookup missing its filter
    returns nothing and looks broken, while a *similarity* search missing its
    filter returns someone else's fact ranked highly, and looks like it worked.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "src" / "amos" / "memory" / "semantic.py"
    ).read_text()

    unscoped = [
        stmt.strip()[:60].replace("\n", " ")
        for stmt in re.findall(r'sql_text\(\s*(""".*?"""|(?:"[^"]*"\s*)+)', source, re.S)
        if "memories" in stmt and "user_id" not in stmt
    ]
    assert not unscoped, f"these statements touch memories without scoping: {unscoped}"
