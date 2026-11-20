"""A transient provider error is retried once, inside the caller's deadline (ADR-014).

The bar for this wrapper is what it must NOT do as much as what it does: it must
never retry a 429 (the quota is spent), an auth error, a malformed request or a
timeout, and it must never let a call outlive the bound its caller set — ADR-009's
chain of per-call < task < visibility timeout depends on that.
"""

from __future__ import annotations

import random

import pytest

from amos.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from amos.llm.base import LLMRequest, LLMResponse
from amos.llm.fake import FakeProvider
from amos.llm.retry import RetryingProvider


class Clock:
    """A fake monotonic clock; `sleep` advances it instead of waiting."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def wrap(provider: object, clock: Clock, **kwargs: object) -> RetryingProvider:
    return RetryingProvider(
        provider,  # type: ignore[arg-type]
        sleep=clock.sleep,
        clock=clock.time,
        rng=random.Random(7),
        **kwargs,  # type: ignore[arg-type]
    )


REQUEST = LLMRequest(prompt="hello")


async def test_a_503_is_retried_once_and_the_retry_answers() -> None:
    clock = Clock()
    inner = FakeProvider([ProviderUnavailableError("503 high demand"), "fine"])
    response = await wrap(inner, clock).complete(REQUEST, timeout=30.0)

    assert response.text == "fine"
    assert inner.call_count == 2
    assert len(clock.slept) == 1 and 0.0 <= clock.slept[0] <= 1.0, "jittered, under a second"


async def test_a_second_503_is_raised_not_retried_again() -> None:
    clock = Clock()
    inner = FakeProvider([ProviderUnavailableError("503"), ProviderUnavailableError("503 again")])

    with pytest.raises(ProviderUnavailableError, match="again"):
        await wrap(inner, clock).complete(REQUEST, timeout=30.0)
    assert inner.call_count == 2


@pytest.mark.parametrize(
    "error",
    [
        ProviderRateLimitError("429", details={"quota_scope": "minute"}),
        ProviderRateLimitError("429", details={"quota_scope": "day"}),
        ProviderAuthError("401"),
        ProviderError("400 bad request", details={"status": 400}),
        ProviderTimeoutError("slow"),
    ],
    ids=["429-minute", "429-day", "auth", "4xx", "timeout"],
)
async def test_nothing_but_unavailable_is_retried(error: ProviderError) -> None:
    clock = Clock()
    inner = FakeProvider([error, "never reached"])

    with pytest.raises(type(error)):
        await wrap(inner, clock).complete(REQUEST, timeout=30.0)
    assert inner.call_count == 1
    assert clock.slept == []


class SlowThenUnavailable:
    """Fails with a 503 after `elapsed` seconds of the caller's budget, then
    records the timeout it was given for the retry."""

    name = "slow"

    def __init__(self, clock: Clock, elapsed: float) -> None:
        self.clock = clock
        self.elapsed = elapsed
        self.timeouts: list[float] = []

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        self.timeouts.append(timeout)
        if len(self.timeouts) == 1:
            self.clock.now += self.elapsed
            raise ProviderUnavailableError("503")
        return LLMResponse(text="ok", model="m", provider="slow")


async def test_the_retry_gets_only_what_is_left_of_the_deadline() -> None:
    """A fresh timeout per attempt would double the per-call worst case and let a
    task breach ADR-009's 300 s bound on work that was going to succeed."""
    clock = Clock()
    inner = SlowThenUnavailable(clock, elapsed=10.0)

    await wrap(inner, clock).complete(REQUEST, timeout=30.0)

    assert inner.timeouts[0] == 30.0
    assert inner.timeouts[1] == pytest.approx(30.0 - 10.0 - clock.slept[0])
    assert sum([10.0, *clock.slept]) + inner.timeouts[1] == pytest.approx(30.0)


async def test_no_retry_when_too_little_of_the_deadline_is_left() -> None:
    clock = Clock()
    inner = SlowThenUnavailable(clock, elapsed=28.5)

    with pytest.raises(ProviderUnavailableError):
        await wrap(inner, clock, min_retry_seconds=3.0).complete(REQUEST, timeout=30.0)
    assert len(inner.timeouts) == 1


async def test_the_wrapper_is_a_provider() -> None:
    from amos.llm.base import LLMProvider

    wrapped = wrap(FakeProvider(["x"]), Clock())
    assert isinstance(wrapped, LLMProvider)
    assert wrapped.name == "fake"


# ---------- the Gemini boundary ----------


def test_a_gemini_server_error_becomes_unavailable_with_its_status() -> None:
    """Before V1.6 a 5xx became a bare ProviderError with no status: nothing
    could tell a 503 from a malformed request, and the API answered HTTP 500."""
    import asyncio

    from google.genai import errors as genai_errors

    from amos.llm.gemini import GeminiProvider

    body = {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}

    class FailingModels:
        async def generate_content(self, **_: object) -> object:
            raise genai_errors.ServerError(503, body)

    from types import SimpleNamespace

    provider = GeminiProvider(api_key="test-key-not-real", model="m")
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=FailingModels()))  # type: ignore[assignment]

    with pytest.raises(ProviderUnavailableError) as caught:
        asyncio.run(provider.complete(REQUEST, timeout=5.0))
    assert caught.value.details["status"] == 503


def test_unavailable_is_answered_as_503_by_the_api() -> None:
    from amos.api.app import _status_for

    assert _status_for(ProviderUnavailableError("503")) == 503


def test_build_provider_wraps_the_real_provider() -> None:
    from amos.api.dependencies import build_provider
    from tests.conftest import isolated_settings

    assert isinstance(build_provider(isolated_settings()), RetryingProvider)
