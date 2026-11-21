"""Dependency wiring.

Built once at startup and injected. Swapping the provider or changing the tool
set is a change here and nowhere else — that is what the Protocol in
llm/base.py and the registry in tools/registry.py are for.
"""

from __future__ import annotations

from pathlib import Path

from amos.agents.agent import GroundedAgent
from amos.agents.schemas import OPTION_SETTINGS, RunOptions
from amos.agents.sourcing import RecordedSources
from amos.agents.team import AgentTeam
from amos.agents.tool_agent import ToolUsingAgent, build_default_registry
from amos.budget import RunBudget
from amos.config import Settings
from amos.database.progress import RunProgress
from amos.llm.base import LLMProvider
from amos.llm.gemini import GeminiProvider
from amos.llm.ratelimit import MinuteRateLimiter, RateLimitedProvider
from amos.llm.recording import RecordingProvider
from amos.llm.retry import RetryingProvider
from amos.memory.tools import RecallFactsTool, RecallPastRunsTool, RememberFactTool
from amos.orchestration.executor import Executor
from amos.orchestration.orchestrator import Orchestrator
from amos.rag.embeddings import GeminiEmbeddings
from amos.rag.retrieval import SearchKnowledgeTool
from amos.rag.store import SessionScopedVectorStore
from amos.tools.builtin.web import WEB_TOOLS, FetchPageTool, RecordedProvenance, WebSearchTool
from amos.tools.registry import ToolRegistry


def build_provider(settings: Settings) -> LLMProvider:
    """The one place a provider is constructed (V1.6).

    Wrapped in `RetryingProvider` so a 5xx costs one extra call wherever it
    happens — planner and synthesis included, which nothing retried before
    (ADR-014). The judge and the routing CLI come through here too, rather than
    building their own unwrapped provider.
    """
    # Recording INSIDE the retry: every real attempt is its own row (ADR-015).
    # The limiter inside the retry too, so a retried attempt waits for a slot;
    # outside the recorder, so a recorded latency is the provider's, not the queue's.
    return RetryingProvider(
        RateLimitedProvider(
            RecordingProvider(
                GeminiProvider(api_key=settings.require_api_key(), model=settings.llm_model)
            ),
            _limiter(settings.llm_minute_request_limit),
        )
    )


_LIMITERS: dict[int, MinuteRateLimiter] = {}


def _limiter(per_minute_quota: int) -> MinuteRateLimiter:
    """One limiter per process and quota: every provider built here shares it,
    because the quota is the key's, not the provider object's. One slot of
    headroom under the published limit."""
    slots = max(1, per_minute_quota - 1)
    if slots not in _LIMITERS:
        _LIMITERS[slots] = MinuteRateLimiter(slots)
    return _LIMITERS[slots]


def build_retrieval_tool(
    settings: Settings, session_factory: object | None
) -> SearchKnowledgeTool | None:
    """The retrieval tool, when there is a database to retrieve from.

    Returns None without persistence, so the agent simply has one tool fewer
    rather than a tool that fails on every call.
    """
    if session_factory is None:
        return None
    return SearchKnowledgeTool(
        SessionScopedVectorStore(session_factory),
        GeminiEmbeddings(
            settings.require_api_key(),
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        ),
        min_score=settings.retrieval_min_score,
    )


def build_registry(settings: Settings, session_factory: object | None = None) -> ToolRegistry:
    """The tool set. Database-backed tools appear only when there is a database.

    Without persistence the agent simply has fewer tools, rather than tools that
    fail on every call.
    """
    extra: list[object] = []
    if session_factory is not None:
        retrieval = build_retrieval_tool(settings, session_factory)
        if retrieval is not None:
            extra.append(retrieval)
        if settings.memory_enabled:
            embeddings = GeminiEmbeddings(
                settings.require_api_key(),
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
            extra.extend(
                [
                    RememberFactTool(session_factory, embeddings),
                    RecallFactsTool(
                        session_factory, embeddings, min_score=settings.memory_min_score
                    ),
                    RecallPastRunsTool(session_factory, embeddings),
                ]
            )
        if settings.web_enabled:
            # With a database only: fetch_page's provenance is read from the
            # stored run, so without one it could fetch nothing (ADR-018).
            extra.extend(
                [
                    WebSearchTool(settings.searxng_url),
                    FetchPageTool(RecordedProvenance(session_factory)),
                ]
            )
    return build_default_registry(Path(settings.tool_sandbox_root).resolve(), extra)


def build_tool_agent(
    settings: Settings,
    provider: LLMProvider | None = None,
    registry: ToolRegistry | None = None,
) -> ToolUsingAgent:
    """The V0.2 agent: tool-using, bounded loop. Also the executor's task runner."""
    # `is None`, not `or`: ToolRegistry defines __len__, so an EMPTY registry is
    # falsy and `registry or build_registry(...)` silently replaced it with the
    # full tool set (found in the V1.6 survey).
    return ToolUsingAgent(
        provider if provider is not None else build_provider(settings),
        registry if registry is not None else build_registry(settings),
        timeout=settings.llm_timeout_seconds,
        max_iterations=settings.agent_max_iterations,
        temperature=settings.llm_temperature,
    )


def build_agent(
    settings: Settings,
    provider: LLMProvider | None = None,
    registry: ToolRegistry | None = None,
    session_factory: object | None = None,
) -> Orchestrator | ToolUsingAgent | AgentTeam:
    """The V0.4 orchestrator, or the V0.2 agent when planning is disabled.

    Both satisfy `run(goal) -> AgentResult`, so nothing downstream branches on
    which one is in use.
    """
    provider = provider if provider is not None else build_provider(settings)
    tools = registry if registry is not None else build_registry(settings, session_factory)

    # V0.7: the team is a task runner like any other single agent, because they
    # all satisfy `run(goal) -> AgentResult`. That interface holding across five
    # milestones is why this is a substitution rather than a rewrite.
    runner: ToolUsingAgent | AgentTeam
    if settings.multi_agent_enabled:
        runner = AgentTeam(
            provider,
            tools,
            timeout=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_revisions=settings.max_revisions,
            max_iterations=settings.agent_max_iterations,
            critic_enabled=settings.critic_enabled,
            delegation_enabled=settings.delegation_enabled,
            max_delegation_depth=settings.max_delegation_depth,
            delegation_budget=settings.delegation_budget,
            sources=RecordedSources(session_factory) if session_factory is not None else None,
        )
    else:
        runner = build_tool_agent(settings, provider, tools)
    if not settings.planning_enabled:
        return runner

    # V1.1: with a database, the plan is persisted before execution and each
    # task checkpointed as it finishes, so a reclaimed run resumes instead of
    # re-executing (ADR-010). Without one, both are None and the orchestrator
    # behaves exactly as it did at V0.4 — persistence stays optional.
    progress = RunProgress(session_factory) if session_factory is not None else None

    return Orchestrator(
        provider,
        runner,
        timeout=settings.llm_timeout_seconds,
        max_attempts=settings.task_max_attempts,
        task_timeout_seconds=settings.task_timeout_seconds,
        temperature=settings.llm_temperature,
        plan_store=progress,
        executor=Executor(
            runner,
            max_attempts=settings.task_max_attempts,
            task_timeout_seconds=settings.task_timeout_seconds,
            checkpoint=progress,
        )
        if progress is not None
        else None,
    )


def build_grounded_agent(settings: Settings, provider: LLMProvider | None = None) -> GroundedAgent:
    """The V0.1 agent: no tools. Kept because it is still the right choice when
    a goal needs no tools, and because it is what the repair-loop tests cover."""
    return GroundedAgent(
        provider if provider is not None else build_provider(settings),
        timeout=settings.llm_timeout_seconds,
        max_repair_attempts=settings.llm_max_repair_attempts,
        temperature=settings.llm_temperature,
    )


#: Tools that read or write memory — absent from a run that switches memory off.
MEMORY_TOOLS = frozenset({"remember_fact", "recall_facts", "recall_past_runs"})


def settings_for(settings: Settings, options: RunOptions | None) -> Settings:
    """The server's settings with a run's options applied (ADR-016).

    Options are validated as off-only at the API; `model_copy` does not
    validate, which is safe here because only booleans are copied in.
    """
    if options is None or not options.given():
        return settings
    return settings.model_copy(
        update={OPTION_SETTINGS[name]: value for name, value in options.given().items()}
    )


def filter_registry(registry: ToolRegistry, settings: Settings) -> ToolRegistry:
    dropped = (set() if settings.memory_enabled else set(MEMORY_TOOLS)) | (
        set() if settings.web_enabled else set(WEB_TOOLS)
    )
    if not dropped & set(registry.names):
        return registry
    return ToolRegistry([tool for tool in registry if tool.name not in dropped])


def budget_for(settings: Settings) -> RunBudget:
    """A fresh budget for one run (ADR-019)."""
    return RunBudget(
        max_llm_calls=settings.run_max_llm_calls,
        max_tool_calls=settings.run_max_tool_calls,
        max_page_fetches=settings.run_max_page_fetches,
    )


def build_run_agent(
    settings: Settings,
    options: RunOptions | None,
    *,
    provider: LLMProvider,
    registry: ToolRegistry,
    session_factory: object | None,
) -> Orchestrator | ToolUsingAgent | AgentTeam:
    """An agent for one run, from the process's shared provider and tools.

    Constructors do no I/O, so building per run costs object allocation only;
    the worker has built an agent per run since V0.8.
    """
    effective = settings_for(settings, options)
    return build_agent(
        effective,
        provider=provider,
        registry=filter_registry(registry, effective),
        session_factory=session_factory,
    )
