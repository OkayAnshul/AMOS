"""Persistence wiring for the API layer.

Kept separate from `app.py` so the request handler stays about HTTP, and so
tests can build an app with or without a database.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.schemas import (
    AgentResult,
    RunTrace,
    TraceLLMCall,
    TraceStep,
    TraceTask,
    TraceToolCall,
)
from amos.database.engine import session_scope
from amos.database.models import Run
from amos.database.repository import RunRepository
from amos.errors import AmosError
from amos.observability import log_event

logger = logging.getLogger(__name__)


class RunService:
    """Runs a goal and records what happened.

    The ordering is the design: the run row is written *before* execution and
    updated after. A crash mid-execution therefore leaves evidence that the run
    was attempted, which is precisely the case worth investigating.
    """

    def __init__(
        self,
        agent: object,
        session_factory: async_sessionmaker[AsyncSession] | None,
    ) -> None:
        self._agent = agent
        self._factory = session_factory

    @property
    def persistence_enabled(self) -> bool:
        return self._factory is not None

    async def execute(
        self, goal: str, request_id: str, idempotency_key: str | None = None
    ) -> tuple[AgentResult, uuid.UUID | None]:
        if self._factory is None:
            result = await self._agent.run(goal)  # type: ignore[attr-defined]
            return result, None

        # 1. Idempotency check, in its own transaction.
        if idempotency_key:
            async with session_scope(self._factory) as session:
                existing = await RunRepository(session).find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    log_event(
                        logger,
                        "run.deduplicated",
                        run_id=str(existing.id),
                        idempotency_key=idempotency_key,
                    )
                    return _result_from_run(existing), existing.id

        # 2. Record the attempt before doing it.
        async with session_scope(self._factory) as session:
            run = await RunRepository(session).create_run(
                goal=goal, request_id=request_id, idempotency_key=idempotency_key
            )
            run_id = run.id

        # 3. Execute outside any transaction — an LLM call can take seconds and
        #    holding a database connection open across it would exhaust the pool.
        try:
            result = await self._agent.run(goal)  # type: ignore[attr-defined]
        except AmosError as exc:
            async with session_scope(self._factory) as session:
                repo = RunRepository(session)
                run = await repo.get_trace(run_id)  # type: ignore[assignment]
                if run is not None:
                    await repo.record_failure(run, type(exc).__name__, exc.message)
            raise

        # 4. Record the outcome.
        async with session_scope(self._factory) as session:
            repo = RunRepository(session)
            stored = await repo.get_trace(run_id)
            if stored is not None:
                await repo.record_success(stored, result)

        log_event(logger, "run.persisted", run_id=str(run_id))
        return result, run_id

    async def get_trace(self, run_id: uuid.UUID) -> RunTrace | None:
        if self._factory is None:
            return None
        async with session_scope(self._factory) as session:
            run = await RunRepository(session).get_trace(run_id)
            return _to_trace(run) if run is not None else None


def _result_from_run(run: Run) -> AgentResult:
    """Rebuild an AgentResult from stored rows, for a deduplicated request."""
    from amos.agents.schemas import AgentResponse

    payload = run.result or {}
    response = (
        AgentResponse.model_validate(payload)
        if payload
        else AgentResponse(
            answer="(no result recorded)",
            reasoning="This run did not complete.",
            confidence="low",  # type: ignore[arg-type]
            caveats=["Returned from a previously recorded run."],
        )
    )
    return AgentResult(
        request_id=run.request_id or "",
        response=response,
        total_tokens=run.total_tokens,
        latency_ms=run.latency_ms,
    )


def _to_trace(run: Run) -> RunTrace:
    return RunTrace(
        run_id=str(run.id),
        goal=run.goal_text,
        status=run.status,
        request_id=run.request_id,
        result=run.result,
        error=run.error,
        total_tokens=run.total_tokens,
        latency_ms=run.latency_ms,
        created_at=run.created_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        tasks=[
            TraceTask(
                plan_ref=t.plan_ref,
                description=t.description,
                state=t.state,
                depends_on=[str(d) for d in (t.depends_on or [])],
                attempt_count=t.attempt_count,
                error=(t.error or {}).get("message") if t.error else None,
            )
            for t in run.tasks
        ],
        steps=[
            TraceStep(
                attempt=s.attempt,
                status=s.status,
                agent_name=s.agent_name,
                error=s.error,
            )
            for s in run.steps
        ],
        llm_calls=[
            TraceLLMCall(
                provider=c.provider,
                model=c.model,
                prompt_tokens=c.prompt_tokens,
                output_tokens=c.output_tokens,
                latency_ms=c.latency_ms,
                repair_attempt=c.repair_attempt,
                error=c.error,
            )
            for c in run.llm_calls
        ],
        tool_calls=[
            TraceToolCall(
                tool_name=t.tool_name,
                arguments=t.arguments or {},
                status=t.status,
                output=t.output,
                error=t.error,
                latency_ms=t.latency_ms,
            )
            for t in run.tool_calls
        ],
    )
