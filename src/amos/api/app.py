"""FastAPI application.

The API layer does three things and no more: validate input, delegate, and map
domain errors to status codes. Business logic lives in the agent.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from amos.agents.agent import GroundedAgent
from amos.agents.schemas import AgentResult, GoalRequest
from amos.api.dependencies import build_agent
from amos.config import Settings, get_settings
from amos.errors import (
    AmosError,
    ConfigurationError,
    OutputValidationError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from amos.observability import configure_logging, log_event, new_request_id, set_request_id

logger = logging.getLogger(__name__)

# Domain error -> HTTP status. One table, so adding an error type is one line.
_STATUS_MAP: list[tuple[type[AmosError], int]] = [
    (ProviderTimeoutError, 504),
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


def create_app(settings: Settings | None = None, agent: GroundedAgent | None = None) -> FastAPI:
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
        if app.state.agent is None:
            app.state.agent = build_agent(settings)
        log_event(logger, "amos.started", model=settings.llm_model, env=settings.env)
        yield

    app = FastAPI(
        title="AMOS",
        description="Autonomous Multi-Agent Operating System — V0.1",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.agent = agent
    app.state.settings = settings

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
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/v1/goals", response_model=AgentResult)
    async def submit_goal(payload: GoalRequest) -> AgentResult:
        log_event(logger, "goal.received", goal_length=len(payload.goal))
        agent: GroundedAgent = app.state.agent
        result = await agent.run(payload.goal)
        return result

    return app


app = create_app
