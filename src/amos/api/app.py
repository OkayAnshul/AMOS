"""FastAPI application.

The API layer does three things and no more: validate input, delegate, and map
domain errors to status codes. Business logic lives in the agent.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from amos.agents.schemas import AgentResult, GoalRequest, RunTrace
from amos.agents.tool_agent import ToolUsingAgent
from amos.api.dependencies import build_agent
from amos.api.persistence import RunService
from amos.config import Settings, get_settings
from amos.database.engine import create_engine, create_session_factory
from amos.errors import (
    AmosError,
    ConfigurationError,
    OutputValidationError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolLoopExhaustedError,
)
from amos.observability import (
    configure_logging,
    get_request_id,
    log_event,
    new_request_id,
    set_request_id,
)
from amos.orchestration.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# Domain error -> HTTP status. One table, so adding an error type is one line.
_STATUS_MAP: list[tuple[type[AmosError], int]] = [
    (ProviderTimeoutError, 504),
    (ToolLoopExhaustedError, 502),
    (ProviderRateLimitError, 429),
    (ProviderAuthError, 502),
    (OutputValidationError, 502),
    (ConfigurationError, 500),
]


def _status_for(exc: AmosError) -> int:
    for error_type, status in _STATUS_MAP:
        if isinstance(exc, error_type):
            return status
    return 500


def create_app(
    settings: Settings | None = None,
    agent: Orchestrator | ToolUsingAgent | None = None,
    run_service: RunService | None = None,
) -> FastAPI:
    """Build the app.

    `agent` is injectable so tests can supply one backed by FakeProvider without
    an API key or a network call.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast: a missing key is a startup error, not a surprise on the
        # first request an hour from now.
        # The engine is built first: from V0.5 the agent's retrieval tool needs
        # a session factory, so the ordering is load-bearing rather than stylistic.
        factory = None
        if settings.database_url:
            app.state.engine = create_engine(settings)
            factory = create_session_factory(app.state.engine)

        if app.state.agent is None:
            app.state.agent = build_agent(settings, session_factory=factory)

        # Persistence is optional so the app still runs without a database —
        # earlier milestones' behaviour stays reachable, and tests need no container.
        if app.state.run_service is None:
            app.state.run_service = RunService(app.state.agent, factory)

        log_event(
            logger,
            "amos.started",
            model=settings.llm_model,
            env=settings.env,
            tools=app.state.agent.tool_names,
            planning=isinstance(app.state.agent, Orchestrator),
            persistence=app.state.run_service.persistence_enabled,
        )
        try:
            yield
        finally:
            if app.state.engine is not None:
                await app.state.engine.dispose()

    app = FastAPI(
        title="AMOS",
        description="Autonomous Multi-Agent Operating System — V0.5",
        version="0.5.0",
        lifespan=lifespan,
    )
    app.state.agent = agent
    app.state.settings = settings
    app.state.run_service = run_service
    app.state.engine = None

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[object]]
    ) -> object:
        request_id = request.headers.get("x-request-id") or new_request_id()
        set_request_id(request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id  # type: ignore[attr-defined]
        return response

    @app.exception_handler(AmosError)
    async def amos_error_handler(request: Request, exc: AmosError) -> JSONResponse:
        status = _status_for(exc)
        log_event(
            logger,
            "amos.error",
            error_type=type(exc).__name__,
            status=status,
            error_message=exc.message,
        )
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "type": type(exc).__name__,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.5.0"}

    @app.post("/v1/goals", response_model=AgentResult)
    async def submit_goal(
        payload: GoalRequest,
        idempotency_key: str | None = Header(default=None, alias="idempotency-key"),
    ) -> AgentResult:
        log_event(
            logger,
            "goal.received",
            goal_length=len(payload.goal),
            idempotent=idempotency_key is not None,
        )
        service: RunService = app.state.run_service
        result, run_id = await service.execute(
            payload.goal,
            request_id=get_request_id() or "",
            idempotency_key=idempotency_key,
        )
        if run_id is not None:
            result.run_id = str(run_id)
        return result

    @app.get("/v1/runs/{run_id}", response_model=RunTrace)
    async def get_run_trace(run_id: str) -> RunTrace:
        """What exactly happened on this request.

        Assembled from stored rows only, so it answers for runs that finished
        weeks ago and for runs this process never saw.
        """
        try:
            parsed = uuid.UUID(run_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="run_id must be a UUID") from None

        service: RunService = app.state.run_service
        if not service.persistence_enabled:
            raise HTTPException(
                status_code=503,
                detail="Persistence is not configured; set AMOS_DATABASE_URL.",
            )
        trace = await service.get_trace(parsed)
        if trace is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        return trace

    return app


app = create_app
