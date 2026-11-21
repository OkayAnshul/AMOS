"""The read API (V1.7): what the demo page shows, and that it shows it to its owner only.

Every endpoint that lists personal data gets an isolation test here, as every
V1.4 query did: write as one user, read as another, assert nothing comes back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.tool_agent import ToolUsingAgent
from amos.api.app import create_app
from amos.api.persistence import RunService
from amos.auth import Actor, create_user
from amos.database.engine import session_scope
from amos.database.models import Document, Run, User
from amos.llm.fake import FakeProvider
from amos.memory.semantic import SemanticMemory
from amos.rag.embeddings import FakeEmbeddings
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import isolated_settings, valid_response_json

EMBEDDINGS = FakeEmbeddings(dimensions=1536)
DOC = (
    "# Offsite notes\n\n"
    "The offsite is in Goa in March, and the budget per person is fifteen thousand rupees. "
    "Travel is by train from Bhubaneswar, booked two months ahead.\n"
)


class Client:
    def __init__(self, http: httpx.AsyncClient, key: str) -> None:
        self.http = http
        self.headers = {"Authorization": f"Bearer {key}"}

    async def get(self, path: str, **params: object) -> httpx.Response:
        return await self.http.get(path, headers=self.headers, params=params)  # type: ignore[arg-type]

    async def post(self, path: str, body: object) -> httpx.Response:
        return await self.http.post(path, headers=self.headers, json=body)


@pytest_asyncio.fixture
async def other(db_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[Actor]:
    async with session_scope(db_factory) as session:
        created, key = await create_user(session, f"reader-{uuid.uuid4().hex[:8]}")
    object.__setattr__(created, "api_key", key)
    yield created
    async with session_scope(db_factory) as session:
        await session.execute(delete(Run).where(Run.user_id == created.id))
        await session.execute(delete(Document).where(Document.user_id == created.id))
        await session.execute(delete(User).where(User.id == created.id))


@pytest_asyncio.fixture
async def clients(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor, other: Actor
) -> AsyncIterator[tuple[Client, Client]]:
    agent = ToolUsingAgent(
        FakeProvider([valid_response_json(answer="42")]), ToolRegistry([CalculatorTool()])
    )
    app = create_app(isolated_settings(), agent=agent, run_service=RunService(agent, db_factory))
    app.state.embeddings = EMBEDDINGS
    app.state.registry = ToolRegistry([CalculatorTool()])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://amos"
    ) as http:
        yield Client(http, factory_actor.api_key), Client(http, other.api_key)  # type: ignore[attr-defined]
    async with session_scope(db_factory) as session:
        await session.execute(delete(Run).where(Run.user_id == factory_actor.id))
        await session.execute(delete(Document).where(Document.user_id == factory_actor.id))
        await session.execute(
            text("DELETE FROM memories WHERE user_id = :owner"), {"owner": str(factory_actor.id)}
        )


# ---------- runs ----------


async def test_the_run_list_is_yours_only(clients: tuple[Client, Client]) -> None:
    me, them = clients
    submitted = await me.post("/v1/goals", {"goal": "What is 6 times 7?"})
    run_id = submitted.json()["run_id"]

    mine = (await me.get("/v1/runs")).json()
    assert any(r["run_id"] == run_id for r in mine)
    assert mine[0]["answer"] == "42"
    assert all(r["run_id"] != run_id for r in (await them.get("/v1/runs")).json()), "run leaked"


async def test_a_sync_run_is_findable_by_its_idempotency_key(
    clients: tuple[Client, Client],
) -> None:
    """How the page watches a synchronous run: it picks the key, so it can find
    the run row while the POST is still waiting."""
    me, them = clients
    key = f"page-{uuid.uuid4()}"
    response = await me.http.post(
        "/v1/goals",
        json={"goal": "What is 6 times 7?"},
        headers={**me.headers, "idempotency-key": key},
    )
    found = (await me.get("/v1/runs", idempotency_key=key)).json()
    assert [r["run_id"] for r in found] == [response.json()["run_id"]]
    assert (await them.get("/v1/runs", idempotency_key=key)).json() == [], "key lookup leaked"


# ---------- options ----------


async def test_a_run_cannot_switch_on_what_the_server_has_off(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    agent = ToolUsingAgent(FakeProvider([valid_response_json()]), ToolRegistry([CalculatorTool()]))
    app = create_app(
        isolated_settings(critic_enabled=False),
        agent=agent,
        run_service=RunService(agent, db_factory),
    )
    headers = {"Authorization": f"Bearer {factory_actor.api_key}"}  # type: ignore[attr-defined]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as h:
        on = await h.post(
            "/v1/goals", json={"goal": "x", "options": {"critic": True}}, headers=headers
        )
        unknown = await h.post(
            "/v1/goals", json={"goal": "x", "options": {"turbo": True}}, headers=headers
        )
    assert on.status_code == 422 and "critic" in on.text
    assert unknown.status_code == 422, "an unknown option is refused, not ignored"


async def test_options_are_stored_on_the_run(clients: tuple[Client, Client]) -> None:
    me, _ = clients
    submitted = await me.post(
        "/v1/goals", {"goal": "What is 6 times 7?", "options": {"critic": False}}
    )
    trace = (await me.get(f"/v1/runs/{submitted.json()['run_id']}")).json()
    assert trace["options"] == {"critic": False}


# ---------- memory ----------


async def test_memories_are_yours_only(
    clients: tuple[Client, Client],
    db_factory: async_sessionmaker[AsyncSession],
    factory_actor: Actor,
) -> None:
    me, them = clients
    async with session_scope(db_factory) as session:
        memory = SemanticMemory(session, EMBEDDINGS, factory_actor)
        await memory.remember("project review", "Friday")
        await memory.remember("project review", "Monday")

    current = (await me.get("/v1/memories")).json()
    assert [(m["subject"], m["content"]) for m in current] == [("project_review", "Monday")]
    history = (await me.get("/v1/memories/history", subject="project review")).json()
    assert [m["content"] for m in history] == ["Monday", "Friday"]
    assert [m["superseded"] for m in history] == [False, True]

    assert (await them.get("/v1/memories")).json() == [], "memories leaked"
    assert (await them.get("/v1/memories/history", subject="project review")).json() == []


# ---------- corpus and uploads ----------


async def test_an_upload_is_yours_only_and_its_chunks_open_for_you(
    clients: tuple[Client, Client],
) -> None:
    me, them = clients
    uploaded = await me.post("/v1/documents", {"filename": "offsite.md", "text": DOC})
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert (body["status"], body["source"]) == ("ingested", "uploads/offsite.md")
    assert body["chunks"] >= 1

    mine = (await me.get("/v1/corpus")).json()
    assert any(
        d["source"] == "uploads/offsite.md" and d["owner"] == "you" for d in mine["documents"]
    )
    assert mine["totals"]["your_documents"] >= 1
    theirs = (await them.get("/v1/corpus")).json()
    assert all(d["source"] != "uploads/offsite.md" for d in theirs["documents"]), "upload leaked"
    assert theirs["totals"]["your_documents"] == 0

    again = await me.post("/v1/documents", {"filename": "offsite.md", "text": DOC})
    assert again.json()["status"] == "unchanged"


async def test_a_citation_chunk_opens_for_its_owner_and_404s_for_anyone_else(
    clients: tuple[Client, Client],
    db_factory: async_sessionmaker[AsyncSession],
    factory_actor: Actor,
) -> None:
    me, them = clients
    await me.post("/v1/documents", {"filename": "cite.md", "text": DOC})
    async with session_scope(db_factory) as session:
        chunk_id = (
            await session.execute(
                text(
                    "SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id "
                    "WHERE d.user_id = :owner AND d.source = 'uploads/cite.md' LIMIT 1"
                ),
                {"owner": str(factory_actor.id)},
            )
        ).scalar_one()

    opened = await me.get(f"/v1/chunks/{chunk_id}")
    assert opened.status_code == 200 and "Goa" in opened.json()["content"]
    assert (await them.get(f"/v1/chunks/{chunk_id}")).status_code == 404, "chunk leaked"


@pytest.mark.parametrize(
    ("filename", "reason"),
    [
        ("notes.pdf", ".md and .txt"),
        ("../../etc/passwd.md", "plain file name"),
        ("folder/notes.md", "plain file name"),
        (".hidden.md", "plain file name"),
    ],
)
async def test_an_upload_must_be_a_plain_md_or_txt_file(
    clients: tuple[Client, Client], filename: str, reason: str
) -> None:
    me, _ = clients
    response = await me.post("/v1/documents", {"filename": filename, "text": DOC})
    assert response.status_code == 422
    assert reason in response.text


async def test_an_upload_has_a_size_limit(clients: tuple[Client, Client]) -> None:
    me, _ = clients
    response = await me.post("/v1/documents", {"filename": "big.md", "text": "x" * 20_001})
    assert response.status_code == 422 and "make ingest" in response.text


async def test_an_upload_named_like_a_corpus_file_does_not_replace_it(
    clients: tuple[Client, Client], db_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Uploads live under `uploads/`, and replacement is scoped to the owner, so
    a user cannot overwrite the system corpus by choosing a file name."""
    from sqlalchemy import func, select

    me, _ = clients
    async with session_scope(db_factory) as session:
        before = (
            await session.execute(select(func.count(Document.id)).where(Document.user_id.is_(None)))
        ).scalar_one()
    await me.post("/v1/documents", {"filename": "00-vision.md", "text": DOC})
    async with session_scope(db_factory) as session:
        after = (
            await session.execute(select(func.count(Document.id)).where(Document.user_id.is_(None)))
        ).scalar_one()
    assert after == before


# ---------- system and insights ----------


async def test_the_system_view_describes_the_server(clients: tuple[Client, Client]) -> None:
    me, _ = clients
    body = (await me.get("/v1/system")).json()
    assert body["provider"]["model"] == "fake-model"
    assert body["quota"]["daily_limit"] == 500 and "calls_today" in body["quota"]
    assert body["features"]["critic"] is True
    assert "critic" in body["switchable_off"]
    assert {a["name"] for a in body["agents"]} >= {"researcher", "analyst", "critic"}
    assert [t["name"] for t in body["tools"]] == ["calculator"]
    assert body["database"]["connected"] is True and body["database"]["revision"]
    assert body["queue"] is not None and "queued" in body["queue"]


async def test_insights_count_only_your_runs(clients: tuple[Client, Client]) -> None:
    me, them = clients
    await me.post("/v1/goals", {"goal": "What is 6 times 7?"})
    mine = (await me.get("/v1/insights")).json()
    theirs = (await them.get("/v1/insights")).json()
    assert sum(mine["runs_by_status"].values()) >= 1
    assert theirs["runs_by_status"] == {}, "insights counted another user's runs"


# ---------- the structural guards ----------


@pytest.mark.parametrize(
    "module",
    ["src/amos/rag/corpus.py", "src/amos/database/insights.py"],
)
def test_every_statement_in_the_owner_scoped_modules_filters_by_owner(module: str) -> None:
    """V1.6's lesson: a guard protects the files it scans. These two are new
    places queries are built, so they get the same guard as semantic memory."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / module).read_text()
    statements = re.findall(r'sql_text\(\s*((?:f?"[^"]*"\s*)+)', source, re.S) + re.findall(
        r'rows\(\s*((?:f?"[^"]*"\s*)+)\)', source, re.S
    )
    assert statements, f"no statements found in {module} — the guard is not looking"
    unscoped = [s.strip()[:70] for s in statements if ":owner" not in s]
    assert not unscoped, f"statements without an owner filter: {unscoped}"
