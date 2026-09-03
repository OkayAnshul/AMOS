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
from contextvars import ContextVar
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


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
