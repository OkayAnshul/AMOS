"""The live run record (ADR-015): written as it happens, attributed, in order.

These tests run a real orchestrator, team and executor against a scripted
provider, through the same provider stack `build_provider` builds — retry
outside, recording inside — and read the result back as stored rows.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.schemas import RunOptions
from amos.api.dependencies import build_agent
from amos.api.persistence import RunService
from amos.auth import Actor
from amos.database.engine import session_scope
from amos.database.models import LLMCall, Run
from amos.errors import ProviderUnavailableError
from amos.llm.base import LLMRequest, LLMResponse
from amos.llm.fake import FakeProvider
from amos.llm.recording import RecordingProvider
from amos.llm.retry import RetryingProvider
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import isolated_settings, valid_response_json

pytestmark = pytest.mark.asyncio


def plan(*tasks: tuple[str, list[str]]) -> str:
    return json.dumps(
        {
            "reasoning": "split it",
            "tasks": [
                {"id": ref, "description": f"do {ref}", "depends_on": deps} for ref, deps in tasks
            ],
        }
    )


def route(agent: str) -> str:
    return json.dumps({"agent": agent, "reason": f"{agent} fits"})


ACCEPT = json.dumps({"verdict": "accept", "reasoning": "supported"})


async def no_sleep(_: float) -> None:
    """Retry tests must not wait out the jitter."""


def stack(inner: object) -> RetryingProvider:
    """What `build_provider` builds, around a scripted provider."""
    return RetryingProvider(RecordingProvider(inner), sleep=no_sleep)  # type: ignore[arg-type]


async def run_and_trace(
    service: RunService, actor: Actor, goal: str = "a goal", **kwargs: object
) -> tuple[uuid.UUID, dict[str, object]]:
    _, run_id = await service.execute(goal, actor, request_id="r", **kwargs)  # type: ignore[arg-type]
    assert run_id is not None
    trace = await service.get_trace(run_id, actor)
    assert trace is not None
    return run_id, trace.model_dump()


async def forget(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
    async with session_scope(factory) as session:
        await session.execute(delete(Run).where(Run.id == run_id))


def team_agent(provider: object, factory: object, **settings: object) -> object:
    return build_agent(
        isolated_settings(delegation_enabled=False, **settings),
        provider=provider,  # type: ignore[arg-type]
        registry=ToolRegistry([CalculatorTool()]),
        session_factory=factory,
    )


async def test_every_call_is_attributed_and_ordered(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """The headline: a two-task run reads back as planner → (router, agent,
    critic) per task → synthesis, each call tagged with its task and agent."""
    provider = stack(
        FakeProvider(
            [
                plan(("t1", []), ("t2", ["t1"])),
                route("analyst"),
                valid_response_json(answer="one"),
                ACCEPT,
                route("researcher"),
                valid_response_json(answer="two"),
                ACCEPT,
                valid_response_json(answer="both"),
            ]
        )
    )
    service = RunService(team_agent(provider, db_factory), db_factory)
    run_id, trace = await run_and_trace(service, factory_actor)
    try:
        calls = trace["llm_calls"]
        assert [c["purpose"] for c in calls] == [
            "planner",
            "router",
            "agent",
            "critic",
            "router",
            "agent",
            "critic",
            "synthesis",
        ]
        assert [c["task_ref"] for c in calls] == [None, "t1", "t1", "t1", "t2", "t2", "t2", None]
        assert [c["agent"] for c in calls if c["purpose"] == "agent"] == ["analyst", "researcher"]
        assert {c["agent"] for c in calls if c["purpose"] == "critic"} == {"critic"}
        seqs = [c["seq"] for c in calls]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

        tasks = {t["plan_ref"]: t for t in trace["tasks"]}
        assert tasks["t2"]["depends_on"] == ["t1"], "dependencies as plan refs, drawable"
        assert (tasks["t1"]["agent"], tasks["t2"]["agent"]) == ("analyst", "researcher")

        kinds = [e["kind"] for e in trace["events"]]
        for expected in (
            "run.started",
            "plan.created",
            "task.started",
            "task.routed",
            "critic.verdict",
            "task.succeeded",
            "run.finished",
        ):
            assert expected in kinds, f"missing event {expected}"
        assert kinds[0] == "run.started" and kinds[-1] == "run.finished"
        verdicts = [e for e in trace["events"] if e["kind"] == "critic.verdict"]
        assert [v["payload"]["verdict"] for v in verdicts] == ["accept", "accept"]

        assert trace["total_tokens"] == sum(
            c["prompt_tokens"] + c["output_tokens"] for c in calls
        ), "the total is the sum of what was stored"
    finally:
        await forget(db_factory, run_id)


async def test_calls_are_written_before_the_run_finishes(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """'As it happens', checked from inside the run: by the time the agent makes
    its third call, the first two are already rows."""
    seen_during_run: list[int] = []

    class Peeking(FakeProvider):
        async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
            if len(self.calls) == 2:
                async with session_scope(db_factory) as session:
                    count = (await session.execute(select(func.count(LLMCall.id)))).scalar_one()
                    seen_during_run.append(int(count))
            return await super().complete(request, timeout=timeout)

    before = 0
    async with session_scope(db_factory) as session:
        before = int((await session.execute(select(func.count(LLMCall.id)))).scalar_one())

    provider = stack(Peeking([plan(("t1", [])), route("analyst"), valid_response_json(), ACCEPT]))
    service = RunService(team_agent(provider, db_factory), db_factory)
    run_id, trace = await run_and_trace(service, factory_actor)
    try:
        assert seen_during_run and seen_during_run[0] >= before + 2
        assert len(trace["llm_calls"]) == 4, "recorded once — not again by the end-of-run batch"
    finally:
        await forget(db_factory, run_id)


async def test_a_retried_503_is_two_rows_one_of_them_the_failure(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """Recording sits inside the retry, so the retry is visible, not hidden."""
    provider = stack(
        FakeProvider(
            [
                ProviderUnavailableError("busy", details={"status": 503}),
                plan(("t1", [])),
                valid_response_json(),
            ]
        )
    )
    agent = team_agent(provider, db_factory, multi_agent_enabled=False)
    service = RunService(agent, db_factory)
    run_id, trace = await run_and_trace(service, factory_actor)
    try:
        planner = [c for c in trace["llm_calls"] if c["purpose"] == "planner"]
        assert len(planner) == 2
        assert planner[0]["error"].startswith("ProviderUnavailableError (503)")
        assert planner[1]["error"] is None
    finally:
        await forget(db_factory, run_id)


async def test_concurrent_tasks_are_attributed_to_themselves(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """Independent tasks run under `gather`; each call must carry its own task."""
    provider = stack(
        FakeProvider(
            [
                plan(("t1", []), ("t2", []), ("t3", [])),
                valid_response_json(),
                valid_response_json(),
                valid_response_json(),
                valid_response_json(answer="all"),
            ]
        )
    )
    agent = team_agent(provider, db_factory, multi_agent_enabled=False)
    service = RunService(agent, db_factory)
    run_id, trace = await run_and_trace(service, factory_actor)
    try:
        refs = sorted(c["task_ref"] for c in trace["llm_calls"] if c["purpose"] == "agent")
        assert refs == ["t1", "t2", "t3"]
    finally:
        await forget(db_factory, run_id)


async def test_a_running_sync_run_is_stored_as_running(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """The sync path never wrote RUNNING; a live view saw RECEIVED all along."""
    statuses: list[str] = []

    class Peeking(FakeProvider):
        async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
            async with session_scope(db_factory) as session:
                rows = (await session.execute(select(Run.status))).scalars().all()
                statuses.extend(rows)
            return await super().complete(request, timeout=timeout)

    provider = stack(Peeking([valid_response_json()]))
    agent = team_agent(provider, db_factory, multi_agent_enabled=False, planning_enabled=False)
    service = RunService(agent, db_factory)
    run_id, _ = await run_and_trace(service, factory_actor)
    try:
        assert "RUNNING" in statuses
    finally:
        await forget(db_factory, run_id)


async def test_options_are_stored_and_build_their_own_agent(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """ADR-016: a run's options travel on its row and get a per-run agent."""
    built: list[RunOptions] = []
    shared = FakeProvider([valid_response_json()])

    def factory(options: RunOptions) -> object:
        built.append(options)
        return team_agent(
            stack(FakeProvider([valid_response_json(answer="per-run")])),
            db_factory,
            planning_enabled=False,
            multi_agent_enabled=False,
        )

    service = RunService(
        team_agent(stack(shared), db_factory, planning_enabled=False),
        db_factory,
        agent_factory=factory,
    )
    run_id, trace = await run_and_trace(
        service, factory_actor, options=RunOptions(planning=False, critic=False)
    )
    try:
        assert built == [RunOptions(planning=False, critic=False)]
        assert trace["options"] == {"planning": False, "critic": False}
        assert shared.call_count == 0, "the shared agent did not run"
    finally:
        await forget(db_factory, run_id)


async def test_a_run_that_spends_its_budget_ends_with_the_reason(
    db_factory: async_sessionmaker[AsyncSession], factory_actor: Actor
) -> None:
    """ADR-019 end to end: two tasks, a budget of three model calls. The second
    task cannot start its work; the run ends with what it has and the record
    says why — not a crash, and not a retry of something that cannot succeed."""
    from amos.budget import RunBudget

    provider = stack(
        FakeProvider(
            [
                plan(("t1", []), ("t2", [])),
                valid_response_json(answer="first"),
                valid_response_json(answer="second"),
                valid_response_json(answer="combined"),
            ]
        )
    )
    agent = team_agent(provider, db_factory, multi_agent_enabled=False)
    service = RunService(
        agent,
        db_factory,
        budget=lambda: RunBudget(max_llm_calls=2, max_tool_calls=10, max_page_fetches=1),
    )
    run_id, trace = await run_and_trace(service, factory_actor)
    try:
        kinds = [e["kind"] for e in trace["events"]]
        assert "budget.exhausted" in kinds
        exhausted = next(e for e in trace["events"] if e["kind"] == "budget.exhausted")
        assert exhausted["payload"]["limit_kind"] == "llm_calls"
        states = sorted(t["state"] for t in trace["tasks"])
        assert states == ["PERMANENTLY_FAILED", "SUCCEEDED"]
        failed = next(t for t in trace["tasks"] if t["state"] == "PERMANENTLY_FAILED")
        assert "BudgetExhaustedError" in (failed["error"] or "")
        assert failed["attempt_count"] == 1, "a spent budget is not retried"
        assert trace["status"] == "PARTIALLY_COMPLETED"
    finally:
        await forget(db_factory, run_id)
