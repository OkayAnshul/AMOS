"""Write a run's calls and events as they happen (ADR-015).

Before V1.7 every model and tool call was batch-inserted when the run finished,
in one transaction — one shared `created_at`, no task, agent or purpose, and
nothing at all visible while the run was in flight. This writes each one as it
completes, in its own short transaction, the way `RunProgress` writes task
checkpoints (ADR-010).

**Why a contextvar.** The provider and the tool-using agents are built once and
shared by every run; a recorder is per run. `RunService` and the worker open
`recording(...)` around a run, exactly as they set the run id, and everything
underneath finds it with `current_recorder()`. Outside a run — the eval harness,
the trials, a unit test — there is none, and recording is a no-op.

**Recording never fails a run.** A lost row costs one line of the record; a
raised error would cost the answer. Failures are logged and swallowed — but only
`Exception`: a cancellation (task timeout) still propagates, and the timeout is
recorded as an event by the executor instead.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from amos.database.engine import session_scope
from amos.observability import get_call_context, get_current_run_id, log_event
from amos.tools.base import ToolOutcome

logger = logging.getLogger(__name__)


class RunRecorder:
    """Writes llm_calls, tool_calls and run_events rows for the current run."""

    def __init__(self, session_factory: Any) -> None:
        self._factory = session_factory

    @staticmethod
    def _run_id() -> uuid.UUID | None:
        raw = get_current_run_id()
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None

    async def llm_call(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        error: str | None = None,
    ) -> None:
        from amos.database.models import LLMCall

        run_id = self._run_id()
        if run_id is None:
            return
        context = get_call_context()
        await self._write(
            LLMCall(
                id=uuid.uuid4(),
                run_id=run_id,
                provider=provider[:32],
                model=model[:64],
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                repair_attempt=0,
                error=error,
                purpose=context.purpose,
                agent=context.agent,
                task_ref=context.task_ref,
                task_attempt=context.task_attempt,
                run_attempt=context.run_attempt,
            ),
            "llm_call",
        )

    async def tool_call(self, outcome: ToolOutcome) -> None:
        from amos.database.models import ToolCallRow

        run_id = self._run_id()
        if run_id is None:
            return
        context = get_call_context()
        await self._write(
            ToolCallRow(
                id=uuid.uuid4(),
                run_id=run_id,
                call_id=outcome.call_id[:64],
                tool_name=outcome.name[:64],
                arguments=_jsonable(outcome.arguments),
                output=_jsonable(outcome.output) if outcome.output is not None else None,
                status=outcome.status.value,
                error=outcome.error,
                latency_ms=outcome.latency_ms,
                purpose=context.purpose,
                agent=context.agent,
                task_ref=context.task_ref,
                task_attempt=context.task_attempt,
                run_attempt=context.run_attempt,
            ),
            "tool_call",
        )

    async def event(self, kind: str, **payload: Any) -> None:
        from amos.database.models import RunEvent

        run_id = self._run_id()
        if run_id is None:
            return
        context = get_call_context()
        await self._write(
            RunEvent(
                id=uuid.uuid4(),
                run_id=run_id,
                kind=kind[:48],
                task_ref=context.task_ref,
                agent=context.agent,
                task_attempt=context.task_attempt,
                run_attempt=context.run_attempt,
                payload=_jsonable(payload),
            ),
            kind,
        )

    async def _write(self, row: object, what: str) -> None:
        try:
            async with session_scope(self._factory) as session:
                session.add(row)
        except Exception as exc:  # noqa: BLE001 - recording must never fail a run
            log_event(logger, "record.failed", what=what, error=type(exc).__name__)


_current: ContextVar[RunRecorder | None] = ContextVar("run_recorder", default=None)


def current_recorder() -> RunRecorder | None:
    return _current.get()


@contextmanager
def recording(recorder: RunRecorder | None) -> Iterator[None]:
    """Record everything inside the block for the current run."""
    token = _current.set(recorder)
    try:
        yield
    finally:
        _current.reset(token)


async def record_event(kind: str, **payload: Any) -> None:
    """Emit an event if a run is being recorded; otherwise nothing."""
    recorder = current_recorder()
    if recorder is not None:
        await recorder.event(kind, **payload)


def _jsonable(value: Any) -> Any:
    """JSONB-safe: tool outputs can carry non-JSON values (UUIDs, decimals)."""
    return json.loads(json.dumps(value, default=str))
