"""Record every real provider call as it returns (ADR-015).

Sits **inside** `RetryingProvider`, so each actual attempt — a 503 that was
retried, a 429 with its scope, a timeout — is its own row. Recorded outside the
retry, a retried call would look like one clean call and the retry would be
invisible on the page that exists to show it.

Also where call metrics are counted (moved from `RunService` in V1.7), so they
are counted on every path: the worker, the eval, and runs without a database.
"""

from __future__ import annotations

import time

from amos.budget import charge_llm_call
from amos.database.recorder import current_recorder
from amos.errors import ProviderError, ProviderRateLimitError
from amos.llm.base import LLMProvider, LLMRequest, LLMResponse
from amos.observability import get_call_context
from amos.telemetry.metrics import instruments, safe_labels


class RecordingProvider:
    """Wraps a provider; records each call to the current run, if any."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    @property
    def model(self) -> str:
        return str(getattr(self._inner, "_model", None) or getattr(self._inner, "model", "unknown"))

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        # Every real attempt counts against the run's budget (ADR-019), retries
        # included: each one spends quota.
        await charge_llm_call(get_call_context().purpose)
        started = time.perf_counter()
        try:
            response = await self._inner.complete(request, timeout=timeout)
        except ProviderError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            instruments().llm_calls.add(
                1, safe_labels(provider=self.name, model=self.model, status="error")
            )
            recorder = current_recorder()
            if recorder is not None:
                await recorder.llm_call(
                    provider=self.name,
                    model=self.model,
                    latency_ms=latency_ms,
                    error=describe(exc),
                )
            raise

        labels = safe_labels(provider=response.provider, model=response.model)
        instruments().llm_calls.add(1, labels)
        instruments().llm_tokens.add(response.prompt_tokens + response.output_tokens, labels)
        recorder = current_recorder()
        if recorder is not None:
            await recorder.llm_call(
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
            )
        return response


def describe(exc: ProviderError) -> str:
    """`Type (qualifier): message` — the type first, so the page and the quota
    meter can classify a stored error without parsing prose."""
    qualifier = None
    if isinstance(exc, ProviderRateLimitError):
        qualifier = exc.details.get("quota_scope") or "unknown"
    elif exc.details.get("status") is not None:
        qualifier = exc.details.get("status")
    head = type(exc).__name__ + (f" ({qualifier})" if qualifier is not None else "")
    return f"{head}: {exc.message[:500]}"
