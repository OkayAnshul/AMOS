"""In-memory provider for tests.

Every unit and integration test uses this. Tests never touch the network
(requirement N-14): the free tier is ~15 RPM, non-deterministic, and would make
CI both flaky and rate-limited.

This is also why `LLMProvider` is a Protocol — see llm/base.py.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from amos.errors import ProviderError
from amos.llm.base import LLMRequest, LLMResponse


class FakeProvider:
    """Replays a scripted sequence of responses.

    Each item in `responses` is either:
      - str        -> returned as response text (parsed if it fits the schema)
      - Exception  -> raised, to script failures
    """

    name = "fake"

    def __init__(
        self,
        responses: Sequence[str | Exception],
        *,
        model: str = "fake-model",
        latency_ms: int = 1,
    ) -> None:
        if not responses:
            raise ValueError("FakeProvider needs at least one scripted response")
        self._responses = list(responses)
        self._model = model
        self._latency_ms = latency_ms
        self.calls: list[LLMRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        self.calls.append(request)

        # Past the end of the script, repeat the last entry. Keeps tests that only
        # care about the first N calls from needing an exact-length script.
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        scripted = self._responses[index]

        if isinstance(scripted, Exception):
            raise scripted

        await asyncio.sleep(0)  # yield, so tests exercise the real async path

        parsed: BaseModel | None = None
        if request.response_schema is not None:
            try:
                parsed = request.response_schema.model_validate(json.loads(scripted))
            except json.JSONDecodeError, ValidationError:
                parsed = None  # deliberately: this is what drives the repair loop

        return LLMResponse(
            text=scripted,
            parsed=parsed,
            model=self._model,
            provider=self.name,
            prompt_tokens=len(request.prompt.split()),
            output_tokens=len(scripted.split()),
            latency_ms=self._latency_ms,
            finish_reason="STOP",
        )


class AlwaysFailsProvider:
    """Raises a given error on every call, for testing failure paths."""

    name = "always-fails"

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or ProviderError("scripted failure")
        self.calls: list[LLMRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        self.calls.append(request)
        raise self._error
