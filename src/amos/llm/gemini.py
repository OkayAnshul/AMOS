"""Gemini provider (google-genai).

Vendor exceptions are translated into AMOS's typed errors here, at the boundary.
Nothing above this file imports a vendor SDK — that is what makes the provider
swappable and what keeps `except` blocks elsewhere from naming Google types.
"""

from __future__ import annotations

import asyncio
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from amos.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from amos.llm.base import LLMRequest, LLMResponse


class GeminiProvider:
    """Calls the Gemini API. Async, timeout-bounded, typed errors."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        config = types.GenerateContentConfig(
            temperature=request.temperature,
            system_instruction=request.system_instruction,
        )
        if request.response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_schema = request.response_schema

        started = time.perf_counter()
        try:
            # Every external call is bounded (requirement N-4). The SDK's own
            # timeout handling is not relied upon.
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=request.prompt,
                    config=config,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"Gemini did not respond within {timeout}s",
                details={"model": self._model, "timeout_seconds": timeout},
            ) from exc
        except genai_errors.ClientError as exc:
            raise self._translate_client_error(exc) from exc
        except genai_errors.ServerError as exc:
            raise ProviderError(
                f"Gemini server error: {exc}", details={"model": self._model}
            ) from exc
        except genai_errors.APIError as exc:
            raise ProviderError(f"Gemini API error: {exc}", details={"model": self._model}) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = response.usage_metadata
        finish_reason = None
        if response.candidates:
            finish_reason = str(response.candidates[0].finish_reason)

        return LLMResponse(
            text=response.text or "",
            parsed=response.parsed if isinstance(response.parsed, BaseModel) else None,
            model=self._model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _translate_client_error(exc: genai_errors.ClientError) -> ProviderError:
        """Map HTTP status to a typed error the caller can act on differently."""
        status = getattr(exc, "code", None)
        message = str(exc)
        if status == 429:
            return ProviderRateLimitError(
                "Gemini rate limit exceeded (free tier is ~15 requests/minute)",
                details={"status": status},
            )
        if status in (401, 403):
            return ProviderAuthError("Gemini rejected the API key", details={"status": status})
        return ProviderError(f"Gemini client error: {message}", details={"status": status})
