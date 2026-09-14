"""Ingestion keeps finished documents when a later one fails.

`engineering/bugs-log.md` (2026-09-05) recorded the fix for "rate-limited ingest
discarded all its work" as **one transaction per document**. The code never had
it: the whole directory ran inside the caller's single `session_scope`, so any
failure partway rolled back every document already embedded. It went unnoticed
for nine milestones because pacing stopped the 429s, and no test could see it —
the existing ingest test uses the rollback fixture, which cannot tell a
committed document from an uncommitted one.

These tests therefore commit for real, through the same `session_scope` shape
the CLI uses, and check what survived from a **fresh** session.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.database.engine import session_scope
from amos.database.models import Document
from amos.rag.embeddings import FakeEmbeddings
from amos.rag.ingest import Ingestor
from amos.rag.store import PgVectorStore


class FailsOnCall(FakeEmbeddings):
    """Embeds normally until call `n`, then raises — a 429 partway through."""

    def __init__(self, n: int) -> None:
        super().__init__(dimensions=1536)
        self._fail_on = n
        self.calls = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("429: embedding quota exhausted")
        return await super().embed_documents(texts)


def write_corpus(directory: Path, count: int) -> list[str]:
    """Distinct documents with unique content, so hashes cannot collide with
    anything else in the database — including a real ingest running beside the
    suite."""
    tag = uuid.uuid4().hex[:10]
    names = []
    for i in range(count):
        name = f"{tag}-{i}.md"
        body = f"unique passage {tag} number {i} about transaction boundaries " * 30
        (directory / name).write_text(f"# Document {i}\n\n## Section\n\n{body}\n")
        names.append(name)
    return names


async def sources_present(factory: async_sessionmaker[AsyncSession], names: list[str]) -> list[str]:
    async with session_scope(factory) as fresh:
        rows = await fresh.execute(
            select(Document.source).where(Document.source.in_(names)).order_by(Document.source)
        )
        return list(rows.scalars().all())


async def remove(factory: async_sessionmaker[AsyncSession], names: list[str]) -> None:
    async with session_scope(factory) as session:
        await session.execute(delete(Document).where(Document.source.in_(names)))


async def test_documents_finished_before_a_failure_survive_it(
    tmp_path: Path, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The failure costs the document in progress, not the run."""
    names = write_corpus(tmp_path, 4)
    try:
        with pytest.raises(RuntimeError, match="429"):
            async with session_scope(db_factory) as session:
                await Ingestor(session, PgVectorStore(session), FailsOnCall(3)).ingest_directory(
                    tmp_path
                )

        assert await sources_present(db_factory, names) == names[:2], (
            "documents embedded before the failure were rolled back with it"
        )
    finally:
        await remove(db_factory, names)


async def test_a_rerun_after_a_failure_resumes_instead_of_repeating(
    tmp_path: Path, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The payoff of the boundary: finished documents are skipped by content
    hash, so the rerun spends embedding quota only on what is left."""
    names = write_corpus(tmp_path, 4)
    try:
        with pytest.raises(RuntimeError):
            async with session_scope(db_factory) as session:
                await Ingestor(session, PgVectorStore(session), FailsOnCall(3)).ingest_directory(
                    tmp_path
                )

        embeddings = FakeEmbeddings(dimensions=1536)
        async with session_scope(db_factory) as session:
            report = await Ingestor(session, PgVectorStore(session), embeddings).ingest_directory(
                tmp_path
            )

        assert report.documents_skipped == 2
        assert report.documents_ingested == 2
        assert len(embeddings.document_calls) == 2, "the rerun re-embedded finished documents"
        assert await sources_present(db_factory, names) == names
    finally:
        await remove(db_factory, names)
