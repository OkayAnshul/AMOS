"""Tool abstraction.

A tool is a deterministic capability an agent may invoke. Unlike `LLMProvider`
(a Protocol), `Tool` is an abstract base class, because tools genuinely share
behaviour: every one must validate its arguments and honour its timeout.

That shared behaviour lives in `execute()`, which is concrete and final in
practice; subclasses implement `_run()`. A tool therefore **cannot opt out of
validation or timeouts** — it has no opportunity to. Making those guarantees the
base class's job rather than each author's is the whole point of the design.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from amos.errors import ToolTimeoutError, ToolValidationError


class Permission(StrEnum):
    """What a tool is allowed to touch.

    Ordered by blast radius. V0.2 implements only the first three: every tool is
    deterministic and reversible. WRITE and DESTRUCTIVE exist to name the
    boundary AMOS has not crossed — they require the approval workflow described
    in docs/13-security.md, which is not built.
    """

    PURE = "pure"  # no I/O, no side effects
    READ_LOCAL = "read_local"  # reads the local filesystem, sandboxed
    NETWORK_READ = "network_read"  # outbound HTTP GET, allowlisted
    WRITE = "write"  # NOT IMPLEMENTED — needs approval workflow
    DESTRUCTIVE = "destructive"  # NOT IMPLEMENTED — needs human approval


class ToolStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    TIMEOUT = "timeout"
    DENIED = "denied"
    ERROR = "error"


class ToolCall(BaseModel):
    """A tool invocation requested by the model. Provider-agnostic."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolOutcome(BaseModel):
    """The result of attempting a tool call.

    Every outcome — including failures — is fed back to the model, so it can
    correct itself rather than being left to guess why nothing happened. These
    fields are the `tool_calls` table columns from docs/05-data-model.md.
    """

    call_id: str
    name: str
    status: ToolStatus
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status is ToolStatus.OK

    def to_model_payload(self) -> dict[str, Any]:
        """What the model sees. Errors are described, never hidden."""
        if self.succeeded:
            return {"status": "ok", "result": self.output}
        return {"status": self.status.value, "error": self.error}


class ToolSpec(BaseModel):
    """Provider-agnostic tool declaration handed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class Tool(ABC):
    """Base class for every tool.

    Subclasses set the class variables and implement `_run`. They never
    implement `execute` — that is where the guarantees live.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[type[BaseModel]]
    permission: ClassVar[Permission]
    timeout_seconds: ClassVar[float] = 10.0

    @abstractmethod
    async def _run(self, args: Any) -> dict[str, Any]:
        """Do the work. `args` is already validated against `input_schema`."""
        raise NotImplementedError

    async def execute(self, call: ToolCall) -> ToolOutcome:
        """Validate, run under a timeout, and never raise.

        Returns a ToolOutcome in every case. A tool failure is data the agent
        loop reasons about, not an exception that unwinds it — the model needs
        to be told what went wrong so it can try something else.
        """
        started = time.perf_counter()

        try:
            args = self.input_schema.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolOutcome(
                call_id=call.id,
                name=self.name,
                status=ToolStatus.INVALID_ARGS,
                error=_summarise_validation_error(exc),
                latency_ms=_elapsed_ms(started),
            )

        try:
            output = await asyncio.wait_for(self._run(args), timeout=self.timeout_seconds)
        except TimeoutError:
            return ToolOutcome(
                call_id=call.id,
                name=self.name,
                status=ToolStatus.TIMEOUT,
                error=f"Tool '{self.name}' exceeded its {self.timeout_seconds}s timeout",
                latency_ms=_elapsed_ms(started),
            )
        except (ToolValidationError, ToolTimeoutError) as exc:
            return ToolOutcome(
                call_id=call.id,
                name=self.name,
                status=ToolStatus.INVALID_ARGS,
                error=exc.message,
                latency_ms=_elapsed_ms(started),
            )
        except Exception as exc:  # noqa: BLE001 - a tool must not crash the agent
            return ToolOutcome(
                call_id=call.id,
                name=self.name,
                status=ToolStatus.ERROR,
                error=f"{type(exc).__name__}: {exc}"[:500],
                latency_ms=_elapsed_ms(started),
            )

        return ToolOutcome(
            call_id=call.id,
            name=self.name,
            status=ToolStatus.OK,
            output=output,
            latency_ms=_elapsed_ms(started),
        )

    @classmethod
    def spec(cls) -> ToolSpec:
        """Declaration for the model, derived from the Pydantic input schema.

        Generated, never hand-written: a hand-maintained copy of the schema
        drifts from the validator, and then the model is told one thing while
        the code enforces another.
        """
        schema = cls.input_schema.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=cls.name, description=cls.description, parameters=schema)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _summarise_validation_error(exc: ValidationError) -> str:
    """Compact, model-readable validation errors.

    Pydantic's full output is verbose and includes URLs. The model needs to know
    which field is wrong and why, in as few tokens as possible.
    """
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(p) for p in error["loc"]) or "(root)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)[:500]
