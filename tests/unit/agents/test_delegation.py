"""Agent-to-agent delegation and its bounds (V1.3, ADR-012).

`AgentTask` was defined at V0.7 and had no caller for six milestones. The tests
that matter here are not "does delegation work" — they are the ones asserting
that an agent which can delegate cannot delegate *forever*, cannot delegate to
itself, and cannot acquire capability it was not given.
"""

from __future__ import annotations

import pytest

from amos.agents.delegation import (
    DelegateArgs,
    DelegateTool,
    DelegationBudget,
)
from amos.agents.messages import AgentTask
from amos.agents.registry import ANALYST, RESEARCHER, AgentRegistry
from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.agents.team import AgentTeam
from amos.llm.fake import FakeProvider
from amos.tools.base import ToolCall, ToolStatus
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import valid_response_json


class StubAgent:
    """Stands in for a specialist. Records what it was asked."""

    def __init__(self, answer: str = "42") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def run(self, goal: str) -> AgentResult:
        self.prompts.append(goal)
        return AgentResult(
            request_id="r",
            response=AgentResponse(
                answer=self.answer, reasoning="computed", confidence=Confidence.HIGH
            ),
        )


def delegate_call(target: str, instruction: str = "compute 15% of 20", **extra: object) -> ToolCall:
    return ToolCall(
        id="d1",
        name="delegate",
        arguments={"target_agent": target, "instruction": instruction, **extra},
    )


def build_tool(
    caller: str = "researcher",
    agent: StubAgent | None = None,
    *,
    depth: int = 0,
    budget: DelegationBudget | None = None,
    available: frozenset[str] = frozenset({"analyst"}),
) -> tuple[DelegateTool, StubAgent]:
    target = agent or StubAgent()
    tool = DelegateTool(
        caller,
        lambda name, *, depth: target,
        budget or DelegationBudget(),
        depth=depth,
        available=available,
    )
    return tool, target


# ---------- the contract ----------


async def test_delegation_produces_a_structured_agent_task() -> None:
    """§10 of the brief: agents exchange typed objects, never free-form prose.

    The instruction the delegate receives is the *presentation* of an AgentTask;
    the contract is the model, which is validated before anything runs.
    """
    tool, target = build_tool()

    outcome = await tool.execute(
        delegate_call("analyst", "compute 15 percent of 20", context=["the quota is 20 per day"])
    )

    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["agent"] == "analyst"
    assert outcome.output["answer"] == "42"
    # The context reaches the delegate, because it cannot see the caller's work.
    assert "the quota is 20 per day" in target.prompts[0]
    assert "compute 15 percent of 20" in target.prompts[0]


async def test_the_answer_is_labelled_as_another_agents_claim() -> None:
    """So the caller's model does not present a delegate's answer as something it
    established itself."""
    tool, _ = build_tool()

    outcome = await tool.execute(delegate_call("analyst"))

    assert outcome.output is not None
    assert "analyst" in outcome.output["note"]


async def test_invalid_arguments_are_rejected_before_anything_runs() -> None:
    """The model produced them, so they are untrusted input — the V0.2 rule."""
    tool, target = build_tool()

    outcome = await tool.execute(
        ToolCall(id="d1", name="delegate", arguments={"target_agent": "analyst"})
    )

    assert outcome.status is ToolStatus.INVALID_ARGS
    assert target.prompts == []


# ---------- the bounds ----------


async def test_an_agent_cannot_delegate_to_itself() -> None:
    tool, target = build_tool(caller="researcher", available=frozenset({"analyst"}))

    outcome = await tool.execute(delegate_call("researcher"))

    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["delegated"] is False
    assert target.prompts == [], "no agent should have been run"


async def test_an_unknown_agent_is_refused_with_the_real_list() -> None:
    """A refusal the model can act on beats an exception it cannot."""
    tool, target = build_tool()

    outcome = await tool.execute(delegate_call("wizard"))

    assert outcome.output is not None
    assert outcome.output["delegated"] is False
    assert outcome.output["available_agents"] == ["analyst"]
    assert target.prompts == []


async def test_the_budget_is_shared_across_a_run_not_per_agent() -> None:
    """A per-agent counter would let a chain of agents each spend the full
    allowance, which is not a budget."""
    budget = DelegationBudget(limit=2)
    first, first_target = build_tool("researcher", budget=budget)
    second, second_target = build_tool(
        "analyst", budget=budget, available=frozenset({"researcher"})
    )

    await first.execute(delegate_call("analyst"))
    await second.execute(delegate_call("researcher"))
    exhausted = await second.execute(delegate_call("researcher"))

    assert budget.spent == 2
    assert exhausted.output is not None
    assert exhausted.output["delegated"] is False
    assert "budget" in exhausted.output["reason"].lower()


async def test_an_exhausted_budget_still_lets_the_caller_finish() -> None:
    """It is a budget message, not an error: the caller can answer with what it
    has, and telling it why is what makes that possible."""
    budget = DelegationBudget(limit=0)
    tool, target = build_tool(budget=budget)

    outcome = await tool.execute(delegate_call("analyst"))

    assert outcome.status is ToolStatus.OK, "a spent budget is not a tool failure"
    assert target.prompts == []


# ---------- depth, enforced structurally ----------


def team(**kwargs: object) -> AgentTeam:
    return AgentTeam(
        FakeProvider([valid_response_json()]),
        ToolRegistry([CalculatorTool()]),
        **kwargs,  # type: ignore[arg-type]
    )


def test_delegate_is_present_below_the_depth_cap() -> None:
    assert "delegate" in team().agent_for(RESEARCHER, depth=0).tool_names


def test_delegate_is_absent_at_the_depth_cap() -> None:
    """The bound is the tool's *absence*, not a check inside it.

    A check could be argued past by a sufficiently persuasive payload; a tool
    that is not in the registry returns NOT_FOUND through machinery that never
    reads model output.
    """
    assert "delegate" not in team(max_delegation_depth=1).agent_for(RESEARCHER, depth=1).tool_names


def test_depth_zero_disables_delegation_entirely() -> None:
    assert "delegate" not in team(max_delegation_depth=0).agent_for(RESEARCHER).tool_names


def test_delegation_can_be_switched_off() -> None:
    """Each hop costs at least one LLM call against a 20/day quota."""
    assert "delegate" not in team(delegation_enabled=False).agent_for(RESEARCHER).tool_names


def test_a_delegate_does_not_inherit_the_callers_tools() -> None:
    """Delegation moves work, not authority.

    Otherwise an injection that talked a researcher into delegating would
    inherit whatever the analyst can do, and vice versa — a privilege-escalation
    path dressed as a feature.
    """
    built = team()
    researcher = built.agent_for(RESEARCHER, depth=0)
    analyst = built.agent_for(ANALYST, depth=0)

    assert "calculator" in analyst.tool_names
    assert "calculator" not in researcher.tool_names


def test_the_delegation_instruction_appears_only_with_the_tool() -> None:
    """A prompt promising a capability the registry denies produces an agent that
    repeatedly attempts a tool it cannot have, burning iterations."""
    built = team(max_delegation_depth=1)

    with_tool = built.agent_for(RESEARCHER, depth=0)._system_instruction  # type: ignore[attr-defined]
    without = built.agent_for(RESEARCHER, depth=1)._system_instruction  # type: ignore[attr-defined]

    assert "delegate" in with_tool
    assert "delegate" not in without


def test_the_instruction_names_the_other_agents_and_not_itself() -> None:
    built = team()
    instruction = built.agent_for(RESEARCHER, depth=0)._system_instruction  # type: ignore[attr-defined]

    assert "analyst" in instruction
    # Built from the registry, so adding an agent does not mean editing every
    # other agent's prompt.
    assert "- researcher:" not in instruction


def test_a_single_agent_registry_gets_no_delegate_tool() -> None:
    """Nobody to delegate to, so the tool would be an invitation to fail."""
    solo = AgentRegistry([RESEARCHER])
    assert "delegate" not in team(agents=solo).agent_for(RESEARCHER).tool_names


def test_an_agent_task_validates_its_fields() -> None:
    """The contract is the model. A misread sentence becomes a rejected field."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentTask(task_id="t", source_agent="a", target_agent="b", instruction="")


def test_delegate_args_reject_an_oversized_context() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DelegateArgs(target_agent="analyst", instruction="x", context=["c"] * 11)


# ---------- the scenario delegation exists for ----------


async def test_a_researcher_gets_arithmetic_done_without_gaining_a_calculator() -> None:
    """The concrete gap ADR-012 closes.

    A researcher retrieves "the quota is 20 per day" and is asked for 15% of it.
    `calculator` is not in its allowlist and must not become so — disjoint
    allowlists are what make routing meaningful. Before V1.3 its only options
    were to compute in its head or fail.

    The whole chain runs here with a fake provider scripting both models: the
    researcher delegates, the analyst calculates, the researcher answers.
    """
    provider = FakeProvider(
        [
            # The researcher hits the gap and delegates.
            [delegate_call("analyst", "What is 15 percent of 20?", context=["quota is 20/day"])],
            # ...the analyst reaches for its calculator...
            [ToolCall(id="c1", name="calculator", arguments={"expression": "20 * 0.15"})],
            # ...and answers.
            valid_response_json(answer="3"),
            # The researcher composes the final answer from what came back.
            valid_response_json(answer="15% of the 20/day quota is 3 requests."),
        ]
    )
    built = AgentTeam(provider, ToolRegistry([CalculatorTool()]), critic_enabled=False)
    researcher = built.agent_for(RESEARCHER, depth=0)

    result = await researcher.run("What is 15 percent of the documented daily quota?")

    assert "3" in result.response.answer
    names = [outcome.name for outcome in result.tool_outcomes]
    assert names == ["delegate"], "the researcher itself only called delegate"
    assert "calculator" not in researcher.tool_names, "and never acquired a calculator"


async def test_a_delegates_failure_surfaces_to_the_caller_rather_than_vanishing() -> None:
    """The caller's model decides what to do about it — the same trust boundary
    every other tool has. Silently returning an empty answer would let the caller
    present a gap as a result.
    """
    from amos.errors import ProviderTimeoutError

    class FailingAgent:
        async def run(self, goal: str) -> AgentResult:
            raise ProviderTimeoutError("the analyst timed out")

    tool = DelegateTool(
        "researcher",
        lambda name, *, depth: FailingAgent(),
        DelegationBudget(),
        available=frozenset({"analyst"}),
    )

    outcome = await tool.execute(delegate_call("analyst"))

    assert outcome.status is not ToolStatus.OK
    assert outcome.succeeded is False
