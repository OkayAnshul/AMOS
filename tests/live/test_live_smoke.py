"""The only tests that touch the real API.

Skipped by default. Everything else uses FakeProvider (requirement N-14): the
free tier is ~15 RPM and non-deterministic, so network tests in the main suite
would be slow, flaky and rate-limited.

Run explicitly:  AMOS_RUN_LIVE_TESTS=1 pytest tests/live -v
"""

from __future__ import annotations

import os

import pytest

from amos.agents.agent import GroundedAgent
from amos.agents.schemas import AgentResponse
from amos.config import get_settings
from amos.llm.gemini import GeminiProvider

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("AMOS_RUN_LIVE_TESTS") != "1",
        reason="live tests are opt-in: set AMOS_RUN_LIVE_TESTS=1",
    ),
]


async def test_real_gemini_returns_valid_structured_output() -> None:
    settings = get_settings()
    provider = GeminiProvider(settings.require_api_key(), settings.llm_model)
    agent = GroundedAgent(provider, timeout=90)

    result = await agent.run("What is 17 percent of 2340? Answer with the number.")

    assert isinstance(result.response, AgentResponse)
    assert "397" in result.response.answer
    assert result.total_tokens > 0
    assert result.llm_calls[0].provider == "gemini"
    # Records how often the repair loop actually fires against the real model.
    print(f"\nrepair_count={result.repair_count} tokens={result.total_tokens}")


async def test_real_gemini_uses_the_calculator_tool() -> None:
    """The V0.2 claim, verified end to end: the model chooses a tool, AMOS
    validates and executes it, and the result reaches the final answer."""
    from amos.agents.tool_agent import ToolUsingAgent
    from amos.tools.builtin import CalculatorTool
    from amos.tools.registry import ToolRegistry

    settings = get_settings()
    provider = GeminiProvider(settings.require_api_key(), settings.llm_model)
    agent = ToolUsingAgent(provider, ToolRegistry([CalculatorTool()]), timeout=90)

    result = await agent.run("What is 17 percent of 2340? Use the calculator tool.")

    assert result.tool_outcomes, "the model should have called a tool"
    assert result.tool_outcomes[0].name == "calculator"
    assert result.tool_outcomes[0].status.value == "ok"
    assert "397" in result.response.answer
    print(
        f"\ntools={[o.name for o in result.tool_outcomes]} "
        f"llm_calls={len(result.llm_calls)} tokens={result.total_tokens}"
    )
