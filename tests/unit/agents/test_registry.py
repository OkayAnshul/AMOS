"""Agent specialisation: capability, not wording.

The whole claim of V0.7 rests on agents differing in what they CAN DO. These
tests assert that structurally, because "three prompts over the same tools" is
the failure mode an interviewer will probe first.
"""

from __future__ import annotations

import pytest

from amos.agents.registry import (
    ANALYST,
    CRITIC,
    DEFAULT_SPECS,
    RESEARCHER,
    AgentRegistry,
    UnknownAgentError,
)
from amos.errors import ToolNotFoundError
from amos.tools.builtin import CalculatorTool, HttpGetTool, ReadFileTool
from amos.tools.registry import ToolRegistry


@pytest.fixture
def all_tools(tmp_path: object) -> ToolRegistry:
    return ToolRegistry([CalculatorTool(), HttpGetTool(), ReadFileTool(tmp_path)])  # type: ignore[arg-type]


def test_agents_have_different_tool_allowlists() -> None:
    """If these were equal, this would be one agent with three prompts."""
    assert RESEARCHER.tools != ANALYST.tools
    assert RESEARCHER.tools.isdisjoint(ANALYST.tools)


def test_researcher_cannot_calculate(all_tools: ToolRegistry) -> None:
    """Enforced structurally: the tool is absent from its registry, so refusing
    it needs no extra code path."""
    registry = RESEARCHER.registry_from(all_tools)
    assert not registry.has("calculator")
    with pytest.raises(ToolNotFoundError):
        registry.get("calculator")


def test_analyst_cannot_reach_the_network(all_tools: ToolRegistry) -> None:
    registry = ANALYST.registry_from(all_tools)
    assert not registry.has("http_get")
    assert not registry.has("read_file")
    assert registry.has("calculator")


def test_the_critic_has_no_tools_at_all(all_tools: ToolRegistry) -> None:
    """A critic that can fetch new sources is doing research, and its verdict
    becomes unfalsifiable — it can always find something to justify itself."""
    assert CRITIC.tools == frozenset()
    assert len(CRITIC.registry_from(all_tools)) == 0


def test_allowlists_survive_a_registry_that_has_more_tools(
    all_tools: ToolRegistry,
) -> None:
    """The filter is by name, so adding a tool globally does not silently widen
    every agent's capability."""
    researcher = RESEARCHER.registry_from(all_tools)
    assert set(researcher.names) <= RESEARCHER.tools


def test_unknown_agent_raises_with_the_available_names() -> None:
    with pytest.raises(UnknownAgentError) as exc:
        AgentRegistry().get("nonexistent")
    assert "researcher" in exc.value.message


def test_the_critic_is_not_routable() -> None:
    """Routing work to a validator is a category error, not a preference."""
    assert "critic" not in [s.name for s in AgentRegistry().routable]
    assert "critic" in AgentRegistry().names


def test_every_spec_has_a_distinct_name_and_an_instruction() -> None:
    names = [s.name for s in DEFAULT_SPECS]
    assert len(names) == len(set(names))
    assert all(s.system_instruction.strip() for s in DEFAULT_SPECS)


def test_instructions_reflect_the_allowlist() -> None:
    """A prompt that promises a capability the allowlist denies produces an agent
    that repeatedly tries a tool it cannot have."""
    assert "no calculator" in RESEARCHER.system_instruction.lower()
    assert "cannot search" in ANALYST.system_instruction.lower()
    assert "no tools" in CRITIC.system_instruction.lower()
