"""Plan validation: the barrier between model output and persisted state."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from amos.orchestration.plan import Plan, PlannedTask


def task(task_id: str, *deps: str) -> dict[str, object]:
    return {"id": task_id, "description": f"do {task_id}", "depends_on": list(deps)}


def test_simple_plan_validates() -> None:
    plan = Plan.model_validate({"tasks": [task("t1"), task("t2", "t1")]})
    assert len(plan.tasks) == 2


def test_empty_plan_is_rejected() -> None:
    """A planner returning zero tasks has failed, not succeeded trivially."""
    with pytest.raises(ValidationError):
        Plan.model_validate({"tasks": []})


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Plan.model_validate({"tasks": [task("t1"), task("t1")]})


def test_dependency_on_unknown_task_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not in the plan"):
        Plan.model_validate({"tasks": [task("t1", "ghost")]})


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(ValidationError, match="depends on itself"):
        Plan.model_validate({"tasks": [task("t1", "t1")]})


@pytest.mark.parametrize(
    "tasks",
    [
        pytest.param([task("t1", "t2"), task("t2", "t1")], id="two-cycle"),
        pytest.param([task("t1", "t3"), task("t2", "t1"), task("t3", "t2")], id="three-cycle"),
        pytest.param(
            [task("t1"), task("t2", "t1", "t4"), task("t3", "t2"), task("t4", "t3")],
            id="cycle-behind-a-valid-prefix",
        ),
    ],
)
def test_cycles_are_rejected(tasks: list[dict[str, object]]) -> None:
    """A cyclic plan reaching the database would be a run that can never finish."""
    with pytest.raises(ValidationError, match="cycle"):
        Plan.model_validate({"tasks": tasks})


def test_cycle_error_names_the_tasks_involved() -> None:
    """So the planner's repair prompt can say what was wrong."""
    with pytest.raises(ValidationError) as exc:
        Plan.model_validate({"tasks": [task("t1", "t2"), task("t2", "t1")]})
    message = str(exc.value)
    assert "t1" in message and "t2" in message


def test_duplicate_dependencies_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        Plan.model_validate({"tasks": [task("t1"), task("t2", "t1", "t1")]})


def test_too_many_tasks_is_rejected() -> None:
    """A bound on blast radius: 50 tasks is a runaway, not a plan."""
    with pytest.raises(ValidationError):
        Plan.model_validate({"tasks": [task(f"t{i}") for i in range(50)]})


def test_topological_order_puts_dependencies_first() -> None:
    plan = Plan.model_validate({"tasks": [task("t3", "t2"), task("t1"), task("t2", "t1")]})
    order = [t.id for t in plan.topological_order()]
    assert order.index("t1") < order.index("t2") < order.index("t3")


def test_topological_order_includes_every_task_once() -> None:
    plan = Plan.model_validate({"tasks": [task("t1"), task("t2", "t1"), task("t3", "t1")]})
    order = [t.id for t in plan.topological_order()]
    assert sorted(order) == ["t1", "t2", "t3"]


def test_diamond_dependencies_are_valid() -> None:
    """t1 → {t2, t3} → t4. Not a cycle, and a shape a naive check gets wrong."""
    plan = Plan.model_validate(
        {"tasks": [task("t1"), task("t2", "t1"), task("t3", "t1"), task("t4", "t2", "t3")]}
    )
    order = [t.id for t in plan.topological_order()]
    assert order.index("t1") == 0
    assert order.index("t4") == 3


def test_description_is_required_and_bounded() -> None:
    with pytest.raises(ValidationError):
        PlannedTask.model_validate({"id": "t1", "description": ""})
    with pytest.raises(ValidationError):
        PlannedTask.model_validate({"id": "t1", "description": "x" * 501})
