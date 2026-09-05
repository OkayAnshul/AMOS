"""Retry policy: bounded, exponential, jittered.

Three properties, each load-bearing:

**Bounded.** A retry budget is a cost ceiling. Unbounded retries against a
20-requests-per-day quota would burn a day's budget on one broken task.

**Exponential.** If a dependency is briefly overloaded, retrying immediately
makes it worse. Doubling the wait gives it room to recover.

**Jittered.** This is the one that is easy to skip and expensive to omit. Without
jitter, five tasks that fail at the same moment retry at the same moment, and
keep colliding on every subsequent attempt — a self-inflicted thundering herd
that synchronises rather than spreading out. Full jitter picks uniformly from
`[0, delay]`, which decorrelates them.
"""

from __future__ import annotations

import random

_DEFAULT_RNG = random.Random()

BASE_DELAY_SECONDS = 0.5
MAX_DELAY_SECONDS = 30.0


def backoff_delay(
    attempt: int,
    *,
    base: float = BASE_DELAY_SECONDS,
    cap: float = MAX_DELAY_SECONDS,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before `attempt` (0-indexed: 0 is the first retry).

    `jitter=False` exists for tests that need determinism, never for production
    use — the whole point of the jitter is that it is not predictable.
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")

    # Explicitly float: int.__pow__ widens to Any under strict typing, which
    # would silently make this function's return type unchecked.
    exponential: float = min(cap, base * float(2**attempt))
    if not jitter:
        return exponential
    return (rng or _DEFAULT_RNG).uniform(0.0, exponential)


def should_retry(attempt_count: int, max_attempts: int) -> bool:
    """Whether another attempt is permitted.

    `attempt_count` is attempts already made. With max_attempts=3 that allows
    one initial try plus two retries.
    """
    return attempt_count < max_attempts
