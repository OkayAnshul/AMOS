"""FastAPI application.

The API layer does three things and no more: validate input, delegate, and map
domain errors to status codes. Business logic lives in the agent.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from amos import __version__
from amos.agents.schemas import AgentResult, GoalRequest, QueuedRun, RunTrace
from amos.agents.team import AgentTeam
from amos.agents.tool_agent import ToolUsingAgent
from amos.api.dependencies import build_agent, build_provider, build_registry
from amos.api.persistence import RunService
from amos.auth import LOCAL_ACTOR, Actor, authenticate, key_from_header
from amos.config import Settings, get_settings
from amos.database.engine import create_engine, create_session_factory, session_scope
from amos.errors import (
    AmosError,
    ConfigurationError,
    OutputValidationError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolLoopExhaustedError,
)
from amos.memory.episodic import EpisodicMemory
from amos.memory.reconcile import MemoryReconciler
from amos.observability import (
    configure_logging,
    get_request_id,
    log_event,
    new_request_id,
    set_request_id,
)
from amos.orchestration.orchestrator import Orchestrator
from amos.rag.embeddings import GeminiEmbeddings
from amos.telemetry.metrics import configure_metrics
from amos.telemetry.tracing import configure_tracing
from amos.worker.queue import DeadLetteredRun

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


async def current_actor(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Actor:
    """Who is asking. 401 for anything that is not a valid key.

    A dependency rather than middleware, so an endpoint that needs no identity
    (`/health`) simply does not declare it — rather than being exempted by a
    path list somebody has to remember to maintain.

    **Module level, deliberately.** Defined inside `create_app` it was a local
    name, and `from __future__ import annotations` makes FastAPI resolve the
    annotation as a *string* against module globals — where it was not found, so
    every protected endpoint silently treated `actor` as a query parameter and
    answered 422 instead of 401. The symptom looked nothing like the cause.
    """
    service: RunService = request.app.state.run_service
    if service.session_factory is None:
        # No database means no users, nothing stored, and no isolation to
        # enforce. Requiring a key here would mean the API could not run without
        # infrastructure at all — a property held since V0.3, with its own CI
        # job. The cost is that this deployment is unauthenticated, which is
        # logged at startup and stated in docs/13-security.md.
        return LOCAL_ACTOR

    async with session_scope(service.session_factory) as session:
        actor = await authenticate(session, key_from_header(authorization))

    if actor is None:
        # One failure path for missing, malformed and wrong keys. The distinction
        # is not useful to a client, and enumerating it tells an attacker which
        # half of their guess was right.
        raise HTTPException(
            status_code=401,
            detail="Provide a valid API key: Authorization: Bearer <key>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return actor


#: Annotated rather than a Depends() default: the modern FastAPI idiom, and it
#: keeps a function call out of an argument default.
CurrentActor = Annotated[Actor, Depends(current_actor)]


def create_app(
    settings: Settings | None = None,
    agent: Orchestrator | ToolUsingAgent | AgentTeam | None = None,
    run_service: RunService | None = None,
) -> FastAPI:
    """Build the app.

    `agent` is injectable so tests can supply one backed by FakeProvider without
    an API key or a network call.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    tracing_on = configure_tracing(settings)
    configure_metrics(settings)

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
            episodic = None
            if factory is not None and settings.memory_enabled:
                embeddings = GeminiEmbeddings(
                    settings.require_api_key(),
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                )

                def episodic(session: object) -> EpisodicMemory:
                    return EpisodicMemory(session, embeddings)  # type: ignore[arg-type]

            reconciler = None
            if factory is not None:
                reconciler = MemoryReconciler(
                    build_provider(settings),
                    build_registry(settings, factory),
                    timeout=settings.llm_timeout_seconds,
                    enabled=settings.memory_reconcile_enabled,
                )

            app.state.run_service = RunService(
                app.state.agent,
                factory,
                episodic,
                trace_content=settings.trace_content,
                reconciler=reconciler,
            )

        log_event(
            logger,
            "amos.started",
            model=settings.llm_model,
            env=settings.env,
            tools=app.state.agent.tool_names,
            planning=isinstance(app.state.agent, Orchestrator),
            tracing=tracing_on,
            persistence=app.state.run_service.persistence_enabled,
            authenticated=app.state.run_service.persistence_enabled,
        )
        if not app.state.run_service.persistence_enabled:
            # Loud, because the alternative is an operator assuming V1.4's auth
            # applies and exposing an open API. `authenticated=false` in a log
            # line is easy to miss; a warning is not.
            logger.warning(
                "AMOS is running WITHOUT a database, so the API is UNAUTHENTICATED. "
                "Every request acts as the single local user. Do not expose this."
            )
        try:
            yield
        finally:
            if app.state.engine is not None:
                await app.state.engine.dispose()

    app = FastAPI(
        title="AMOS",
        # Derived, never written out. A literal here read "V0.7" through the whole
        # of a v1.0 build and was served from /openapi.json and /docs — the same
        # drift that made /health report 0.7.0 (engineering/bugs-log.md), fixed in
        # one place and missed in this one.
        description=f"Autonomous Multi-Agent Operating System — v{__version__}",
        version=__version__,
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
        return {"status": "ok", "version": __version__}

    @app.post("/v1/goals", response_model=AgentResult)
    async def submit_goal(
        payload: GoalRequest,
        actor: CurrentActor,
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
            actor,
            request_id=get_request_id() or "",
            idempotency_key=idempotency_key,
        )
        if run_id is not None:
            result.run_id = str(run_id)
        return result

    # Mounted only when enabled. The route used to be unconditional while
    # `async_enabled` sat in the settings unread — so three documents told the
    # reader to export AMOS_ASYNC_ENABLED=true and it changed nothing either way.
    # A flag that does nothing is worse than no flag: it teaches a mental model
    # of the system that is false.
    #
    # Queueing without a worker running leaves runs QUEUED forever, which is why
    # this is a deployment decision and not a per-request one.
    if settings.async_enabled:

        @app.post("/v1/goals/async", status_code=202, response_model=QueuedRun)
        async def submit_goal_async(
            payload: GoalRequest,
            response: Response,
            actor: CurrentActor,
            idempotency_key: str | None = Header(default=None, alias="idempotency-key"),
        ) -> QueuedRun:
            """Queue a goal for a worker and return immediately.

            202 Accepted, not 200: the work has been *accepted*, not *done*.
            Returning 200 here would tell a client the goal was completed when it
            has not started.
            """
            service: RunService = app.state.run_service
            run_id = await service.enqueue_only(
                payload.goal,
                actor,
                request_id=get_request_id() or "",
                idempotency_key=idempotency_key,
            )
            # Location points at where the outcome will appear, so a client does
            # not have to construct the polling URL itself.
            response.headers["location"] = f"/v1/runs/{run_id}"
            return QueuedRun(run_id=str(run_id), status="QUEUED")

    # DECLARED BEFORE /v1/runs/{run_id}, and it has to be. FastAPI matches routes
    # in declaration order, so with the parameterised route first, "dead-letter"
    # binds as a run_id and the request dies on the UUID check — a 422 on a path
    # that exists. The ordering is load-bearing; the test below pins it.
    @app.get("/v1/runs/dead-letter", response_model=list[DeadLetteredRun])
    async def list_dead_lettered(actor: CurrentActor, limit: int = 50) -> list[DeadLetteredRun]:
        """Runs the queue gave up on, newest first.

        These are not ordinary failures. A FAILED run executed and produced a
        verdict; these never got one — the worker died on them
        `AMOS_WORKER_MAX_ATTEMPTS` times and the queue stopped retrying so one
        bad run could not starve every good one.
        """
        service: RunService = app.state.run_service
        if not service.persistence_enabled:
            raise HTTPException(
                status_code=503,
                detail="Persistence is not configured; set AMOS_DATABASE_URL.",
            )
        return await service.list_dead_letter(min(max(limit, 1), 200))

    @app.get("/v1/runs/{run_id}", response_model=RunTrace)
    async def get_run_trace(run_id: str, actor: CurrentActor) -> RunTrace:
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
        trace = await service.get_trace(parsed, actor)
        if trace is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        return trace

    return app


app = create_app
