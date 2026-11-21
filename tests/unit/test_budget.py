"""The per-run budget (ADR-019): a guarantee in code, at the seams that see every call."""

from __future__ import annotations

import pytest

from amos.budget import RunBudget, budget_scope, charge_llm_call, charge_tool_call
from amos.errors import BudgetExhaustedError
from amos.llm.base import LLMRequest
from amos.llm.fake import FakeProvider
from amos.llm.recording import RecordingProvider
from amos.observability import call_scope


def budget(**limits: int) -> RunBudget:
    return RunBudget(
        max_llm_calls=limits.get("llm", 2),
        max_tool_calls=limits.get("tools", 2),
        max_page_fetches=limits.get("fetches", 1),
    )


async def test_without_a_budget_nothing_is_limited() -> None:
    """The eval harness and tests run outside any run, and must not be capped."""
    for _ in range(100):
        await charge_llm_call("agent")
        assert await charge_tool_call("calculator") is None


async def test_model_calls_stop_at_the_limit() -> None:
    provider = RecordingProvider(FakeProvider(["ok"]))
    with budget_scope(budget(llm=2)):
        await provider.complete(LLMRequest(prompt="1"), timeout=5)
        await provider.complete(LLMRequest(prompt="2"), timeout=5)
        with pytest.raises(BudgetExhaustedError):
            await provider.complete(LLMRequest(prompt="3"), timeout=5)


async def test_synthesis_may_run_past_the_limit() -> None:
    """So a run that spent its budget can still combine what its tasks found."""
    provider = RecordingProvider(FakeProvider(["ok"]))
    with budget_scope(budget(llm=1)):
        await provider.complete(LLMRequest(prompt="1"), timeout=5)
        with call_scope(purpose="synthesis"):
            await provider.complete(LLMRequest(prompt="combine"), timeout=5)


async def test_tool_calls_past_the_limit_are_denied_with_a_reason() -> None:
    with budget_scope(budget(tools=1)):
        assert await charge_tool_call("calculator") is None
        reason = await charge_tool_call("calculator")
    assert reason and "Answer with what you have" in reason


async def test_page_fetches_have_their_own_limit() -> None:
    with budget_scope(budget(tools=10, fetches=1)):
        assert await charge_tool_call("fetch_page") is None
        assert "pages" in (await charge_tool_call("fetch_page") or "")
        assert await charge_tool_call("web_search") is None, "searching is still allowed"


async def test_a_denied_tool_call_reaches_the_agent_as_an_outcome() -> None:
    from amos.agents.tool_agent import ToolUsingAgent
    from amos.tools.base import ToolCall, ToolStatus
    from amos.tools.builtin import CalculatorTool
    from amos.tools.registry import ToolRegistry

    agent = ToolUsingAgent(FakeProvider(["{}"]), ToolRegistry([CalculatorTool()]))
    with budget_scope(budget(tools=0)):
        outcome = await agent._invoke(
            ToolCall(id="c", name="calculator", arguments={"expression": "1+1"})
        )
    assert outcome.status is ToolStatus.DENIED
    assert "tool calls" in (outcome.error or "")


def test_an_exhausted_budget_is_not_retried() -> None:
    from amos.orchestration.executor import is_retryable

    assert is_retryable(BudgetExhaustedError("spent")) is False


def test_the_api_answers_an_exhausted_budget_as_429() -> None:
    from amos.api.app import _status_for

    assert _status_for(BudgetExhaustedError("spent")) == 429
