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
    documents_excluded: int = 0
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
        self,
        *,
        source: str,
        text: str,
        title: str | None = None,
        owner: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID | None, int]:
        """Ingest one document. Returns (document_id, chunks) or (None, 0) if unchanged.

        `owner` makes it a user's upload (V1.7) rather than system corpus: owned,
        retrievable only by that user (ADR-013), and replaced only among that
        user's own documents — an upload named like a corpus file cannot delete
        it. The content hash is namespaced by owner, because `content_hash` is
        unique across the table and identical text uploaded by two users is two
        documents, not one.
        """
        digest = content_hash(text if owner is None else f"{owner}\n{text}")

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
                INSERT INTO documents
                    (id, user_id, source, title, content_hash, chunk_count, metadata)
                VALUES (:id, CAST(:owner AS uuid), :source, :title, :hash, :count, '{}'::jsonb)
                """
            ),
            {
                "id": str(document_id),
                "owner": str(owner) if owner is not None else None,
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
        # A changed file is a new version of the same source, not a second
        # document. Until V1.5 the old version stayed indexed beside the new one,
        # so an edited doc answered questions with both its old and new text.
        # Deleted in the same transaction as the insert: if embedding the new
        # version fails, the old one is still there. Chunks go by FK cascade.
        # Scoped to the system corpus (user_id NULL), never a user's upload.
        replaced = await self._session.execute(
            sql_text(
                "DELETE FROM documents WHERE source = :source AND id <> :id "
                "AND user_id IS NOT DISTINCT FROM CAST(:owner AS uuid)"
            ),
            {
                "source": source,
                "id": str(document_id),
                "owner": str(owner) if owner is not None else None,
            },
        )
        log_event(
            logger,
            "ingest.completed",
            source=source,
            chunks=len(chunks),
            replaced_versions=getattr(replaced, "rowcount", 0),
        )
        return document_id, len(chunks)

    async def ingest_directory(
        self, directory: Path, *, pattern: str = "*.md", exclude: tuple[str, ...] = ()
    ) -> IngestReport:
        """Ingest every matching file, sorted for reproducibility.

        `exclude` names directories (at any depth below `directory`) or files
        that are not ingested — the corpus scope (`amos.rag.cli.CORPUS_EXCLUDE`).

        **Commits after every document.** A failure then costs the document in
        progress, not the run, and re-running skips everything already committed
        through the content hash — so an interrupted ingest resumes.

        This was recorded as the V0.5 fix for "rate-limited ingest discarded all
        its work" (`engineering/bugs-log.md`, 2026-09-05) and **was never
        implemented**: until 2026-09-14 the whole directory ran in the caller's
        single transaction. It went unnoticed because the other half of that fix
        — pacing and honouring `retryDelay` — stopped the 429s, so nothing ever
        failed partway again to expose the missing boundary.
        """
        report = IngestReport()
        root = Path(directory).resolve()

        for path in sorted(root.rglob(pattern)):
            if not path.is_file():
                continue
            if set(path.relative_to(root).parts) & set(exclude):
                report.documents_excluded += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = str(path.relative_to(root))
            document_id, chunks = await self.ingest_text(
                source=relative, text=text, title=path.stem
            )
            if document_id is None:
                report.documents_skipped += 1
            else:
                # The transaction boundary follows the unit of useful work: one
                # embedded document. The caller's session_scope still commits at
                # the end and rolls back the document in progress on failure.
                await self._session.commit()
                report.documents_ingested += 1
                report.chunks_created += chunks
        return report
