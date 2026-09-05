"""Backoff policy."""

from __future__ import annotations

import random

import pytest

from amos.orchestration.retry import backoff_delay, should_retry


def test_delay_grows_exponentially_without_jitter() -> None:
    delays = [backoff_delay(i, jitter=False) for i in range(4)]
    assert delays == [0.5, 1.0, 2.0, 4.0]


def test_delay_is_capped() -> None:
    """Exponential growth without a cap eventually means waiting for hours."""
    assert backoff_delay(50, jitter=False) == 30.0


def test_jitter_stays_within_the_exponential_bound() -> None:
    rng = random.Random(1)
    for attempt in range(6):
        ceiling = backoff_delay(attempt, jitter=False)
        assert 0.0 <= backoff_delay(attempt, rng=rng) <= ceiling


def test_jitter_actually_varies() -> None:
    """Without this, 'jitter' could be implemented as a constant and still pass
    the bounds test — while doing nothing to decorrelate retries."""
    rng = random.Random(7)
    samples = {backoff_delay(3, rng=rng) for _ in range(20)}
    assert len(samples) > 15


def test_negative_attempt_is_rejected() -> None:
    with pytest.raises(ValueError):
        backoff_delay(-1)


@pytest.mark.parametrize(
    ("attempts_made", "max_attempts", "expected"),
    [
        (0, 3, True),
        (1, 3, True),
        (2, 3, True),
        (3, 3, False),
        (4, 3, False),
        (0, 1, True),
        (1, 1, False),
    ],
)
def test_retry_budget(attempts_made: int, max_attempts: int, expected: bool) -> None:
    assert should_retry(attempts_made, max_attempts) is expected
