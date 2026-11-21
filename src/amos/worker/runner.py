"""The worker loop.

Polls for queued runs, executes them, records the outcome. Deliberately boring:
all the interesting guarantees live in `queue.py`'s SQL and in the executor's
state machine, and a worker that adds its own cleverness is a worker with its own
bugs.

## Why polling rather than notifications

Postgres has `LISTEN`/`NOTIFY`, which would remove the poll interval. It is not
used because it adds a second mechanism to reason about (a dedicated connection,
reconnect handling, and notifications that are lost if nobody is listening) to
save a latency floor that nobody is currently measuring. Recorded in ADR-003's
tradeoffs, and the right thing to reconsider if poll latency ever becomes visible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.schemas import RunOptions
from amos.api.persistence import record_episode
from amos.auth import Actor
from amos.budget import RunBudget, budget_scope
from amos.database.engine import session_scope
from amos.database.recorder import RunRecorder, record_event, recording
from amos.database.repository import RunRepository
from amos.memory.episodic import EpisodicMemory
from amos.observability import call_scope, log_event, set_current_run_id, set_request_id
from amos.telemetry.metrics import instruments, safe_labels
from amos.telemetry.tracing import continued_trace
from amos.worker.queue import (
    DEFAULT_VISIBILITY_TIMEOUT,
    claim_next_run,
    give_up,
    reclaim_abandoned_runs,
)

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    """Identify the worker in `runs.claimed_by`, so a stuck run names its owner."""
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    """Claims and executes queued runs."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        agent_factory: Callable[[RunOptions | None], object],
        *,
        worker_id: str | None = None,
        poll_interval: float = 2.0,
        max_attempts: int = 3,
        visibility_timeout: int = DEFAULT_VISIBILITY_TIMEOUT,
        episodic: Callable[[AsyncSession, Actor], EpisodicMemory] | None = None,
        budget: Callable[[], RunBudget] | None = None,
    ) -> None:
        self._factory = session_factory
        # Before V1.7 the worker recorded no episodes, so queued runs were
        # invisible to `recall_past_runs`; it shares the sync path's helper now.
        self._episodic = episodic
        self._budget = budget
        self._agent_factory = agent_factory
        self.worker_id = worker_id or default_worker_id()
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._visibility_timeout = visibility_timeout
        self._running = False
        self.runs_completed = 0

    async def run_once(self) -> bool:
        """Claim and execute at most one run. Returns whether work was found."""
        async with session_scope(self._factory) as session:
            claimed = await claim_next_run(session, self.worker_id, max_attempts=self._max_attempts)
        if claimed is None:
            return False

        set_request_id(str(claimed.run_id)[:16])
        # V1.1: the run id has to be in context here too, not only on the
        # synchronous path. The plan store and the task checkpoint read it
        # (ADR-010), so without this a reclaimed run would silently re-execute
        # from the start — in the worker, which is the only place a reclaim
        # happens and therefore the only place resumption matters.
        set_current_run_id(str(claimed.run_id))
        # The worker acts AS the run's owner. Giving it an unscoped
        # repository would make the one component that touches every user's
        # runs the one component with no isolation (ADR-013).
        actor = Actor(id=claimed.user_id, name="worker") if claimed.user_id else None
        if actor is None:
            log_event(logger, "worker.run_has_no_owner", run_id=str(claimed.run_id))
            return True

        # Execution happens outside any transaction — the V0.3 reasoning still
        # applies, and matters more here: a run can take minutes, and holding a
        # pooled connection open across it would starve every other worker.
        options = RunOptions.model_validate(claimed.options) if claimed.options else None
        started = time.perf_counter()
        # Recorded as it happens, like the sync path (ADR-015), with the run's
        # attempt on every row — a reclaimed run's earlier attempt stays visible.
        with (
            recording(RunRecorder(self._factory)),
            call_scope(run_attempt=claimed.attempt_count),
            budget_scope(self._budget() if self._budget is not None else None),
        ):
            await record_event(
                "run.claimed",
                worker=self.worker_id,
                attempt=claimed.attempt_count,
                options=options.given() if options else {},
            )
            try:
                agent = self._agent_factory(options)
                # Continues the trace of the request that enqueued this run,
                # rather than starting a second one that nothing links to the first.
                with continued_trace(
                    claimed.trace_parent,
                    "run.execute.worker",
                    **{
                        "amos.run_id": str(claimed.run_id),
                        "amos.worker_id": self.worker_id,
                        "amos.attempt": claimed.attempt_count,
                    },
                ):
                    result = await agent.run(claimed.goal)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - a worker must not die on one run
                instruments().runs.add(1, safe_labels(outcome="FAILED"))
                await self._record_failure(claimed.run_id, actor, exc)
                await record_event("run.failed", error=type(exc).__name__)
                set_current_run_id(None)
                return True

            async with session_scope(self._factory) as session:
                repo = RunRepository(session, actor)
                stored = await repo.get_trace(claimed.run_id)
                if stored is not None:
                    await repo.record_success(stored, result)
            await record_event("run.finished", outcome=result.outcome)

        memory_on = not (options is not None and options.memory is False)
        if self._episodic is not None and memory_on:
            await record_episode(
                self._factory, self._episodic, actor, claimed.run_id, claimed.goal, result
            )
        instruments().runs.add(1, safe_labels(outcome=result.outcome))
        instruments().run_duration.record(
            int((time.perf_counter() - started) * 1000), safe_labels(outcome=result.outcome)
        )

        self.runs_completed += 1
        log_event(logger, "worker.completed", run_id=str(claimed.run_id))
        set_current_run_id(None)
        return True

    async def _record_failure(self, run_id: uuid.UUID, actor: Actor, exc: Exception) -> None:
        """Record a failed attempt, and stop retrying once the budget is spent.

        Leaving it QUEUED after the final attempt would make it a poison message:
        reclaimed forever, failing forever, consuming the queue.
        """
        async with session_scope(self._factory) as session:
            repo = RunRepository(session, actor)
            stored = await repo.get_trace(run_id)
            if stored is None:
                return
            message = getattr(exc, "message", str(exc))
            if stored.attempt_count >= self._max_attempts:
                await give_up(
                    session, run_id, f"{type(exc).__name__} after {stored.attempt_count} attempts"
                )
            else:
                await repo.record_failure(stored, type(exc).__name__, message)
        log_event(logger, "worker.run_failed", run_id=str(run_id), error=type(exc).__name__)

    async def sweep(self) -> int:
        """Return runs abandoned by dead workers to the queue — or, on their
        final attempt, to the dead-letter queue."""
        async with session_scope(self._factory) as session:
            return await reclaim_abandoned_runs(
                session,
                visibility_timeout=self._visibility_timeout,
                max_attempts=self._max_attempts,
            )

    async def run_forever(self, *, sweep_every: int = 30) -> None:
        """Poll until stopped.

        Sleeps only when there was nothing to do, so a backlog drains at full
        speed rather than one run per poll interval.
        """
        self._running = True
        polls_since_sweep = 0
        log_event(logger, "worker.started", worker=self.worker_id)

        while self._running:
            try:
                if polls_since_sweep >= sweep_every:
                    await self.sweep()
                    polls_since_sweep = 0

                found_work = await self.run_once()
                if not found_work:
                    polls_since_sweep += 1
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # A worker that dies on an unexpected error stops draining the
                # queue entirely. Log, back off, continue.
                log_event(logger, "worker.loop_error", error=type(exc).__name__)
                await asyncio.sleep(self._poll_interval)

        log_event(logger, "worker.stopped", worker=self.worker_id, completed=self.runs_completed)

    def stop(self) -> None:
        self._running = False
