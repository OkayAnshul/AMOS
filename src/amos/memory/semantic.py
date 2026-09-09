"""Semantic memory: durable facts about the user and the world.

## The decision this module exists to embody

The reflexive design for "make the AI remember things" is to embed every fact and
retrieve by similarity. That is the wrong default, and this module is built the
other way round: **relational first, vectors only where similarity is genuinely
the right question.**

Three reasons, each of which shows up in the code below:

1. **Exact recall is a key lookup.** "What is the user's name?" must return *the*
   name. Similarity search returns the most similar-looking fact — which, in a
   store containing several names, is sometimes the wrong person's.
2. **Contradictions need ordering.** When a fact changes, the previous value must
   stop being current. Vector search has no notion of superseded; it will happily
   return both the old and new value, ranked by cosine distance, with no way to
   tell which is true.
3. **Provenance is a join**, not a nearest neighbour.

So: `subject` is a normalised key used for exact lookup and contradiction
detection; the embedding is a *secondary* index for questions that have no key.

## Contradiction resolution is deterministic

When a fact arrives for a subject that already has one, the previous row is
marked `superseded_by` the new one. Newest wins — a rule, not a judgement, and
specifically **not** something the model decides. Superseded rows are kept, so
the history of a changed fact stays auditable.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.observability import log_event
from amos.rag.embeddings import EmbeddingProvider
from amos.rag.store import _to_pgvector

logger = logging.getLogger(__name__)

_NON_KEY = re.compile(r"[^a-z0-9]+")


def normalise_subject(subject: str) -> str:
    """Reduce a subject to a stable key.

    "User's Name", "user name" and "USER_NAME" must collide, or the same fact
    stored twice with different capitalisation produces two 'current' values and
    contradiction resolution silently stops working.
    """
    return _NON_KEY.sub("_", subject.strip().lower()).strip("_")


@dataclass
class Fact:
    id: uuid.UUID
    subject: str
    content: str
    confidence: float
    superseded: bool = False
    score: float | None = None


class SemanticMemory:
    """Stores and recalls durable facts."""

    def __init__(self, session: AsyncSession, embeddings: EmbeddingProvider) -> None:
        self._session = session
        self._embeddings = embeddings

    async def remember(
        self,
        subject: str,
        content: str,
        *,
        confidence: float = 1.0,
        source_run_id: uuid.UUID | None = None,
    ) -> Fact:
        """Store a fact, superseding any current fact on the same subject."""
        key = normalise_subject(subject)
        if not key:
            raise ValueError("subject must contain at least one alphanumeric character")

        embedding = await self._embeddings.embed_documents([f"{subject}: {content}"])
        new_id = uuid.uuid4()

        # INSERT before UPDATE. The reverse order looks safer — supersede the old
        # row first, so there is never a moment with two current facts — but it
        # cannot work: `superseded_by` is a foreign key to `memories.id`, and the
        # new row does not exist yet. Postgres rejects it outright.
        #
        # Insert-then-update is also genuinely safe, which the original reasoning
        # missed. Both statements run in one transaction, so no other transaction
        # ever observes the intermediate state where two rows are current.
        await self._session.execute(
            sql_text(
                """
                INSERT INTO memories
                    (id, subject, content, confidence, source_run_id, embedding)
                VALUES
                    (:id, :subject, :content, :confidence, :run_id,
                     CAST(:embedding AS vector))
                """
            ),
            {
                "id": str(new_id),
                "subject": key,
                "content": content,
                "confidence": confidence,
                "run_id": str(source_run_id) if source_run_id else None,
                "embedding": _to_pgvector(embedding[0]),
            },
        )

        # `id <> :new_id` matters: without it the new row supersedes itself and
        # the subject ends up with zero current facts.
        result = await self._session.execute(
            sql_text(
                "UPDATE memories SET superseded_by = :new_id "
                "WHERE subject = :subject AND superseded_by IS NULL "
                "AND id <> :new_id "
                "RETURNING id"
            ),
            {"new_id": str(new_id), "subject": key},
        )
        superseded = [row.id for row in result]
        log_event(
            logger,
            "memory.remembered",
            subject=key,
            superseded=len(superseded),
        )
        return Fact(id=new_id, subject=key, content=content, confidence=confidence)

    async def recall_exact(self, subject: str) -> Fact | None:
        """Look a fact up by subject. Deterministic — no ranking involved."""
        result = await self._session.execute(
            sql_text(
                "SELECT id, subject, content, confidence FROM memories "
                "WHERE subject = :subject AND superseded_by IS NULL"
            ),
            {"subject": normalise_subject(subject)},
        )
        row = result.first()
        if row is None:
            return None
        return Fact(id=row.id, subject=row.subject, content=row.content, confidence=row.confidence)

    async def recall_similar(
        self, query: str, *, limit: int = 5, min_score: float = 0.3
    ) -> list[Fact]:
        """Find facts related to a question that has no exact subject key.

        The secondary path. Used when the caller does not know what to ask for —
        never as a substitute for `recall_exact` when a key is available.
        """
        embedding = await self._embeddings.embed_query(query)
        result = await self._session.execute(
            sql_text(
                """
                SELECT id, subject, content, confidence,
                       1 - (embedding <=> CAST(:embedding AS vector)) AS score
                FROM memories
                WHERE superseded_by IS NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {"embedding": _to_pgvector(embedding), "limit": limit},
        )
        return [
            Fact(
                id=row.id,
                subject=row.subject,
                content=row.content,
                confidence=row.confidence,
                score=float(row.score),
            )
            for row in result
            if float(row.score) >= min_score
        ]

    async def history(self, subject: str) -> list[Fact]:
        """Every value this subject has held, newest first.

        Possible only because supersession is a chain rather than a delete.
        """
        result = await self._session.execute(
            sql_text(
                "SELECT id, subject, content, confidence, superseded_by "
                "FROM memories WHERE subject = :subject ORDER BY created_at DESC"
            ),
            {"subject": normalise_subject(subject)},
        )
        return [
            Fact(
                id=row.id,
                subject=row.subject,
                content=row.content,
                confidence=row.confidence,
                superseded=row.superseded_by is not None,
            )
            for row in result
        ]

    async def count_current(self) -> int:
        result = await self._session.execute(
            sql_text("SELECT count(*) FROM memories WHERE superseded_by IS NULL")
        )
        return int(result.scalar_one())
