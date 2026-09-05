"""Orchestrator: planner + executor + synthesis, end to end on fakes."""

from __future__ import annotations

import json

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.errors import ProviderTimeoutError
from amos.llm.base import LLMCallRecord
from amos.llm.fake import FakeProvider
from amos.orchestration.orchestrator import Orchestrator
from amos.orchestration.state import TaskState
from tests.conftest import valid_response_json


def plan_json(*tasks: dict[str, object]) -> str:
    return json.dumps({"reasoning": "r", "tasks": list(tasks)})


def task(task_id: str, *deps: str) -> dict[str, object]:
    return {"id": task_id, "description": f"do {task_id}", "depends_on": list(deps)}


def result(answer: str = "done") -> AgentResult:
    return AgentResult(
        request_id="r",
        response=AgentResponse(answer=answer, reasoning="x", confidence=Confidence.HIGH),
        llm_calls=[LLMCallRecord(provider="fake", model="m", prompt_tokens=1, output_tokens=1)],
        total_tokens=2,
    )


class ScriptedRunner:
    tool_names = ["calculator"]

    def __init__(self, behaviour: dict[str, object] | None = None) -> None:
        self.behaviour = behaviour or {}
        self.goals: list[str] = []

    async def run(self, goal: str) -> AgentResult:
        self.goals.append(goal)
        for key, outcome in self.behaviour.items():
            if key in goal:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome  # type: ignore[return-value]
        return result()


async def no_sleep(_seconds: float) -> None: ...


def build(provider: FakeProvider, runner: ScriptedRunner, **kwargs: object) -> Orchestrator:
    from amos.orchestration.executor import Executor

    orchestrator = Orchestrator(provider, runner, **kwargs)  # type: ignore[arg-type]
    orchestrator._executor = Executor(runner, max_attempts=3, sleep=no_sleep)
    return orchestrator


async def test_single_task_plan_skips_synthesis() -> None:
    """One task's answer IS the answer. Spending a call to restate it would be
    waste on a 20-request/day quota."""
    provider = FakeProvider([plan_json(task("t1"))])
    runner = ScriptedRunner({"do t1": result("42")})

    outcome = await build(provider, runner).run("goal")

    assert outcome.response.answer == "42"
    assert provider.call_count == 1, "planning only — no synthesis call"
    assert outcome.outcome == "COMPLETED"


async def test_multi_task_plan_synthesises() -> None:
    provider = FakeProvider(
        [plan_json(task("t1"), task("t2")), valid_response_json(answer="combined")]
    )
    runner = ScriptedRunner()

    outcome = await build(provider, runner).run("goal")

    assert outcome.response.answer == "combined"
    assert provider.call_count == 2, "plan + synthesis"
    assert len(outcome.tasks) == 2


async def test_tasks_are_reported_with_their_states() -> None:
    provider = FakeProvider([plan_json(task("t1"), task("t2", "t1")), valid_response_json()])
    outcome = await build(provider, ScriptedRunner()).run("goal")

    states = {t.plan_ref: t.state for t in outcome.tasks}
    assert states == {"t1": "SUCCEEDED", "t2": "SUCCEEDED"}
    assert outcome.tasks[1].depends_on == ["t1"]


async def test_partial_completion_is_surfaced_with_a_caveat() -> None:
    provider = FakeProvider(
        [plan_json(task("t1"), task("t2")), valid_response_json(answer="partial")]
    )
    runner = ScriptedRunner({"do t2": ProviderTimeoutError("boom")})

    outcome = await build(provider, runner).run("goal")

    assert outcome.outcome == "PARTIALLY_COMPLETED"
    assert any("1 of 2" in c for c in outcome.response.caveats)


async def test_total_failure_costs_no_synthesis_call() -> None:
    """Nothing to synthesise means no reason to pay for a call."""
    provider = FakeProvider([plan_json(task("t1"))])
    runner = ScriptedRunner({"do t1": ProviderTimeoutError("boom")})

    outcome = await build(provider, runner).run("goal")

    assert outcome.outcome == "FAILED"
    assert provider.call_count == 1
    assert outcome.response.confidence is Confidence.LOW
    assert outcome.tasks[0].state == TaskState.PERMANENTLY_FAILED.value


async def test_planner_and_task_llm_calls_are_all_recorded() -> None:
    """The trace must account for every call, including the planner's."""
    provider = FakeProvider([plan_json(task("t1"), task("t2")), valid_response_json()])
    outcome = await build(provider, ScriptedRunner()).run("goal")

    # 1 planning + 2 task calls + 1 synthesis
    assert len(outcome.llm_calls) == 4
    assert outcome.total_tokens == sum(c.prompt_tokens + c.output_tokens for c in outcome.llm_calls)
