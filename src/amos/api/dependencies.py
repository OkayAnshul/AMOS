"""Dependency wiring.

Built once at startup and injected. Swapping the provider or changing the tool
set is a change here and nowhere else — that is what the Protocol in
llm/base.py and the registry in tools/registry.py are for.
"""

from __future__ import annotations

from pathlib import Path

from amos.agents.agent import GroundedAgent
from amos.agents.tool_agent import ToolUsingAgent, build_default_registry
from amos.config import Settings
from amos.llm.base import LLMProvider
from amos.llm.gemini import GeminiProvider
from amos.orchestration.orchestrator import Orchestrator
from amos.rag.embeddings import GeminiEmbeddings
from amos.rag.retrieval import SearchKnowledgeTool
from amos.rag.store import SessionScopedVectorStore
from amos.tools.registry import ToolRegistry


def build_provider(settings: Settings) -> LLMProvider:
    return GeminiProvider(api_key=settings.require_api_key(), model=settings.llm_model)


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
    retrieval = build_retrieval_tool(settings, session_factory)
    return build_default_registry(
        Path(settings.tool_sandbox_root).resolve(),
        [retrieval] if retrieval is not None else [],
    )


def build_tool_agent(
    settings: Settings,
    provider: LLMProvider | None = None,
    registry: ToolRegistry | None = None,
) -> ToolUsingAgent:
    """The V0.2 agent: tool-using, bounded loop. Also the executor's task runner."""
    return ToolUsingAgent(
        provider or build_provider(settings),
        registry or build_registry(settings),
        timeout=settings.llm_timeout_seconds,
        max_iterations=settings.agent_max_iterations,
        temperature=settings.llm_temperature,
    )


def build_agent(
    settings: Settings,
    provider: LLMProvider | None = None,
    registry: ToolRegistry | None = None,
    session_factory: object | None = None,
) -> Orchestrator | ToolUsingAgent:
    """The V0.4 orchestrator, or the V0.2 agent when planning is disabled.

    Both satisfy `run(goal) -> AgentResult`, so nothing downstream branches on
    which one is in use.
    """
    provider = provider or build_provider(settings)
    runner = build_tool_agent(
        settings, provider, registry or build_registry(settings, session_factory)
    )
    if not settings.planning_enabled:
        return runner
    return Orchestrator(
        provider,
        runner,
        timeout=settings.llm_timeout_seconds,
        max_attempts=settings.task_max_attempts,
        temperature=settings.llm_temperature,
    )


def build_grounded_agent(settings: Settings, provider: LLMProvider | None = None) -> GroundedAgent:
    """The V0.1 agent: no tools. Kept because it is still the right choice when
    a goal needs no tools, and because it is what the repair-loop tests cover."""
    return GroundedAgent(
        provider or build_provider(settings),
        timeout=settings.llm_timeout_seconds,
        max_repair_attempts=settings.llm_max_repair_attempts,
        temperature=settings.llm_temperature,
    )
