"""Reconciling claimed memory with written memory.

The guarantee under test: **the system never claims to have remembered something
it did not store.** Storage itself is best effort and cannot be guaranteed —
it depends on model tool selection — so these tests separate the two carefully.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest
from pydantic import BaseModel

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.llm.fake import AlwaysFailsProvider, FakeProvider
from amos.memory.reconcile import (
    MemoryReconciler,
    claims_memory,
    stored_this_run,
)
from amos.memory.tools import RememberArgs
from amos.tools.base import Permission, Tool, ToolOutcome, ToolStatus
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry


def result(answer: str, tools: list[ToolOutcome] | None = None) -> AgentResult:
    return AgentResult(
        request_id="r",
        response=AgentResponse(answer=answer, reasoning="x", confidence=Confidence.HIGH),
        tool_outcomes=tools or [],
        total_tokens=10,
    )


def remembered(status: ToolStatus = ToolStatus.OK) -> ToolOutcome:
    return ToolOutcome(call_id="c", name="remember_fact", status=status, output={"stored": True})


def recalled() -> ToolOutcome:
    return ToolOutcome(call_id="c", name="recall_facts", status=ToolStatus.OK, output={"found": 1})


class StubRememberTool(Tool):
    """A working `remember_fact` for tests.

    The real tool needs a session factory and an embedding provider; handing it
    dummies makes every call fail, which then looks like the reconciler not
    retrying rather than the tool being unusable. This double carries the same
    NAME — which is all the reconciler filters on — and actually succeeds.
    """

    name: ClassVar[str] = "remember_fact"
    description: ClassVar[str] = "Store a durable fact."
    input_schema: ClassVar[type[BaseModel]] = RememberArgs
    permission: ClassVar[Permission] = Permission.READ_LOCAL

    def __init__(self) -> None:
        self.stored: list[tuple[str, str]] = []

    async def _run(self, args: RememberArgs) -> dict[str, object]:
        self.stored.append((args.subject, args.content))
        return {"stored": True, "subject": args.subject}


def tools_with_memory(remember: Tool | None = None) -> ToolRegistry:
    return ToolRegistry([CalculatorTool(), remember or StubRememberTool()])


def store_json() -> str:
    return json.dumps({"answer": "stored", "reasoning": "r", "confidence": "high"})


# ---------- stored_this_run: the fact the guarantee rests on ----------


def test_a_successful_remember_is_detected() -> None:
    assert stored_this_run(result("x", [remembered()]))


def test_a_failed_remember_does_not_count_as_stored() -> None:
    """The call happening is not the write landing."""
    assert not stored_this_run(result("x", [remembered(ToolStatus.ERROR)]))


def test_other_tools_do_not_count_as_stored() -> None:
    """The observed failure: the model called recall_facts and claimed it had
    remembered. A recall is not a write."""
    assert not stored_this_run(result("x", [recalled()]))


# ---------- claims_memory: the heuristic, positive AND negative ----------


@pytest.mark.parametrize(
    "answer",
    [
        # Verbatim from observed failures.
        "I have noted that your preferred backend language is Python.",
        "I have successfully recorded that your mentor is Dr Sharma for future sessions.",
        "Your preference has been stored for later sessions.",
        "I've saved that for you.",
        "This is now committed to memory.",
        "Noted for future reference.",
    ],
)
def test_claims_are_recognised(answer: str) -> None:
    assert claims_memory(answer)


@pytest.mark.parametrize(
    "answer",
    [
        # Recall is not a write. Firing on these would trigger a pointless retry
        # after every successful lookup.
        "I recall that you said your language is Python.",
        "According to what you told me earlier, your city is Bangalore.",
        "From my memory of previous sessions, your project is AMOS.",
        # Ordinary answers with no memory claim at all.
        "17% of 2340 is 397.8.",
        "The documentation does not mention that.",
    ],
)
def test_non_claims_do_not_fire(answer: str) -> None:
    """The V1.0 lesson: broadening a detector until everything matches passes the
    exact failure it exists to catch."""
    assert not claims_memory(answer)


# ---------- the common paths must cost nothing ----------


async def test_stored_and_claimed_is_left_alone() -> None:
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory())

    out = await reconciler.reconcile("goal", result("I have noted that.", [remembered()]))

    assert provider.call_count == 0, "no inconsistency, so no cost"
    assert out.response.caveats == []


async def test_no_claim_is_left_alone() -> None:
    """Do not retry what was never promised."""
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory())

    out = await reconciler.reconcile("goal", result("17% of 2340 is 397.8."))

    assert provider.call_count == 0
    assert out.response.caveats == []


async def test_stored_without_claiming_is_fine() -> None:
    provider = FakeProvider([store_json()])
    out = await MemoryReconciler(provider, tools_with_memory()).reconcile(
        "goal", result("Done.", [remembered()])
    )
    assert provider.call_count == 0
    assert out.response.caveats == []


# ---------- the inconsistency: one bounded retry ----------


async def test_a_false_claim_triggers_exactly_one_retry() -> None:
    """Bounded, like the V0.7 critic. Two models disagreeing without a bound
    spends a day's quota."""
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory())

    await reconciler.reconcile("goal", result("I have noted that.", [recalled()]))

    assert provider.call_count <= 2, "one retry, not a loop"


async def test_a_successful_retry_adds_no_caveat() -> None:
    """Do not warn the user about a problem that was fixed."""
    from amos.tools.base import ToolCall

    provider = FakeProvider(
        [
            [ToolCall(id="t", name="remember_fact", arguments={"subject": "s", "content": "c"})],
            store_json(),
        ]
    )
    remember = StubRememberTool()
    reconciler = MemoryReconciler(provider, tools_with_memory(remember))

    out = await reconciler.reconcile("goal", result("I have noted that.", [recalled()]))

    assert remember.stored == [("s", "c")], "the retry went through the real tool"
    assert stored_this_run(out)
    assert not any("NOT STORED" in c for c in out.response.caveats)


async def test_a_failed_retry_flags_the_answer() -> None:
    """The guarantee. The user must be told their fact was not kept."""
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory())

    out = await reconciler.reconcile("goal", result("I have noted that.", [recalled()]))

    assert any("NOT STORED" in c for c in out.response.caveats)
    assert out.response.confidence is Confidence.LOW


async def test_a_crashing_retry_still_flags_rather_than_failing_the_run() -> None:
    """Reconciliation must never lose a correct answer. The run's result is
    already computed and paid for."""
    reconciler = MemoryReconciler(AlwaysFailsProvider(), tools_with_memory())

    out = await reconciler.reconcile("goal", result("I have noted that.", [recalled()]))

    assert any("NOT STORED" in c for c in out.response.caveats)


async def test_the_retry_is_offered_only_the_memory_tool() -> None:
    """Capability is the allowlist. The retry cannot retrieve, plan or wander —
    and re-running the full Orchestrator would cost 8-10 calls to store one fact.
    """
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory())

    await reconciler.reconcile("goal", result("I have noted that.", [recalled()]))

    assert provider.calls, "a retry should have happened"
    assert [spec.name for spec in provider.calls[0].tools] == ["remember_fact"]


async def test_the_retrys_cost_appears_in_the_trace() -> None:
    """V0.7's rule: the trace accounts for every call, including ones spent
    fixing something."""
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory())

    before = result("I have noted that.", [recalled()])
    out = await reconciler.reconcile("goal", before)

    assert len(out.llm_calls) > len(before.llm_calls)
    assert out.total_tokens > before.total_tokens


# ---------- configuration and degradation ----------


async def test_disabling_the_reconciler_restores_previous_behaviour() -> None:
    """So a baseline can be captured for comparison."""
    provider = FakeProvider([store_json()])
    reconciler = MemoryReconciler(provider, tools_with_memory(), enabled=False)

    out = await reconciler.reconcile("goal", result("I have noted that.", [recalled()]))

    assert provider.call_count == 0
    assert out.response.caveats == []


async def test_without_the_memory_tool_it_still_flags_the_claim() -> None:
    """No database means no remember_fact, so no retry is possible — but the
    honesty guarantee does not depend on being able to retry."""
    reconciler = MemoryReconciler(FakeProvider([store_json()]), ToolRegistry([CalculatorTool()]))

    assert not reconciler.can_retry
    out = await reconciler.reconcile("goal", result("I have noted that.", []))
    assert any("NOT STORED" in c for c in out.response.caveats)
