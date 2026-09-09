"""SQLAlchemy models — the durable shape of an execution.

These mirror `docs/05-data-model.md`. The important property is that they are
*not a new design*: `AgentResult`, `LLMCallRecord` and `ToolOutcome` were shaped
at V0.1 and V0.2 to become these rows, so persistence is a serialisation change
rather than a redesign.

Note `run_id` on `llm_calls` and `tool_calls` alongside `step_id`. That is a
deliberate denormalisation: assembling a full run trace is the most common query
in the system, and carrying `run_id` turns a four-table join into one filter.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base with a constraint naming convention.

    Without this, SQLAlchemy lets the database invent constraint names, and
    Alembic then autogenerates a downgrade that says
    `DROP CONSTRAINT ... it has no name` — the migration applies but cannot be
    reversed. An irreversible migration is a one-way door, and you discover it
    only when you need to go back.

    Deterministic names make every constraint droppable by name from any
    migration, on any database built from these models.
    """

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Run(Base):
    """One execution attempt of a goal. The unit of tracing."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Nullable + unique: most runs have no key, so the index is partial.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Episodic memory (V0.6). Deliberately columns on `runs` rather than an
    # `episodes` table: an episode IS a run, and a separate table would duplicate
    # goal, status, tokens and timings to add only an embedding and a lesson.
    lesson: Mapped[str | None] = mapped_column(Text, nullable=True)
    # V0.8: run-level claiming. A worker takes a whole run; the DAG's internal
    # concurrency is already handled inside the executor.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # NOTE: `runs.goal_embedding vector(1536)` exists in the database but is
    # deliberately absent here — SQLAlchemy core has no `vector` type. It is read
    # and written through raw SQL in `amos.memory.episodic`, and
    # `test_models_match_the_database_schema` allowlists it explicitly so the
    # drift check stays meaningful.

    tasks: Mapped[list[Task]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Task.position"
    )
    steps: Mapped[list[Step]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Step.attempt"
    )
    llm_calls: Mapped[list[LLMCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="LLMCall.created_at"
    )
    tool_calls: Mapped[list[ToolCallRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ToolCallRow.created_at"
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="runs_idempotency_unique"),
        # The index the worker claims through. Partial, because QUEUED runs are a
        # small and shrinking fraction of all runs — an index over every run ever
        # executed would be mostly dead weight on the hot path.
        Index(
            "idx_runs_claimable",
            "status",
            "created_at",
            postgresql_where=(status == "QUEUED"),
        ),
        Index(
            "idx_runs_idempotency",
            "idempotency_key",
            postgresql_where=idempotency_key.isnot(None),
        ),
        Index("idx_runs_created", "created_at"),
    )


class Memory(Base):
    """A durable fact about the user or the world (V0.6, semantic memory).

    ## Why this is a relational table and not just vectors

    The obvious move for "remember things" in an AI system is to embed everything
    and search by similarity. That is wrong for facts, for three reasons this
    schema encodes:

    1. **Exact recall is a key lookup.** "What is the user's name?" should return
       *the* name, not the most similar-looking fact. Similarity search will
       occasionally hand back someone else's name with high confidence.
    2. **Contradictions need ordering.** When a fact changes, the old one must
       stop being current. Vector search has no notion of superseded.
    3. **Provenance needs joins.** "Where did this come from?" is a foreign key
       to a run, not a nearest neighbour.

    So `subject` carries a normalised key for exact lookup and contradiction
    detection, `embedding` supports similarity for questions that have no key,
    and `superseded_by` makes "current" a deterministic query rather than a
    ranking. Superseded rows are **kept**, so the history of a changed fact is
    auditable.
    """

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    # A chain, not a delete: the previous value of a changed fact stays
    # inspectable, and "current" is `superseded_by IS NULL`.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Partial: almost every query wants only current facts, and superseded
        # rows accumulate without ever being read by the hot path.
        Index(
            "idx_memories_current",
            "subject",
            postgresql_where=(superseded_by.is_(None)),
        ),
        Index("idx_memories_source_run", "source_run_id"),
    )


class Document(Base):
    """An ingested source document (V0.5).

    `content_hash` is UNIQUE so re-ingesting unchanged content is a no-op rather
    than a duplicate. Without it, running ingestion twice doubles the corpus and
    every retrieval starts returning the same chunk twice.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="documents_content_hash_unique"),
        Index("idx_documents_source", "source"),
    )


class Task(Base):
    """One unit of work within a run, produced by the planner (V0.4).

    `depends_on` is a Postgres UUID[] rather than a join table. The alternative
    — a `task_dependencies` table — is the textbook normalisation, but every read
    of this graph loads the whole run's tasks at once anyway, so the join buys
    nothing and costs a table. Postgres arrays are first-class here.

    `plan_ref` keeps the planner's own symbolic id ("t1") so a stored task can be
    traced back to the plan text the model produced.
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    plan_ref: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    depends_on: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Claimed-at supports the V0.8 visibility timeout. Present now because
    # adding a column later to a table with rows is a migration; adding it now
    # is a default.

    run: Mapped[Run] = relationship(back_populates="tasks")

    __table_args__ = (
        UniqueConstraint("run_id", "plan_ref", name="tasks_run_planref_unique"),
        Index("idx_tasks_run_state", "run_id", "state"),
        # There used to be a `claimed_at` column and a partial claimable index
        # here, added at V0.4 with the comment "present now because adding a
        # column later to a table with rows is a migration".
        #
        # V0.8 removed both. The reasoning was sound and the guess was wrong:
        # claiming happens at the RUN level, not the task level, because a run is
        # what a client submits and polls, and a run's internal task concurrency
        # is already handled inside one worker. The speculative column did not
        # merely go unused — it anticipated the wrong granularity.
    )


class Step(Base):
    """One attempt at doing the run's work.

    Separate from Run because a retried unit of work has several attempts, and
    collapsing them would destroy the evidence of what actually happened. At
    V0.3 there is exactly one step per run; V0.4's planner creates many.
    """

    __tablename__ = "steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: V0.3 runs have steps with no task. From V0.4 every step belongs
    # to one, and a task with three attempts has three steps — which is what
    # makes "was this retried?" answerable from the data.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="steps")

    __table_args__ = (
        # Was UNIQUE(run_id, attempt) at V0.3, when a run had exactly one step.
        # With a task DAG, several tasks legitimately share attempt number 0.
        UniqueConstraint("task_id", "attempt", name="steps_task_attempt_unique"),
        Index("idx_steps_run", "run_id"),
        Index("idx_steps_task", "task_id"),
    )


class LLMCall(Base):
    """One provider call. Columns match `LLMCallRecord` from V0.1."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repair_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[Run] = relationship(back_populates="llm_calls")

    __table_args__ = (Index("idx_llm_calls_run", "run_id"),)


class ToolCallRow(Base):
    """One tool invocation. Columns match `ToolOutcome` from V0.2.

    Named ToolCallRow because `ToolCall` is already the domain object for a
    *requested* call; this is the record of an *attempted* one.
    """

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), nullable=True
    )
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[Run] = relationship(back_populates="tool_calls")

    __table_args__ = (Index("idx_tool_calls_run", "run_id"),)
