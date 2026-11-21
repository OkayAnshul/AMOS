"""The call context and the recording provider (ADR-015), without a database."""

from __future__ import annotations

import asyncio

import pytest

from amos.errors import ProviderRateLimitError, ProviderUnavailableError
from amos.llm.base import LLMRequest
from amos.llm.fake import FakeProvider
from amos.llm.recording import RecordingProvider, describe
from amos.observability import call_scope, get_call_context


def test_a_scope_is_reset_when_its_block_ends() -> None:
    with call_scope(purpose="planner"):
        assert get_call_context().purpose == "planner"
    assert get_call_context().purpose is None


def test_a_scope_is_reset_even_when_the_block_raises() -> None:
    """The whole reason for set-and-reset: an agent run that raises must not
    leave its agent name on every later call."""
    with pytest.raises(RuntimeError), call_scope(agent="analyst"):
        raise RuntimeError("boom")
    assert get_call_context().agent is None


def test_scopes_nest_and_unwind_in_order() -> None:
    with call_scope(task_ref="t1", purpose="agent"):
        with call_scope(purpose="critic", agent="critic"):
            context = get_call_context()
            assert (context.task_ref, context.purpose, context.agent) == ("t1", "critic", "critic")
        context = get_call_context()
        assert (context.task_ref, context.purpose, context.agent) == ("t1", "agent", None)


async def test_concurrent_tasks_keep_their_own_scope() -> None:
    """The executor gathers independent tasks; a scope opened inside each
    gathered coroutine must not bleed into its sibling."""
    seen: dict[str, str | None] = {}

    async def task(ref: str) -> None:
        with call_scope(task_ref=ref):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            seen[ref] = get_call_context().task_ref

    await asyncio.gather(task("t1"), task("t2"), task("t3"))
    assert seen == {"t1": "t1", "t2": "t2", "t3": "t3"}


async def test_wait_for_does_not_isolate_a_bare_set() -> None:
    """Why a bare `.set()` is not allowed: on 3.14 `wait_for` runs the coroutine
    in the same task, so its context change leaks to the caller. call_scope's
    reset is what keeps the caller clean."""

    async def inner() -> None:
        with call_scope(agent="delegate-target"):
            await asyncio.sleep(0)

    await asyncio.wait_for(inner(), timeout=1)
    assert get_call_context().agent is None


async def test_the_recording_provider_passes_through_without_a_run() -> None:
    inner = FakeProvider(["hello"])
    response = await RecordingProvider(inner).complete(LLMRequest(prompt="x"), timeout=5)
    assert response.text == "hello"


def test_a_stored_error_names_its_type_first() -> None:
    """The quota meter and the page classify stored errors by prefix."""
    day = ProviderRateLimitError("quota gone", details={"quota_scope": "day"})
    assert describe(day).startswith("ProviderRateLimitError (day): ")
    unavailable = ProviderUnavailableError("busy", details={"status": 503})
    assert describe(unavailable).startswith("ProviderUnavailableError (503): ")
