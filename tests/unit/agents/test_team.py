"""The team: routing, specialised execution, bounded criticism."""

from __future__ import annotations

import json

import pytest

from amos.agents.registry import AgentRegistry
from amos.agents.team import AgentTeam
from amos.llm.fake import FakeProvider
from amos.tools.builtin import CalculatorTool, HttpGetTool, ReadFileTool
from amos.tools.registry import ToolRegistry
from tests.conftest import valid_response_json


@pytest.fixture
def tools(tmp_path: object) -> ToolRegistry:
    return ToolRegistry([CalculatorTool(), HttpGetTool(), ReadFileTool(tmp_path)])  # type: ignore[arg-type]


def route(agent: str) -> str:
    return json.dumps({"agent": agent, "reason": "because"})


def review(verdict: str, **extra: object) -> str:
    payload: dict[str, object] = {"verdict": verdict, "reasoning": "r"}
    payload.update(extra)
    return json.dumps(payload)


async def test_a_routed_task_runs_on_the_chosen_specialist(tools: ToolRegistry) -> None:
    provider = FakeProvider([route("analyst"), valid_response_json(), review("accept")])
    result = await AgentTeam(provider, tools).run("calculate something")

    assert result.agent_name == "analyst"
    assert result.critic_verdict == "accept"


async def test_an_invalid_agent_name_falls_back_rather_than_failing(
    tools: ToolRegistry,
) -> None:
    """A wrong route produces a worse answer; a failed route produces none."""
    provider = FakeProvider([route("wizard"), valid_response_json(), review("accept")])
    result = await AgentTeam(provider, tools).run("do something")
    assert result.agent_name == "researcher"


async def test_routing_to_the_critic_is_refused(tools: ToolRegistry) -> None:
    """Routing work to a validator is a category error."""
    provider = FakeProvider([route("critic"), valid_response_json(), review("accept")])
    result = await AgentTeam(provider, tools).run("do something")
    assert result.agent_name == "researcher"


async def test_the_specialist_only_receives_its_own_tools(tools: ToolRegistry) -> None:
    provider = FakeProvider([route("analyst"), valid_response_json(), review("accept")])
    team = AgentTeam(provider, tools)
    await team.run("calculate")

    # The second call is the agent's; its declared tools must be the analyst's.
    declared = {spec.name for spec in provider.calls[1].tools}
    assert declared == {"calculator"}


async def test_researcher_is_offered_research_tools_not_the_calculator(
    tools: ToolRegistry,
) -> None:
    provider = FakeProvider([route("researcher"), valid_response_json(), review("accept")])
    await AgentTeam(provider, tools).run("find something")

    declared = {spec.name for spec in provider.calls[1].tools}
    assert "calculator" not in declared
    assert {"http_get", "read_file"} <= declared


# ---------- the bounded argument ----------


async def test_a_rejected_answer_is_revised_once_then_accepted(
    tools: ToolRegistry,
) -> None:
    provider = FakeProvider(
        [
            route("researcher"),
            valid_response_json(answer="first attempt"),
            review("revise", unsupported_claims=["claim A"]),
            valid_response_json(answer="revised attempt"),
            review("accept"),
        ]
    )
    result = await AgentTeam(provider, tools, max_revisions=1).run("goal")

    assert result.response.answer == "revised attempt"
    assert result.critic_verdict == "accept"


async def test_a_critic_that_never_accepts_cannot_loop_forever(
    tools: ToolRegistry,
) -> None:
    """Two models arguing without a bound spends a day's quota in minutes."""
    provider = FakeProvider(
        [
            route("researcher"),
            valid_response_json(answer="attempt"),
            review("revise", unsupported_claims=["always something"]),
        ]
    )
    result = await AgentTeam(provider, tools, max_revisions=2).run("goal")

    assert result.critic_verdict == "revise"
    # 1 route + 1 answer + 1 review + 2 x (revise + review) = 7, and no more.
    assert provider.call_count <= 8


async def test_unresolved_objections_reach_the_user(tools: ToolRegistry) -> None:
    """Returning a rejected answer silently, as if accepted, is the worst option."""
    provider = FakeProvider(
        [
            route("researcher"),
            valid_response_json(answer="attempt"),
            review("revise", unsupported_claims=["unsupported thing"]),
        ]
    )
    result = await AgentTeam(provider, tools, max_revisions=0).run("goal")

    assert any("unsupported thing" in c for c in result.response.caveats)
    assert result.response.confidence.value == "low"


async def test_the_critic_can_be_disabled(tools: ToolRegistry) -> None:
    """Review costs a call per answer on a 20/day quota."""
    provider = FakeProvider([route("analyst"), valid_response_json()])
    result = await AgentTeam(provider, tools, critic_enabled=False).run("goal")

    assert result.critic_verdict is None
    assert provider.call_count == 2


async def test_routing_is_skipped_when_disabled(tools: ToolRegistry) -> None:
    provider = FakeProvider([valid_response_json(), review("accept")])
    result = await AgentTeam(provider, tools, routing_enabled=False).run("goal")
    assert result.agent_name == "researcher"


async def test_every_call_is_accounted_for_in_the_trace(tools: ToolRegistry) -> None:
    """Routing and review are not free; the trace must show what they cost."""
    provider = FakeProvider([route("analyst"), valid_response_json(), review("accept")])
    result = await AgentTeam(provider, tools).run("goal")

    assert len(result.llm_calls) == 3
    assert result.total_tokens == sum(c.prompt_tokens + c.output_tokens for c in result.llm_calls)


async def test_single_routable_agent_skips_the_routing_call(tools: ToolRegistry) -> None:
    from amos.agents.registry import ANALYST, CRITIC

    provider = FakeProvider([valid_response_json(), review("accept")])
    team = AgentTeam(provider, tools, agents=AgentRegistry(specs=(ANALYST, CRITIC)))
    result = await team.run("goal")

    assert result.agent_name == "analyst"
    assert provider.call_count == 2, "nothing to choose between, so no router call"
