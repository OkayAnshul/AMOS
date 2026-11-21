"""What one run may spend, enforced in code (ADR-019).

Set per run by `RunService` and the worker, like the recorder, and checked at
the seams that already see every call: `RecordingProvider` for model calls,
`ToolUsingAgent._invoke` for tool calls and page fetches. Outside a run — the
eval harness, a unit test — there is no budget and nothing is limited.

A budget is a guarantee, so it is code. A prompt asking the model to be frugal
is a hope.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from amos.database.recorder import record_event
from amos.errors import BudgetExhaustedError

#: Purposes that may run past the model-call budget: combining what the tasks
#: found is how a run that hit its budget still answers with what it has.
EXEMPT_PURPOSES = frozenset({"synthesis"})


@dataclass
class RunBudget:
    max_llm_calls: int
    max_tool_calls: int
    max_page_fetches: int
    llm_calls: int = 0
    tool_calls: int = 0
    page_fetches: int = 0
    reported: set[str] = field(default_factory=set)

    def snapshot(self) -> dict[str, int]:
        return {
            "llm_calls": self.llm_calls,
            "max_llm_calls": self.max_llm_calls,
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "page_fetches": self.page_fetches,
            "max_page_fetches": self.max_page_fetches,
        }


_current: ContextVar[RunBudget | None] = ContextVar("run_budget", default=None)


def current_budget() -> RunBudget | None:
    return _current.get()


@contextmanager
def budget_scope(budget: RunBudget | None) -> Iterator[None]:
    token = _current.set(budget)
    try:
        yield
    finally:
        _current.reset(token)


async def _report(budget: RunBudget, kind: str, limit: int) -> None:
    if kind not in budget.reported:
        budget.reported.add(kind)
        await record_event("budget.exhausted", limit_kind=kind, limit=limit, **budget.snapshot())


async def charge_llm_call(purpose: str | None) -> None:
    """Count one real model call; raise once the run's budget is spent."""
    budget = current_budget()
    if budget is None:
        return
    if purpose not in EXEMPT_PURPOSES and budget.llm_calls >= budget.max_llm_calls:
        await _report(budget, "llm_calls", budget.max_llm_calls)
        raise BudgetExhaustedError(
            f"This run has used its {budget.max_llm_calls} model calls.",
            details={"limit_kind": "llm_calls", "limit": budget.max_llm_calls},
        )
    budget.llm_calls += 1


async def charge_tool_call(tool_name: str) -> str | None:
    """Count one tool call. Returns a reason to deny it, or None to allow it.

    A denial, not an exception: the agent is told why and can answer with what
    it already has, which is the "partial answer, not a crash" ADR-019 asks for.
    """
    budget = current_budget()
    if budget is None:
        return None
    if budget.tool_calls >= budget.max_tool_calls:
        await _report(budget, "tool_calls", budget.max_tool_calls)
        return (
            f"This run has used its {budget.max_tool_calls} tool calls. "
            "Answer with what you have, and say what is missing."
        )
    if tool_name == "fetch_page" and budget.page_fetches >= budget.max_page_fetches:
        await _report(budget, "page_fetches", budget.max_page_fetches)
        return (
            f"This run has read its {budget.max_page_fetches} pages. "
            "Answer from the pages already read, and say what is missing."
        )
    budget.tool_calls += 1
    if tool_name == "fetch_page":
        budget.page_fetches += 1
    return None
