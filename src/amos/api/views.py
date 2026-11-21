"""The read side of the API (V1.7): what the demo page shows.

Response models and the code that assembles them. Every query that touches a
user's data goes through an owner-scoped module — `RunRepository`,
`SemanticMemory`, `rag.corpus`, `database.insights` — never raw SQL here, so the
source-scan guards over those modules cover everything this file serves.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos import __version__
from amos.agents.registry import AgentRegistry
from amos.agents.schemas import OPTION_SETTINGS
from amos.auth import Actor
from amos.config import Settings
from amos.database.insights import insights
from amos.database.models import Run
from amos.database.repository import RunRepository
from amos.database.usage import usage_today
from amos.memory.semantic import Fact, SemanticMemory
from amos.rag.corpus import corpus_totals, get_chunk, list_documents
from amos.rag.embeddings import EmbeddingProvider
from amos.rag.ingest import Ingestor
from amos.rag.store import PgVectorStore
from amos.tools.registry import ToolRegistry
from amos.worker.queue import count_dead_letter, queue_overview

# ---------- runs ----------


class RunSummary(BaseModel):
    run_id: str
    goal: str
    status: str
    created_at: str
    completed_at: str | None = None
    total_tokens: int = 0
    latency_ms: int = 0
    options: dict[str, bool] = Field(default_factory=dict)
    answer: str | None = None
    confidence: str | None = None
    attempt_count: int = 0
    lesson: str | None = None


def summarise(run: Run) -> RunSummary:
    result = run.result or {}
    answer = result.get("answer")
    return RunSummary(
        run_id=str(run.id),
        goal=run.goal_text,
        status=run.status,
        created_at=run.created_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        total_tokens=run.total_tokens,
        latency_ms=run.latency_ms,
        options=run.options or {},
        answer=str(answer)[:400] if answer else None,
        confidence=result.get("confidence"),
        attempt_count=run.attempt_count,
        lesson=run.lesson,
    )


async def list_runs(
    session: AsyncSession,
    actor: Actor,
    *,
    limit: int,
    before: datetime | None,
    idempotency_key: str | None,
) -> list[RunSummary]:
    repo = RunRepository(session, actor)
    if idempotency_key:
        # How a page gets a live view of a SYNC run: it chose the key, so it can
        # find the run row — committed before execution — while the POST waits.
        run = await repo.find_by_idempotency_key(idempotency_key)
        return [summarise(run)] if run is not None else []
    return [summarise(run) for run in await repo.list_recent(limit=limit, before=before)]


# ---------- memory ----------


class MemoryView(BaseModel):
    id: str
    subject: str
    content: str
    confidence: float
    superseded: bool = False
    created_at: str | None = None
    source_run_id: str | None = None


def memory_view(fact: Fact) -> MemoryView:
    return MemoryView(
        id=str(fact.id),
        subject=fact.subject,
        content=fact.content,
        confidence=fact.confidence,
        superseded=fact.superseded,
        created_at=fact.created_at.isoformat() if fact.created_at else None,
        source_run_id=str(fact.source_run_id) if fact.source_run_id else None,
    )


async def memories(
    session: AsyncSession, actor: Actor, embeddings: EmbeddingProvider, limit: int
) -> list[MemoryView]:
    return [
        memory_view(f) for f in await SemanticMemory(session, embeddings, actor).list_current(limit)
    ]


async def memory_history(
    session: AsyncSession, actor: Actor, embeddings: EmbeddingProvider, subject: str
) -> list[MemoryView]:
    return [
        memory_view(f) for f in await SemanticMemory(session, embeddings, actor).history(subject)
    ]


# ---------- corpus ----------


class DocumentView(BaseModel):
    id: str
    source: str
    title: str | None = None
    chunk_count: int
    created_at: str
    owner: str  # "system" or "you"


class CorpusView(BaseModel):
    totals: dict[str, int]
    documents: list[DocumentView]


async def corpus(session: AsyncSession, actor: Actor) -> CorpusView:
    return CorpusView(
        totals=await corpus_totals(session, actor.id),
        documents=[
            DocumentView(
                id=str(d.id),
                source=d.source,
                title=d.title,
                chunk_count=d.chunk_count,
                created_at=d.created_at.isoformat(),
                owner="you" if d.owned else "system",
            )
            for d in await list_documents(session, actor.id)
        ],
    )


class ChunkView(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    heading: str | None = None
    content: str
    source: str
    title: str | None = None
    owner: str


async def chunk(session: AsyncSession, actor: Actor, chunk_id: uuid.UUID) -> ChunkView | None:
    found = await get_chunk(session, actor.id, chunk_id)
    if found is None:
        return None
    return ChunkView(
        id=str(found.id),
        document_id=str(found.document_id),
        chunk_index=found.chunk_index,
        heading=found.heading,
        content=found.content,
        source=found.source,
        title=found.title,
        owner="you" if found.owned else "system",
    )


# ---------- upload ----------

#: One embedding batch (25 chunks of ~1000 characters) and no pacing pause, so an
#: upload answers in seconds. Larger documents belong in `make ingest`.
UPLOAD_MAX_BYTES = 20_000
UPLOAD_SUFFIXES = (".md", ".txt")


class DocumentUpload(BaseModel):
    """JSON rather than multipart: multipart needs `python-multipart`, a new
    dependency for one endpoint, and the page reads the file as text anyway."""

    filename: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class UploadResult(BaseModel):
    document_id: str | None
    source: str
    chunks: int
    status: str  # "ingested" | "unchanged"


class UploadRejected(ValueError):
    """The upload is not something this endpoint accepts."""


def validate_upload(upload: DocumentUpload) -> str:
    """The stored source for an upload, or `UploadRejected` saying why not."""
    name = PurePosixPath(upload.filename.replace("\\", "/")).name
    if not name or name != upload.filename.strip() or name.startswith("."):
        raise UploadRejected("Give a plain file name, without folders.")
    if not name.lower().endswith(UPLOAD_SUFFIXES):
        raise UploadRejected("Only .md and .txt files can be uploaded.")
    if len(upload.text.encode("utf-8")) > UPLOAD_MAX_BYTES:
        raise UploadRejected(
            f"The file is over {UPLOAD_MAX_BYTES // 1000} KB. Add larger documents with "
            "`make ingest`."
        )
    return f"uploads/{name}"


async def upload_document(
    session: AsyncSession, actor: Actor, embeddings: EmbeddingProvider, upload: DocumentUpload
) -> UploadResult:
    """Ingest one user-owned document. A same-named earlier upload by the same
    user is replaced; the system corpus and other users' files never are."""
    source = validate_upload(upload)
    ingestor = Ingestor(session, PgVectorStore(session), embeddings)
    document_id, chunks = await ingestor.ingest_text(
        source=source,
        text=upload.text,
        title=PurePosixPath(source).stem,
        owner=actor.id,
    )
    return UploadResult(
        document_id=str(document_id) if document_id else None,
        source=source,
        chunks=chunks,
        status="ingested" if document_id else "unchanged",
    )


# ---------- system ----------


class SystemView(BaseModel):
    version: str
    env: str
    actor: str
    authenticated: bool
    provider: dict[str, Any]
    quota: dict[str, Any]
    features: dict[str, bool]
    switchable_off: list[str]
    bounds: dict[str, Any]
    database: dict[str, Any]
    queue: dict[str, Any] | None
    dead_letters: int | None
    corpus: dict[str, int] | None
    telemetry: dict[str, Any]
    agents: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    web: dict[str, Any] = Field(default_factory=dict)


def _migration_head() -> str | None:
    """The newest migration in the repository, when running from a checkout."""
    try:
        from alembic.script import ScriptDirectory

        directory = Path(__file__).resolve().parents[3] / "migrations"
        if not directory.is_dir():
            return None
        return ScriptDirectory(str(directory)).get_current_head()
    except Exception:  # noqa: BLE001 - informational only
        return None


async def _searxng_answers(base_url: str) -> bool:
    """Whether the search service is up — a two-second probe, so a stopped
    container shows as "not running" rather than as a slow page."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{base_url.rstrip('/')}/healthz")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def describe_tools(registry: ToolRegistry | None) -> list[dict[str, Any]]:
    if registry is None:
        return []
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "permission": tool.permission.value,
            "timeout_seconds": tool.timeout_seconds,
            "parameters": tool.spec().parameters,
        }
        for tool in sorted(registry, key=lambda t: t.name)
    ]


def describe_agents(settings: Settings) -> list[dict[str, Any]]:
    agents = AgentRegistry()
    routable = {spec.name for spec in agents.routable}
    return [
        {
            "name": spec.name,
            "purpose": spec.purpose,
            "tools": sorted(spec.tools),
            "routable": spec.name in routable,
            "max_iterations": settings.agent_max_iterations or spec.max_iterations,
            "routing_hint": spec.routing_hint,
        }
        for spec in agents.specs
    ]


async def system(
    settings: Settings,
    actor: Actor,
    *,
    session: AsyncSession | None,
    registry: ToolRegistry | None,
    tracing: bool,
) -> SystemView:
    features = {name: bool(getattr(settings, field)) for name, field in OPTION_SETTINGS.items()}
    features.update(
        {
            "async": settings.async_enabled,
            "memory_reconcile": settings.memory_reconcile_enabled,
        }
    )
    database: dict[str, Any] = {"connected": session is not None, "head": _migration_head()}
    web: dict[str, Any] = {
        "enabled": settings.web_enabled,
        "search_url": settings.searxng_url,
        "reachable": await _searxng_answers(settings.searxng_url),
        "engine": "SearXNG, self-hosted (ADR-018)",
    }
    quota: dict[str, Any] = {
        "daily_limit": settings.llm_daily_request_limit,
        "minute_limit": settings.llm_minute_request_limit,
        "embedding_daily_limit": settings.embedding_daily_limit,
        "resets": "midnight Pacific",
        "note": (
            "Counts model calls made inside runs. Calls by the eval, the trials and "
            "every embedding call are not recorded, so this is a floor."
        ),
    }
    queue = dead = totals = None
    if session is not None:
        current = (
            await session.execute(sql_text("SELECT version_num FROM alembic_version"))
        ).scalar()
        database["revision"] = current
        database["current"] = current == database["head"] if database["head"] else None
        quota.update(await usage_today(session))
        queue = await queue_overview(session)
        dead = await count_dead_letter(session, owner=actor.id)
        totals = await corpus_totals(session, actor.id)

    return SystemView(
        version=__version__,
        env=settings.env,
        actor=actor.name,
        authenticated=session is not None,
        provider={
            "name": "gemini",
            "model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "embedding_dimensions": settings.embedding_dimensions,
            "timeout_seconds": settings.llm_timeout_seconds,
            "retry": "one retry on a 5xx, inside the call's deadline (ADR-014)",
        },
        quota=quota,
        features=features,
        switchable_off=[name for name in OPTION_SETTINGS if features[name]],
        bounds={
            "llm_timeout_seconds": settings.llm_timeout_seconds,
            "task_timeout_seconds": settings.task_timeout_seconds,
            "worker_visibility_timeout": settings.worker_visibility_timeout,
            "task_max_attempts": settings.task_max_attempts,
            "worker_max_attempts": settings.worker_max_attempts,
            "agent_max_iterations": settings.agent_max_iterations,
            "max_revisions": settings.max_revisions,
            "max_delegation_depth": settings.max_delegation_depth,
            "delegation_budget": settings.delegation_budget,
            "retrieval_min_score": settings.retrieval_min_score,
            "memory_min_score": settings.memory_min_score,
            "run_max_llm_calls": settings.run_max_llm_calls,
            "run_max_tool_calls": settings.run_max_tool_calls,
            "run_max_page_fetches": settings.run_max_page_fetches,
        },
        web=web,
        database=database,
        queue=queue,
        dead_letters=dead,
        corpus=totals,
        telemetry={
            "tracing": tracing,
            "endpoint": settings.otlp_endpoint or None,
            "service_name": settings.otel_service_name,
            "goal_text_in_spans": settings.trace_content,
        },
        agents=describe_agents(settings),
        tools=describe_tools(registry),
    )


async def run_insights(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    return await insights(session, actor.id)
