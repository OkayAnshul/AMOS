"""Keep model calls under the per-minute quota, by waiting rather than being refused.

Found on the first live run of a complex goal (V1.10): the planner split a trip
into three research tasks, the executor ran them concurrently, and together they
passed flash-lite's 15 requests a minute. One task's calls came back 429, were
retried after half a second — far too soon for a one-minute window — and the
run lost the task, then its dependents, then the final call.

A sliding-window limiter, process-wide: each real call takes a slot, and when
the last minute's slots are spent the caller waits until the oldest one ages
out. Waiting is not part of a call's own timeout — it is not the provider being
slow — but it is inside the task timeout, which still bounds the whole (ADR-009).
The wait is recorded as an event, so the page shows it instead of a silent gap.

**Per process.** The API and a worker each have their own limiter, so running
both against one key can still exceed the quota. The demo runs synchronously in
the API; `make demo` says so.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from amos.database.recorder import record_event
from amos.llm.base import LLMProvider, LLMRequest, LLMResponse
from amos.observability import log_event

logger = logging.getLogger(__name__)

#: Waits shorter than this are not worth an event on the page.
REPORT_WAITS_OVER_SECONDS = 1.0


class MinuteRateLimiter:
    def __init__(
        self,
        per_minute: int,
        *,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be at least 1")
        self.per_minute = per_minute
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Take a slot, waiting if the window is full. Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= self._window:
                    self._stamps.popleft()
                if len(self._stamps) < self.per_minute:
                    self._stamps.append(now)
                    return waited
                delay = self._window - (now - self._stamps[0]) + 0.05
                waited += delay
                await self._sleep(delay)


class RateLimitedProvider:
    """Waits for a slot before each real call. Sits inside the retry, so a
    retried attempt takes a slot too — it spends quota like any other."""

    def __init__(self, inner: LLMProvider, limiter: MinuteRateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter
        self.name = inner.name

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        waited = await self._limiter.acquire()
        if waited >= REPORT_WAITS_OVER_SECONDS:
            log_event(logger, "llm.throttled", waited_seconds=round(waited, 1))
            await record_event(
                "provider.throttled",
                waited_seconds=round(waited, 1),
                per_minute=self._limiter.per_minute,
            )
        return await self._inner.complete(request, timeout=timeout)
