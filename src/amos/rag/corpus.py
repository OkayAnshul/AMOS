"""What a user can see of the corpus: the system documents and their own (V1.7).

The same rule as retrieval (ADR-013): `user_id IS NULL OR user_id = :owner`.
Every statement in this file carries it, and a source-scan test asserts so —
the V1.6 lesson that isolation enforced "where queries are built" must name
every file a query is built in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class DocumentInfo:
    id: uuid.UUID
    source: str
    title: str | None
    chunk_count: int
    created_at: datetime
    owned: bool


@dataclass
class ChunkInfo:
    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    heading: str | None
    content: str
    source: str
    title: str | None
    owned: bool


async def list_documents(session: AsyncSession, owner: uuid.UUID) -> list[DocumentInfo]:
    result = await session.execute(
        sql_text(
            "SELECT id, source, title, chunk_count, created_at, user_id IS NOT NULL AS owned "
            "FROM documents WHERE (user_id IS NULL OR user_id = :owner) "
            "ORDER BY owned DESC, source"
        ),
        {"owner": str(owner)},
    )
    return [
        DocumentInfo(
            id=row.id,
            source=row.source,
            title=row.title,
            chunk_count=row.chunk_count,
            created_at=row.created_at,
            owned=bool(row.owned),
        )
        for row in result
    ]


async def get_chunk(
    session: AsyncSession, owner: uuid.UUID, chunk_id: uuid.UUID
) -> ChunkInfo | None:
    """One passage, for a citation card. Another user's chunk is not found."""
    row = (
        await session.execute(
            sql_text(
                "SELECT c.id, c.document_id, c.chunk_index, c.heading, c.content, "
                "d.source, d.title, d.user_id IS NOT NULL AS owned "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.id = :id AND (d.user_id IS NULL OR d.user_id = :owner)"
            ),
            {"id": str(chunk_id), "owner": str(owner)},
        )
    ).first()
    if row is None:
        return None
    return ChunkInfo(
        id=row.id,
        document_id=row.document_id,
        chunk_index=row.chunk_index,
        heading=row.heading,
        content=row.content,
        source=row.source,
        title=row.title,
        owned=bool(row.owned),
    )


async def corpus_totals(session: AsyncSession, owner: uuid.UUID) -> dict[str, int]:
    row = (
        await session.execute(
            sql_text(
                "SELECT count(*) FILTER (WHERE user_id IS NULL) AS system_documents, "
                "coalesce(sum(chunk_count) FILTER (WHERE user_id IS NULL), 0) AS system_chunks, "
                "count(*) FILTER (WHERE user_id = :owner) AS your_documents, "
                "coalesce(sum(chunk_count) FILTER (WHERE user_id = :owner), 0) AS your_chunks "
                "FROM documents WHERE (user_id IS NULL OR user_id = :owner)"
            ),
            {"owner": str(owner)},
        )
    ).one()
    return {
        "system_documents": int(row.system_documents),
        "system_chunks": int(row.system_chunks),
        "your_documents": int(row.your_documents),
        "your_chunks": int(row.your_chunks),
    }
