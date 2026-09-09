"""Routing, and its measurement."""

from __future__ import annotations

import json

from amos.agents.registry import AgentRegistry
from amos.agents.router import ROUTING_CASES, Router, RoutingCase, evaluate_routing
from amos.llm.fake import FakeProvider


def route(agent: str) -> str:
    return json.dumps({"agent": agent, "reason": "r"})


async def test_a_valid_agent_is_returned() -> None:
    decision = await Router(FakeProvider([route("analyst")]), AgentRegistry()).route("t")
    assert decision.agent == "analyst"


async def test_a_hallucinated_agent_falls_back() -> None:
    decision = await Router(FakeProvider([route("wizard")]), AgentRegistry()).route("t")
    assert decision.agent == "researcher"
    assert "not a routable agent" in decision.reason


async def test_unparseable_routing_output_falls_back() -> None:
    decision = await Router(FakeProvider(["not json"]), AgentRegistry()).route("t")
    assert decision.agent == "researcher"


async def test_routing_uses_zero_temperature() -> None:
    """Routing should be stable, not creative — the same task should route the
    same way every time."""
    provider = FakeProvider([route("analyst")])
    await Router(provider, AgentRegistry()).route("t")
    assert provider.calls[0].temperature == 0.0


async def test_evaluation_scores_a_perfect_router() -> None:
    cases = [RoutingCase("calculate 2+2", "analyst")]
    router = Router(FakeProvider([route("analyst")]), AgentRegistry())
    result = await evaluate_routing(router, cases)

    assert result.accuracy == 1.0
    assert result.mistakes == []


async def test_evaluation_records_what_was_chosen_instead() -> None:
    """'80% accurate' is a scoreboard; naming the mistakes is actionable."""
    cases = [RoutingCase("calculate 2+2", "analyst")]
    router = Router(FakeProvider([route("researcher")]), AgentRegistry())
    result = await evaluate_routing(router, cases)

    assert result.accuracy == 0.0
    instruction, expected, actual = result.mistakes[0]
    assert (expected, actual) == ("analyst", "researcher")


def test_the_labelled_set_covers_both_routable_agents() -> None:
    expected = {c.expected_agent for c in ROUTING_CASES}
    assert expected == {"researcher", "analyst"}


def test_the_labelled_set_includes_topic_versus_requirement_traps() -> None:
    """A router that pattern-matches subject matter instead of what the task
    requires must fail some cases, or the measurement proves nothing."""
    assert any(c.note for c in ROUTING_CASES)
