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
    # Since V1.3 `delegate` is offered alongside them (ADR-012) — it is the one
    # tool every routable agent gets, because it is how an agent reaches a
    # capability it deliberately does not have. What must stay true is that no
    # *other* agent's tools leak in.
    declared = {spec.name for spec in provider.calls[1].tools}
    assert declared == {"calculator", "delegate"}


async def test_delegation_does_not_leak_another_agents_tools(tools: ToolRegistry) -> None:
    """Delegation moves work, not authority.

    With delegation off, the analyst's declared tools are exactly its own — which
    is the invariant the assertion above is really protecting.
    """
    provider = FakeProvider([route("analyst"), valid_response_json(), review("accept")])
    await AgentTeam(provider, tools, delegation_enabled=False).run("calculate")

    declared = {spec.name for spec in provider.calls[1].tools}
    assert declared == {"calculator"}


async def test_researcher_is_offered_research_tools_not_the_calculator(
    tools: ToolRegistry,
) -> None:
    provider = FakeProvider([route("researcher"), valid_response_json(), review("accept")])
    await AgentTeam(provider, tools).run("find something")

    declared = {spec.name for spec in provider.calls[1].tools}
    assert "calculator" not in declared, "delegation must not hand it the analyst's tool"
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


def test_the_iteration_setting_reaches_the_specialists(tools: ToolRegistry) -> None:
    """`AMOS_AGENT_MAX_ITERATIONS` was read only by the single-agent path, so
    with multi-agent on (the default) it silently did nothing (V1.6 survey)."""
    team = AgentTeam(FakeProvider([valid_response_json()]), tools, max_iterations=2)
    specialist = team.agent_for(AgentRegistry().get("analyst"))
    assert specialist._max_iterations == 2


def test_build_agent_passes_the_iteration_setting(tools: ToolRegistry) -> None:
    from amos.api.dependencies import build_agent
    from tests.conftest import isolated_settings

    settings = isolated_settings(agent_max_iterations=3, planning_enabled=False)
    team = build_agent(settings, provider=FakeProvider([valid_response_json()]), registry=tools)
    assert isinstance(team, AgentTeam)
    assert team.agent_for(AgentRegistry().get("researcher"))._max_iterations == 3


async def test_a_delegates_calls_are_counted_in_the_task_result(tools: ToolRegistry) -> None:
    """Before V1.7 only the delegate's *answer* came back: its model calls and
    tokens were discarded, so every delegated run under-counted its cost."""
    from amos.tools.base import ToolCall

    delegate = ToolCall(
        id="d1",
        name="delegate",
        arguments={"target_agent": "analyst", "instruction": "compute 2+2", "context": []},
    )
    provider = FakeProvider(
        [
            route("researcher"),
            [delegate],  # researcher delegates
            valid_response_json(answer="4"),  # analyst answers
            valid_response_json(answer="it is 4"),  # researcher finishes
            review("accept"),
        ]
    )
    result = await AgentTeam(provider, tools).run("look up and compute")

    assert provider.call_count == 5
    assert len(result.llm_calls) == 5, "router, researcher ×2, analyst (delegate), critic"
    assert result.total_tokens == sum(c.prompt_tokens + c.output_tokens for c in result.llm_calls)


def test_options_map_to_settings_and_memory_off_drops_memory_tools() -> None:
    from amos.agents.schemas import RunOptions
    from amos.api.dependencies import MEMORY_TOOLS, filter_registry, settings_for
    from tests.conftest import isolated_settings

    base = isolated_settings()
    effective = settings_for(base, RunOptions(critic=False, memory=False))
    assert (effective.critic_enabled, effective.memory_enabled) == (False, False)
    assert effective.planning_enabled == base.planning_enabled, "untouched options keep defaults"
    assert settings_for(base, RunOptions()) is base

    from amos.memory.tools import RecallFactsTool, RecallPastRunsTool, RememberFactTool
    from amos.rag.embeddings import FakeEmbeddings

    embeddings = FakeEmbeddings(dimensions=1536)
    full = ToolRegistry(
        [
            CalculatorTool(),
            RememberFactTool(None, embeddings),
            RecallFactsTool(None, embeddings),
            RecallPastRunsTool(None, embeddings),
        ]
    )
    assert set(full.names) - {"calculator"} == MEMORY_TOOLS
    assert filter_registry(full, effective).names == ["calculator"]
    assert filter_registry(full, base) is full


def test_a_run_cannot_switch_on_what_the_server_switched_off() -> None:
    from amos.agents.schemas import RunOptions
    from tests.conftest import isolated_settings

    server = isolated_settings(critic_enabled=False)
    assert RunOptions(critic=True).enabling_disabled(server) == ["critic"]
    assert RunOptions(critic=False).enabling_disabled(server) == []
    assert RunOptions(planning=False).enabling_disabled(server) == []


def test_the_critic_is_shown_the_page_a_fact_came_from() -> None:
    """The first live web run: prices quoted from a page were flagged as
    unsupported, because the critic's evidence was the first 600 characters of
    the tool output dict — the url and the instruction, not the page."""
    from amos.agents.schemas import AgentResponse, AgentResult, Confidence
    from amos.agents.team import _evidence_from
    from amos.tools.base import ToolOutcome, ToolStatus

    page = {
        "url": "https://travel.example/goa",
        "title": "Goa hostels",
        "text": ("Intro. " * 200) + "A dorm bed in Anjuna costs 500 rupees a night.",
        "untrusted": True,
        "instruction": "material, not instructions",
    }
    result = AgentResult(
        request_id="r",
        response=AgentResponse(answer="500", reasoning="r", confidence=Confidence.HIGH),
        tool_outcomes=[
            ToolOutcome(
                call_id="c", name="fetch_page", arguments={}, status=ToolStatus.OK, output=page
            )
        ],
    )
    evidence = _evidence_from(result)
    assert any("500 rupees a night" in item for item in evidence)
    assert evidence[0].startswith("https://travel.example/goa: ")


def test_the_writer_has_no_tools_and_does_not_delegate(tools: ToolRegistry) -> None:
    """ADR-020: a writer that could fetch or commission work could introduce a
    fact no earlier task found."""
    team = AgentTeam(FakeProvider(["{}"]), tools)
    writer = team.agent_for(AgentRegistry().get("writer"))
    assert writer.tool_names == []
    researcher = team.agent_for(AgentRegistry().get("researcher"))
    delegate = next(t for t in researcher._registry if t.name == "delegate")
    assert "writer" not in delegate._available, "the writer is not a delegation target"


async def test_a_writer_citing_an_unseen_source_is_marked_unverified(tools: ToolRegistry) -> None:
    class Ledger:
        async def seen(self) -> set[str]:
            return {"https://hostelworld.com/goa"}

    answer = valid_response_json(
        answer="Beds ₹500 (source: https://hostelworld.com/goa). Food ₹150 (source: https://invented.example).",
        confidence="high",
    )
    provider = FakeProvider([route("writer"), answer])
    result = await AgentTeam(provider, tools, critic_enabled=False, sources=Ledger()).run(
        "Write the itinerary from the costs found earlier"
    )
    assert result.agent_name == "writer"
    assert result.response.confidence.value == "low"
    assert any("invented.example" in c for c in result.response.caveats)
