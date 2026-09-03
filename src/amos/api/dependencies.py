"""Dependency wiring.

The agent is built once at startup and injected. Swapping GeminiProvider for
another implementation is a change here and nowhere else — that is the point of
the Protocol in llm/base.py.
"""

from __future__ import annotations

from amos.agents.agent import GroundedAgent
from amos.config import Settings
from amos.llm.base import LLMProvider
from amos.llm.gemini import GeminiProvider


def build_provider(settings: Settings) -> LLMProvider:
    return GeminiProvider(
        api_key=settings.require_api_key(),
        model=settings.llm_model,
    )


def build_agent(settings: Settings, provider: LLMProvider | None = None) -> GroundedAgent:
    return GroundedAgent(
        provider or build_provider(settings),
        timeout=settings.llm_timeout_seconds,
        max_repair_attempts=settings.llm_max_repair_attempts,
        temperature=settings.llm_temperature,
    )
