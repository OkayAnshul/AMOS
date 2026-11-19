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
from amos.llm.base import LLMRequest, LLMResponse, Turn
from amos.llm.quota import quota_scope
from amos.telemetry.tracing import span
from amos.tools.base import ToolCall


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
        if request.tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=spec.name,
                            description=spec.description,
                            # The Pydantic-generated JSON Schema goes straight to
                            # the model, so validator and declaration cannot drift.
                            parameters_json_schema=spec.parameters,
                        )
                        for spec in request.tools
                    ]
                )
            ]
            # AMOS validates arguments and enforces timeouts itself; the SDK
            # executing functions on our behalf would bypass both.
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)

        started = time.perf_counter()
        with span(
            "llm.complete",
            **{"gen_ai.system": self.name, "gen_ai.request.model": self._model},
        ):
            return await self._complete_inner(request, config, timeout, started)

    async def _complete_inner(
        self,
        request: LLMRequest,
        config: types.GenerateContentConfig,
        timeout: float,
        started: float,
    ) -> LLMResponse:
        try:
            # Every external call is bounded (requirement N-4). The SDK's own
            # timeout handling is not relied upon.
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=_to_contents(request.turns()),
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

        tool_calls = [
            ToolCall(
                id=call.id or f"call_{index}",
                name=call.name or "",
                arguments=dict(call.args or {}),
            )
            for index, call in enumerate(response.function_calls or [])
        ]

        raw_content = response.candidates[0].content if response.candidates else None

        return LLMResponse(
            text=_safe_text(response),
            tool_calls=tool_calls,
            provider_state=raw_content,
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
            # Two shapes: gemini-3.5-flash binds DAILY (20/day), flash-lite
            # PER MINUTE (15/min). `quota_scope` says which, from the violated
            # quota's id, so a caller can say "resets at midnight Pacific"
            # rather than "try again". The provider's own text is kept because
            # it names the exact quota.
            scope = quota_scope(exc.details)
            return ProviderRateLimitError(
                f"Gemini quota exceeded ({scope or 'unknown'} limit). {message}",
                details={"status": status, "quota_scope": scope},
            )
        if status in (401, 403):
            return ProviderAuthError("Gemini rejected the API key", details={"status": status})
        return ProviderError(f"Gemini client error: {message}", details={"status": status})


def _safe_text(response: types.GenerateContentResponse) -> str:
    """`.text` raises when the response holds only function calls."""
    try:
        return response.text or ""
    except ValueError, AttributeError:
        return ""


def _to_contents(turns: list[Turn]) -> list[types.Content]:
    """Translate AMOS turns into Gemini contents.

    Note the role on a tool-result turn is "user", not "tool" — Gemini expects
    function responses to come from the user role. Confirmed against the API,
    not assumed.
    """
    contents: list[types.Content] = []
    for turn in turns:
        if turn.role == "tool":
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=outcome.name, response=outcome.to_model_payload()
                        )
                        for outcome in turn.tool_outcomes
                    ],
                )
            )
            continue

        # Replay the model's own content verbatim when we have it. Gemini 3.x
        # rejects reconstructed function-call parts because they lack the
        # thought_signature, which is not exposed as a reproducible value.
        if turn.role == "model" and isinstance(turn.provider_state, types.Content):
            contents.append(turn.provider_state)
            continue

        parts: list[types.Part] = []
        if turn.text:
            parts.append(types.Part(text=turn.text))
        for call in turn.tool_calls:
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=call.id, name=call.name, args=call.arguments
                    )
                )
            )
        if parts:
            contents.append(
                types.Content(role="model" if turn.role == "model" else "user", parts=parts)
            )
    return contents
