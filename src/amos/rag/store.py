"""Vector storage.

`VectorStore` is a Protocol for the same reason `LLMProvider` is: the test suite
needs an in-memory implementation regardless, so the abstraction is paid for by
V0.5's own tests rather than by speculation about swapping databases (ADR-001).

It also keeps the Qdrant option honest — the ADR says "reconsider above ~5M
vectors", and that reconsideration is a new class here, not a rewrite of every
call site.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class StoredChunk:
    """A chunk ready to be written."""

    document_id: uuid.UUID
    chunk_index: int
    content: str
    embedding: list[float]
    heading: str | None = None
    metadata: dict[str, str] | None = None


@dataclass
class Hit:
    """A retrieval result.

    `score` is cosine *similarity* in [-1, 1] — higher is better — converted from
    pgvector's cosine *distance*. Exposing distance would invert the intuition of
    every caller and every threshold.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float
    heading: str | None = None
    source: str | None = None


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(self, chunks: list[StoredChunk]) -> int: ...

    async def search(
        self, embedding: list[float], *, limit: int = 5, min_score: float = 0.0
    ) -> list[Hit]: ...

    async def count(self) -> int: ...


class PgVectorStore:
    """pgvector-backed storage.

    Chunk text and its embedding live in the same row, written in the same
    transaction — the consistency argument that decided ADR-001. A separate
    vector database would make every write a distributed write with no shared
    transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, chunks: list[StoredChunk]) -> int:
        if not chunks:
            return 0
        for chunk in chunks:
            await self._session.execute(
                sql_text(
                    """
                    INSERT INTO chunks
                        (id, document_id, chunk_index, content, heading, embedding, metadata)
                    VALUES
                        (:id, :document_id, :chunk_index, :content, :heading,
                         CAST(:embedding AS vector), CAST(:metadata AS jsonb))
                    ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content,
                        heading = EXCLUDED.heading,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "document_id": str(chunk.document_id),
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "heading": chunk.heading,
                    "embedding": _to_pgvector(chunk.embedding),
                    "metadata": _to_json(chunk.metadata or {}),
                },
            )
        return len(chunks)

    async def search(
        self, embedding: list[float], *, limit: int = 5, min_score: float = 0.0
    ) -> list[Hit]:
        """Nearest neighbours by cosine distance.

        `<=>` is pgvector's cosine distance operator, and the only one the HNSW
        index built with `vector_cosine_ops` can serve. Using a different
        operator here would silently fall back to a sequential scan.
        """
        result = await self._session.execute(
            sql_text(
                """
                SELECT c.id, c.document_id, c.content, c.heading,
                       d.source,
                       1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                ORDER BY c.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {"embedding": _to_pgvector(embedding), "limit": limit},
        )
        return [
            Hit(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content,
                heading=row.heading,
                source=row.source,
                score=float(row.score),
            )
            for row in result
            if float(row.score) >= min_score
        ]

    async def count(self) -> int:
        result = await self._session.execute(sql_text("SELECT count(*) FROM chunks"))
        return int(result.scalar_one())


class InMemoryVectorStore:
    """Exact brute-force search, for tests.

    Deliberately exact rather than approximate: a test asserting "the right chunk
    ranks first" should fail because retrieval is wrong, never because an ANN
    index happened to miss.
    """

    def __init__(self) -> None:
        self._chunks: list[tuple[uuid.UUID, StoredChunk]] = []

    async def upsert(self, chunks: list[StoredChunk]) -> int:
        for chunk in chunks:
            existing = next(
                (
                    index
                    for index, (_, stored) in enumerate(self._chunks)
                    if stored.document_id == chunk.document_id
                    and stored.chunk_index == chunk.chunk_index
                ),
                None,
            )
            if existing is not None:
                self._chunks[existing] = (self._chunks[existing][0], chunk)
            else:
                self._chunks.append((uuid.uuid4(), chunk))
        return len(chunks)

    async def search(
        self, embedding: list[float], *, limit: int = 5, min_score: float = 0.0
    ) -> list[Hit]:
        scored = [
            (
                _cosine_similarity(embedding, stored.embedding),
                chunk_id,
                stored,
            )
            for chunk_id, stored in self._chunks
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            Hit(
                chunk_id=chunk_id,
                document_id=stored.document_id,
                content=stored.content,
                heading=stored.heading,
                source=(stored.metadata or {}).get("source"),
                score=score,
            )
            for score, chunk_id, stored in scored[:limit]
            if score >= min_score
        ]

    async def count(self) -> int:
        return len(self._chunks)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    magnitude = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return 0.0 if magnitude == 0 else dot / magnitude


def _to_pgvector(embedding: list[float]) -> str:
    """pgvector's text input format: '[1.0,2.0,3.0]'."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _to_json(data: dict[str, str]) -> str:
    import json

    return json.dumps(data)


class SessionScopedVectorStore:
    """A store that opens its own short-lived session per operation.

    The retrieval tool is constructed once at startup but called during requests,
    so it cannot hold a session — a session captured at startup would be long
    dead by the first search, and holding one open for the process's lifetime
    would pin a pooled connection forever.
    """

    def __init__(self, session_factory: Any) -> None:
        self._factory = session_factory

    async def upsert(self, chunks: list[StoredChunk]) -> int:
        from amos.database.engine import session_scope

        async with session_scope(self._factory) as session:
            return await PgVectorStore(session).upsert(chunks)

    async def search(
        self, embedding: list[float], *, limit: int = 5, min_score: float = 0.0
    ) -> list[Hit]:
        from amos.database.engine import session_scope

        async with session_scope(self._factory) as session:
            return await PgVectorStore(session).search(embedding, limit=limit, min_score=min_score)

    async def count(self) -> int:
        from amos.database.engine import session_scope

        async with session_scope(self._factory) as session:
            return await PgVectorStore(session).count()
