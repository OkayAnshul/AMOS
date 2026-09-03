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
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
        Index(
            "idx_runs_idempotency",
            "idempotency_key",
            postgresql_where=idempotency_key.isnot(None),
        ),
        Index("idx_runs_created", "created_at"),
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
        UniqueConstraint("run_id", "attempt", name="steps_run_attempt_unique"),
        Index("idx_steps_run", "run_id"),
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
