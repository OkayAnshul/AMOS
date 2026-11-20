"""One retry for a transient provider failure, inside the caller's deadline (ADR-014).

A wrapper over `LLMProvider` rather than a policy in each caller: the planner,
router, agents, critic and synthesis all call `complete`, and five copies of a
retry policy drift. Applied once, in `build_provider`.

Three rules, each load-bearing:

**Only `ProviderUnavailableError` (5xx).** A 429 means the quota is spent and a
retry spends more of it; an auth error or a 4xx fails identically every time; a
timeout already consumed the whole budget.

**Once.** A second consecutive 503 is a provider in trouble, not a blip, and the
executor and the worker each have their own, coarser retry above this one.

**The deadline is shared.** The retry gets what is *left* of the caller's
`timeout`, not a fresh one. ADR-009 orders the bounds per-call < task (300 s) <
visibility (600 s); a fresh timeout per attempt would double the per-call worst
case and let a task outlive its bound on work that was going to succeed.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

from amos.errors import ProviderUnavailableError
from amos.llm.base import LLMProvider, LLMRequest, LLMResponse
from amos.observability import log_event
from amos.orchestration.retry import backoff_delay
from amos.telemetry.metrics import instruments, safe_labels

logger = logging.getLogger(__name__)

#: Below this much remaining budget a retry is not attempted: a call given two
#: seconds is a call set up to time out, which costs a request and returns nothing.
MIN_RETRY_SECONDS = 3.0

#: Base for the jittered delay: full jitter over [0, 1 s]. Enough to step off a
#: synchronised retry; short enough that the deadline, not the delay, dominates.
RETRY_BASE_SECONDS = 1.0


class RetryingProvider:
    """Wraps a provider; retries a 5xx once within the original deadline."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        min_retry_seconds: float = MIN_RETRY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._inner = inner
        self._min_retry_seconds = min_retry_seconds
        self._sleep = sleep
        self._clock = clock
        self._rng = rng
        self.name = inner.name

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        deadline = self._clock() + timeout
        try:
            return await self._inner.complete(request, timeout=timeout)
        except ProviderUnavailableError as exc:
            delay = backoff_delay(0, base=RETRY_BASE_SECONDS, rng=self._rng)
            remaining = deadline - self._clock() - delay
            if remaining < self._min_retry_seconds:
                log_event(
                    logger,
                    "llm.retry_skipped",
                    provider=self.name,
                    remaining_seconds=round(remaining, 2),
                )
                raise
            log_event(
                logger,
                "llm.retry",
                provider=self.name,
                status=exc.details.get("status"),
                delay_seconds=round(delay, 3),
                remaining_seconds=round(remaining, 2),
            )
            instruments().llm_retries.add(1, safe_labels(provider=self.name))
            await self._sleep(delay)
            return await self._inner.complete(request, timeout=remaining)
