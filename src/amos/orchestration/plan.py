"""Plan schema and validation.

A plan comes from an LLM, so it is untrusted structure. It is checked for
*shape* by Pydantic and for *meaning* here — unique ids, resolvable dependencies,
and no cycles — before a single row is written.

Validating before persisting matters: a cyclic plan that reaches the database is
a run that can never complete, holding rows that look live forever.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from amos.errors import AmosError

MAX_TASKS = 10
MAX_DEPENDENCIES = 5


class InvalidPlanError(AmosError):
    """A plan that is structurally valid JSON but not a runnable DAG."""


class PlannedTask(BaseModel):
    """One task as proposed by the planner.

    `id` is planner-local ("t1", "t2") rather than a UUID, because the model has
    to reference it in `depends_on` and short symbolic ids are far more reliable
    for it than UUIDs — which it will happily invent or mistype.
    """

    id: str = Field(min_length=1, max_length=32)
    description: str = Field(
        min_length=1,
        max_length=500,
        description="A self-contained instruction. Assume the executor sees no other task.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        max_length=MAX_DEPENDENCIES,
        description="Ids of tasks whose results this one needs.",
    )

    @field_validator("depends_on")
    @classmethod
    def _no_duplicate_dependencies(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("depends_on contains duplicates")
        return value


class Plan(BaseModel):
    """A validated, acyclic task graph."""

    reasoning: str = Field(default="", max_length=2000)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=MAX_TASKS)

    @model_validator(mode="after")
    def _validate_graph(self) -> Plan:
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task ids must be unique")

        known = set(ids)
        for task in self.tasks:
            for dependency in task.depends_on:
                if dependency not in known:
                    raise ValueError(
                        f"task '{task.id}' depends on '{dependency}', which is not in the plan"
                    )
                if dependency == task.id:
                    raise ValueError(f"task '{task.id}' depends on itself")

        if (cycle := _find_cycle(self.tasks)) is not None:
            raise ValueError(f"plan contains a dependency cycle: {' -> '.join(cycle)}")
        return self

    def topological_order(self) -> list[PlannedTask]:
        """Tasks in an order where dependencies always come first.

        Only meaningful because the graph is known acyclic — the validator
        guarantees that before this can be called.
        """
        by_id = {task.id: task for task in self.tasks}
        ordered: list[PlannedTask] = []
        seen: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in seen:
                return
            seen.add(task_id)
            for dependency in by_id[task_id].depends_on:
                visit(dependency)
            ordered.append(by_id[task_id])

        for task in self.tasks:
            visit(task.id)
        return ordered


def _find_cycle(tasks: list[PlannedTask]) -> list[str] | None:
    """Return a cycle if one exists, naming the tasks involved.

    Iterative DFS with an explicit stack rather than recursion: a hostile or
    confused plan could otherwise blow the Python stack, and a crash is a worse
    failure mode than a rejection. Returning the actual cycle rather than a bare
    boolean means the error message can tell the planner what it did wrong.
    """
    by_id = {task.id: task for task in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(by_id, WHITE)

    for start in by_id:
        if colour[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                if colour[node] == BLACK:
                    continue
                colour[node] = GREY
                path.append(node)
            dependencies = by_id[node].depends_on
            if index < len(dependencies):
                stack.append((node, index + 1))
                nxt = dependencies[index]
                if colour[nxt] == GREY:
                    return [*path[path.index(nxt) :], nxt]
                if colour[nxt] == WHITE:
                    stack.append((nxt, 0))
            else:
                colour[node] = BLACK
                if path and path[-1] == node:
                    path.pop()
    return None
