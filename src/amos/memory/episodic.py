"""Episodic memory: what happened on previous runs.

## Why there is no `episodes` table

An episode **is** a run. It has a goal, an outcome, a token cost and a duration —
all of which `runs` already stores. A separate table would duplicate every one of
those to add an embedding and a lesson.

So episodic memory is two columns on `runs` (`goal_embedding`, `lesson`) plus the
queries in this module. **It is an index on an existing store, not a new store.**

This is the same instinct that kept AMOS on one database: before adding a place to
put things, check whether the thing already has a place.

## What it is for

Answering "have I done something like this before, and how did it go?" before
starting work. That is different from RAG: knowledge memory retrieves *facts from
documents*, episodic memory retrieves *the system's own experience*.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.observability import log_event
from amos.rag.embeddings import EmbeddingProvider
from amos.rag.store import _to_pgvector

logger = logging.getLogger(__name__)


@dataclass
class Episode:
    run_id: uuid.UUID
    goal: str
    status: str
    lesson: str | None
    total_tokens: int
    score: float | None = None


class EpisodicMemory:
    """Records and recalls past run experience."""

    def __init__(self, session: AsyncSession, embeddings: EmbeddingProvider) -> None:
        self._session = session
        self._embeddings = embeddings

    async def record(self, run_id: uuid.UUID, goal: str, lesson: str | None = None) -> None:
        """Make a completed run findable by similarity.

        Called after the run finishes, not before: the lesson is only knowable
        once the outcome is.
        """
        embedding = await self._embeddings.embed_documents([goal])
        await self._session.execute(
            sql_text(
                "UPDATE runs SET goal_embedding = CAST(:embedding AS vector), "
                "lesson = COALESCE(:lesson, lesson) WHERE id = :run_id"
            ),
            {
                "embedding": _to_pgvector(embedding[0]),
                "lesson": lesson,
                "run_id": str(run_id),
            },
        )
        log_event(logger, "episode.recorded", run_id=str(run_id), has_lesson=lesson is not None)

    async def recall_similar(
        self,
        goal: str,
        *,
        limit: int = 3,
        min_score: float = 0.5,
        exclude_run_id: uuid.UUID | None = None,
    ) -> list[Episode]:
        """Find past runs with similar goals.

        `exclude_run_id` matters: the current run is already in the table by the
        time this is called (V0.3 writes the row before executing), so without it
        the most similar past experience is always the run asking the question.
        """
        embedding = await self._embeddings.embed_query(goal)
        result = await self._session.execute(
            sql_text(
                """
                SELECT id, goal_text, status, lesson, total_tokens,
                       1 - (goal_embedding <=> CAST(:embedding AS vector)) AS score
                FROM runs
                WHERE goal_embedding IS NOT NULL
                  -- Cast on BOTH uses: with a NULL parameter Postgres cannot
                  -- infer the type of a bare `:exclude IS NULL` and errors with
                  -- AmbiguousParameterError.
                  AND (CAST(:exclude AS uuid) IS NULL OR id <> CAST(:exclude AS uuid))
                ORDER BY goal_embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {
                "embedding": _to_pgvector(embedding),
                "limit": limit,
                "exclude": str(exclude_run_id) if exclude_run_id else None,
            },
        )
        return [
            Episode(
                run_id=row.id,
                goal=row.goal_text,
                status=row.status,
                lesson=row.lesson,
                total_tokens=row.total_tokens,
                score=float(row.score),
            )
            for row in result
            if float(row.score) >= min_score
        ]
