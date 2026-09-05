"""Ingestion: documents in, embedded chunks out.

    parse → chunk → hash → embed → store

The content hash is the interesting part. Re-running ingestion on an unchanged
document is a **no-op**, not a duplicate. Without it, ingesting twice doubles the
corpus and retrieval starts returning the same passage repeatedly — which looks
like a relevance problem and is actually a bookkeeping one.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.observability import log_event
from amos.rag.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_markdown
from amos.rag.embeddings import EmbeddingProvider
from amos.rag.store import StoredChunk, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    documents_ingested: int = 0
    documents_skipped: int = 0
    chunks_created: int = 0

    @property
    def total_documents(self) -> int:
        return self.documents_ingested + self.documents_skipped


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Ingestor:
    """Runs the ingestion pipeline."""

    def __init__(
        self,
        session: AsyncSession,
        store: VectorStore,
        embeddings: EmbeddingProvider,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> None:
        self._session = session
        self._store = store
        self._embeddings = embeddings
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def ingest_text(
        self, *, source: str, text: str, title: str | None = None
    ) -> tuple[uuid.UUID | None, int]:
        """Ingest one document. Returns (document_id, chunks) or (None, 0) if unchanged."""
        digest = content_hash(text)

        existing = await self._session.execute(
            sql_text("SELECT id FROM documents WHERE content_hash = :hash"),
            {"hash": digest},
        )
        if existing.first() is not None:
            log_event(logger, "ingest.skipped_unchanged", source=source)
            return None, 0

        chunks = chunk_markdown(
            text, chunk_size=self._chunk_size, overlap=self._overlap, source=source
        )
        if not chunks:
            log_event(logger, "ingest.no_chunks", source=source)
            return None, 0

        document_id = uuid.uuid4()
        await self._session.execute(
            sql_text(
                """
                INSERT INTO documents (id, source, title, content_hash, chunk_count, metadata)
                VALUES (:id, :source, :title, :hash, :count, '{}'::jsonb)
                """
            ),
            {
                "id": str(document_id),
                "source": source,
                "title": title or source,
                "hash": digest,
                "count": len(chunks),
            },
        )

        vectors = await self._embeddings.embed_documents([c.content for c in chunks])
        await self._store.upsert(
            [
                StoredChunk(
                    document_id=document_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    embedding=vector,
                    heading=chunk.heading,
                    metadata={"source": source},
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )
        log_event(logger, "ingest.completed", source=source, chunks=len(chunks))
        return document_id, len(chunks)

    async def ingest_directory(self, directory: Path, *, pattern: str = "*.md") -> IngestReport:
        """Ingest every matching file, sorted for reproducibility."""
        report = IngestReport()
        root = Path(directory).resolve()

        for path in sorted(root.rglob(pattern)):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = str(path.relative_to(root))
            document_id, chunks = await self.ingest_text(
                source=relative, text=text, title=path.stem
            )
            if document_id is None:
                report.documents_skipped += 1
            else:
                report.documents_ingested += 1
                report.chunks_created += chunks
        return report
