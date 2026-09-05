"""Planner: LLM output becomes a validated DAG, or it does not become anything."""

from __future__ import annotations

import json

import pytest

from amos.llm.base import LLMCallRecord
from amos.llm.fake import FakeProvider
from amos.orchestration.plan import Plan
from amos.orchestration.planner import Planner, PlanningError


def plan_json(*tasks: dict[str, object], reasoning: str = "because") -> str:
    return json.dumps({"reasoning": reasoning, "tasks": list(tasks)})


def task(task_id: str, *deps: str) -> dict[str, object]:
    return {"id": task_id, "description": f"do {task_id}", "depends_on": list(deps)}


async def test_valid_plan_is_returned_on_the_first_attempt() -> None:
    provider = FakeProvider([plan_json(task("t1"), task("t2", "t1"))])
    plan = await Planner(provider).plan("some goal")

    assert isinstance(plan, Plan)
    assert [t.id for t in plan.tasks] == ["t1", "t2"]
    assert provider.call_count == 1


async def test_cyclic_plan_is_rejected_and_repaired() -> None:
    """The planner is told what was wrong, so its second attempt can differ."""
    provider = FakeProvider(
        [
            plan_json(task("t1", "t2"), task("t2", "t1")),  # cycle
            plan_json(task("t1"), task("t2", "t1")),
        ]
    )
    plan = await Planner(provider, max_attempts=2).plan("goal")

    assert len(plan.tasks) == 2
    assert provider.call_count == 2
    assert "rejected" in (provider.calls[1].system_instruction or "")
    assert "cycle" in (provider.calls[1].system_instruction or "")


async def test_plan_referencing_an_unknown_task_is_rejected() -> None:
    provider = FakeProvider([plan_json(task("t1", "ghost")), plan_json(task("t1"))])
    plan = await Planner(provider, max_attempts=2).plan("goal")
    assert len(plan.tasks) == 1


async def test_exhausted_attempts_raise_planning_error() -> None:
    provider = FakeProvider([plan_json(task("t1", "t2"), task("t2", "t1"))])
    with pytest.raises(PlanningError) as exc:
        await Planner(provider, max_attempts=2).plan("goal")

    assert provider.call_count == 2
    assert "cycle" in str(exc.value.details["last_error"])


async def test_malformed_json_is_treated_as_an_invalid_plan() -> None:
    provider = FakeProvider(["not json at all", plan_json(task("t1"))])
    plan = await Planner(provider, max_attempts=2).plan("goal")
    assert len(plan.tasks) == 1


async def test_empty_plan_is_rejected() -> None:
    provider = FakeProvider([json.dumps({"tasks": []}), plan_json(task("t1"))])
    plan = await Planner(provider, max_attempts=2).plan("goal")
    assert len(plan.tasks) == 1


async def test_planner_records_its_llm_calls_including_rejected_ones() -> None:
    """A rejected plan still cost tokens; the trace must show them."""
    provider = FakeProvider(["garbage", plan_json(task("t1"))])
    calls: list[LLMCallRecord] = []
    await Planner(provider, max_attempts=2).plan("goal", calls)

    assert len(calls) == 2
    assert calls[0].repair_attempt == 0
    assert calls[1].repair_attempt == 1
