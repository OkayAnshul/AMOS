"""The bounded agent loop.

The provider is scripted, so these test the loop's behaviour — not the model's.
"""

from __future__ import annotations

import pytest

from amos.agents.tool_agent import ToolUsingAgent
from amos.errors import ToolLoopExhaustedError
from amos.llm.fake import FakeProvider
from amos.tools.base import ToolCall, ToolStatus
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import valid_response_json


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([CalculatorTool()])


def calc_call(expression: str, call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name="calculator", arguments={"expression": expression})


async def test_answer_without_tools_makes_no_tool_calls(
    registry: ToolRegistry, valid_json: str
) -> None:
    provider = FakeProvider([valid_json])
    result = await ToolUsingAgent(provider, registry).run("What is your name?")

    assert result.tool_outcomes == []
    assert result.response.answer == "42"


async def test_tool_is_executed_and_result_returned(registry: ToolRegistry) -> None:
    provider = FakeProvider([[calc_call("2340 * 0.17")], valid_response_json(answer="397.8")])
    result = await ToolUsingAgent(provider, registry).run("What is 17% of 2340?")

    assert len(result.tool_outcomes) == 1
    outcome = result.tool_outcomes[0]
    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["result"] == pytest.approx(397.8)
    assert result.response.answer == "397.8"


async def test_hallucinated_tool_name_does_not_crash(registry: ToolRegistry) -> None:
    """Expected behaviour, not exceptional. The model is told and can recover."""
    provider = FakeProvider(
        [
            [ToolCall(id="x", name="send_email", arguments={"to": "a@b.c"})],
            valid_response_json(),
        ]
    )
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert result.tool_outcomes[0].status is ToolStatus.NOT_FOUND
    # The error names what does exist, so the model can pick a real tool.
    assert "calculator" in (result.tool_outcomes[0].error or "")


async def test_invalid_arguments_are_rejected_before_execution(
    registry: ToolRegistry,
) -> None:
    provider = FakeProvider(
        [
            [ToolCall(id="x", name="calculator", arguments={"wrong_field": "2+2"})],
            valid_response_json(),
        ]
    )
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert result.tool_outcomes[0].status is ToolStatus.INVALID_ARGS
    assert "expression" in (result.tool_outcomes[0].error or "")


async def test_tool_failure_is_fed_back_and_loop_continues(
    registry: ToolRegistry,
) -> None:
    """A failed call must not abort the request — the model gets a second chance."""
    provider = FakeProvider(
        [
            [calc_call("1/0")],                    # fails
            [calc_call("2340 * 0.17", "c2")],      # model corrects itself
            valid_response_json(answer="397.8"),
        ]
    )
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert len(result.tool_outcomes) == 2
    assert result.tool_outcomes[0].status is ToolStatus.INVALID_ARGS
    assert result.tool_outcomes[1].status is ToolStatus.OK


async def test_loop_cap_is_enforced(registry: ToolRegistry) -> None:
    """A model that never stops calling tools must be stopped by the code."""
    provider = FakeProvider([[calc_call("1+1")]])  # repeats forever
    agent = ToolUsingAgent(provider, registry, max_iterations=3)

    with pytest.raises(ToolLoopExhaustedError) as exc:
        await agent.run("goal")

    assert exc.value.iterations == 3
    assert exc.value.tool_calls == 3
    assert provider.call_count == 3, "must stop calling the provider, not just fail at the end"


async def test_max_iterations_must_be_at_least_one(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError):
        ToolUsingAgent(FakeProvider(["x"]), registry, max_iterations=0)


async def test_multiple_tools_in_one_turn_all_execute(registry: ToolRegistry) -> None:
    provider = FakeProvider(
        [
            [calc_call("2+2", "a"), calc_call("3*3", "b")],
            valid_response_json(),
        ]
    )
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert len(result.tool_outcomes) == 2
    assert all(o.status is ToolStatus.OK for o in result.tool_outcomes)


async def test_tools_are_declared_to_the_provider(registry: ToolRegistry, valid_json: str) -> None:
    provider = FakeProvider([valid_json])
    await ToolUsingAgent(provider, registry).run("goal")

    assert [spec.name for spec in provider.calls[0].tools] == ["calculator"]


async def test_tokens_include_every_call(registry: ToolRegistry) -> None:
    provider = FakeProvider([[calc_call("2+2")], valid_response_json()])
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert len(result.llm_calls) >= 2
    assert result.total_tokens == sum(
        c.prompt_tokens + c.output_tokens for c in result.llm_calls
    )


async def test_prompt_injection_in_tool_output_does_not_change_permissions(
    tmp_path: object,
) -> None:
    """A tool returning instructions is returning a string.

    The model may or may not be fooled. The system must not be: the registry
    still contains one tool, and a call to anything else is NOT_FOUND regardless
    of what the tool output said.
    """
    registry = ToolRegistry([CalculatorTool()])
    provider = FakeProvider(
        [
            [calc_call("1+1")],
            # pretend the model obeyed injected instructions in the tool output
            [ToolCall(id="evil", name="read_file", arguments={"path": "/etc/passwd"})],
            valid_response_json(),
        ]
    )
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert result.tool_outcomes[1].status is ToolStatus.NOT_FOUND


async def test_no_extra_call_when_the_tool_turn_returns_a_valid_answer(
    registry: ToolRegistry,
) -> None:
    """The schema is requested on every turn, so a non-tool turn is already the
    final answer. An extra finalise call would waste a third of the daily
    free-tier quota."""
    provider = FakeProvider([[calc_call("2340 * 0.17")], valid_response_json(answer="397.8")])
    result = await ToolUsingAgent(provider, registry).run("goal")

    assert provider.call_count == 2, "one tool turn + one answering turn, no finalise"
    assert len(result.llm_calls) == 2
    assert result.response.answer == "397.8"


async def test_schema_is_requested_alongside_tools(
    registry: ToolRegistry, valid_json: str
) -> None:
    from amos.agents.schemas import AgentResponse

    provider = FakeProvider([valid_json])
    await ToolUsingAgent(provider, registry).run("goal")

    request = provider.calls[0]
    assert request.response_schema is AgentResponse
    assert request.tools, "tools and schema must be sent together"
