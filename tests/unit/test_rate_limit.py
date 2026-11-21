"""Model calls wait for a slot instead of being refused (V1.10)."""

from __future__ import annotations

import asyncio

import pytest

from amos.llm.base import LLMRequest
from amos.llm.fake import FakeProvider
from amos.llm.ratelimit import MinuteRateLimiter, RateLimitedProvider


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_calls_within_the_limit_do_not_wait() -> None:
    clock = Clock()
    limiter = MinuteRateLimiter(3, clock=clock.time, sleep=clock.sleep)
    assert [await limiter.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]


async def test_the_next_call_waits_for_the_oldest_slot_to_age_out() -> None:
    clock = Clock()
    limiter = MinuteRateLimiter(2, clock=clock.time, sleep=clock.sleep)
    await limiter.acquire()
    clock.now = 10.0
    await limiter.acquire()
    waited = await limiter.acquire()
    assert waited == pytest.approx(50.05, abs=0.1), "until the call at t=0 is a minute old"


async def test_concurrent_callers_never_exceed_the_limit_in_any_minute() -> None:
    """The failure it exists for: three tasks calling at once."""
    clock = Clock()
    limiter = MinuteRateLimiter(4, clock=clock.time, sleep=clock.sleep)
    stamps: list[float] = []

    async def caller() -> None:
        for _ in range(3):
            await limiter.acquire()
            stamps.append(clock.now)

    await asyncio.gather(caller(), caller(), caller())
    assert len(stamps) == 9
    for t in stamps:
        assert sum(1 for s in stamps if t <= s < t + 60) <= 4


async def test_the_provider_takes_a_slot_per_call() -> None:
    clock = Clock()
    limiter = MinuteRateLimiter(1, clock=clock.time, sleep=clock.sleep)
    provider = RateLimitedProvider(FakeProvider(["a", "b"]), limiter)
    await provider.complete(LLMRequest(prompt="1"), timeout=5)
    await provider.complete(LLMRequest(prompt="2"), timeout=5)
    assert clock.now >= 60.0, "the second call waited for the first slot"


def test_build_provider_limits_below_the_published_quota() -> None:
    from amos.api.dependencies import build_provider
    from tests.conftest import isolated_settings

    provider = build_provider(
        isolated_settings(llm_minute_request_limit=15, fallback_llm_model="", groq_api_key="")
    )
    limited = provider.inner  # type: ignore[attr-defined]
    assert isinstance(limited, RateLimitedProvider)
    assert limited._limiter.per_minute == 14


def test_build_provider_assembles_the_fallback_chain_in_order() -> None:
    """ADR-021: Gemini primary, the Gemini fallback model, Groq — each with its own limiter."""
    from amos.api.dependencies import build_provider
    from amos.llm.fallback import FallbackProvider, model_of
    from tests.conftest import isolated_settings

    provider = build_provider(
        isolated_settings(fallback_llm_model="gemma-4-26b-a4b-it", groq_api_key="test-groq")
    )
    chain = provider.inner  # type: ignore[attr-defined]
    assert isinstance(chain, FallbackProvider)
    assert [f"{m.name}/{model_of(m)}" for m in chain.members] == [
        "gemini/fake-model",
        "gemini/gemma-4-26b-a4b-it",
        "groq/openai/gpt-oss-120b",
    ]
    limiters = {id(m._limiter) for m in chain.members}  # type: ignore[attr-defined]
    assert len(limiters) == 3, "each model has its own per-minute quota"
