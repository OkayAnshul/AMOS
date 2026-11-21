"""Structured JSON logging with a request id threaded through every line.

The request id is the V0.1 seam that becomes the OpenTelemetry trace_id at V0.9.
It costs almost nothing now and makes every later milestone's debugging possible,
so it is built on day one rather than retrofitted.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

#: The run currently executing. Mirrors the request id above, and exists for the
#: same reason: something deep in the stack needs it and threading it through
#: every signature would be worse.
#:
#: Concretely, `RememberFactTool` is constructed once at startup — before any run
#: exists — so it cannot be given a run id as a constructor argument. Without
#: this, `memories.source_run_id` was NULL for every row and "where did this fact
#: come from?" was unanswerable, which is one of the three reasons
#: docs/09-memory-architecture.md gives for memory being relational at all.
_current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def set_current_run_id(run_id: str | None) -> None:
    _current_run_id.set(run_id)


def get_current_run_id() -> str | None:
    return _current_run_id.get()


@dataclass(frozen=True)
class CallContext:
    """Who is making a model or tool call, and why (ADR-015).

    The run id is NOT here: it has one source, `get_current_run_id`, already read
    by the plan store, the checkpoints and the memory tools. A second copy would
    be a second thing to keep in step.

    `purpose` is one of: planner · router · agent · revision · critic ·
    synthesis · delegate · reconciler.
    """

    purpose: str | None = None
    agent: str | None = None
    task_ref: str | None = None
    task_attempt: int = 0
    run_attempt: int = 0


_EMPTY_CONTEXT = CallContext()
_call_context: ContextVar[CallContext | None] = ContextVar("call_context", default=None)


def get_call_context() -> CallContext:
    return _call_context.get() or _EMPTY_CONTEXT


@contextmanager
def call_scope(**changes: Any) -> Iterator[CallContext]:
    """Change the call context for a block, and **put it back** afterwards.

    Set-and-reset rather than a bare `.set()`, because `asyncio.wait_for` runs
    its coroutine in the *same* task on Python 3.14 and copies no context: a
    `.set()` inside an agent run would leak out to its caller and label every
    later call with the wrong agent and purpose. `asyncio.gather` does copy, so a
    scope opened inside each gathered coroutine stays with that coroutine.
    """
    scoped = replace(get_call_context(), **changes)
    token = _call_context.set(scoped)
    try:
        yield scoped
    finally:
        _call_context.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, request id attached automatically."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if (rid := get_request_id()) is not None:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything passed via logger.info(..., extra={"fields": {...}})
        if extra := getattr(record, "fields", None):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn's own access log duplicates ours in a different format
    logging.getLogger("uvicorn.access").disabled = True


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    """Log a message with structured fields."""
    logger.info(message, extra={"fields": fields})
