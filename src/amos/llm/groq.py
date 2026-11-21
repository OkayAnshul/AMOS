"""Groq provider, over its OpenAI-compatible chat API with `httpx` — no SDK (ADR-021).

Translated at the boundary, like `GeminiProvider`: AMOS turns in, `LLMResponse`
out, and vendor failures become AMOS's typed errors so nothing above this file
knows which provider answered.

Two facts from Groq's documentation shape it (2026-11-22):
- **Structured output and tools cannot be combined in one request.** With tools,
  the schema is stated in the system instruction and the final answer parsed from
  the text; without tools, `response_format: json_schema` is used.
- `openai/gpt-oss-120b` supports tool calls but **not parallel** tool calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from amos.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from amos.llm.base import LLMRequest, LLMResponse, Turn
from amos.telemetry.tracing import span
from amos.tools.base import ToolCall

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
#: A request's characters, at most. Groq's free tier refuses a request over its
#: 8,000 tokens a minute outright (413); ~18,000 characters leaves room for the
#: answer (ADR-023).
MAX_REQUEST_CHARS = 18_000
#: A tool result is never cut below this.
MIN_TOOL_CHARS = 1_500
#: Models with strict structured output (Groq's docs, 2026-11-22).
STRICT_MODELS = frozenset({"openai/gpt-oss-120b", "openai/gpt-oss-20b"})
#: Keywords strict mode is not documented to accept; pydantic still enforces them after parsing.
_UNSTRICT = frozenset(
    {"default", "minLength", "maxLength", "minItems", "maxItems", "pattern", "format"}
    | {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "title"}
)


def strict_schema(schema: Any) -> Any:
    """A JSON schema Groq's strict mode accepts: every object's properties all
    required, no additional properties, and no keywords strict mode rejects."""
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {
        key: strict_schema(value)
        for key, value in schema.items()
        if key not in _UNSTRICT or key == "properties"
    }
    if isinstance(out.get("properties"), dict):
        out["properties"] = {
            name: strict_schema(value) for name, value in schema["properties"].items()
        }
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


#: Groq's own custom status for "flex tier at capacity"; transient like a 503.
_UNAVAILABLE = {498, 500, 502, 503, 504}


class GroqProvider:
    """Calls Groq. Async, timeout-bounded, typed errors."""

    name = "groq"
    #: Groq accepts any history: a tool call another model made needs no signature.
    requires_own_history = False

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        started = time.perf_counter()
        try:
            return await self._complete_once(request, timeout, started)
        except ProviderRateLimitError as exc:
            # A per-minute limit with a short retry-after is cheaper to wait out than
            # to hand to a weaker model — inside the same deadline, once (ADR-023).
            wait = exc.details.get("retry_after")
            remaining = timeout - (time.perf_counter() - started)
            if exc.details.get("quota_scope") != "minute" or not isinstance(wait, float):
                raise
            if wait + 3 > remaining:
                raise
            await self._sleep(wait)
            return await self._complete_once(request, remaining - wait, started)

    async def _complete_once(
        self, request: LLMRequest, timeout: float, started: float
    ) -> LLMResponse:
        try:
            return await self._complete(request, timeout, started)
        except ProviderError as exc:
            # gpt-oss sometimes delivers its final JSON answer as a call to a
            # tool named "json", which Groq refuses (400, "tool call validation
            # failed") — seen live on 2026-11-22. The tool results are already in
            # the history, so ask once more with no tools offered, in structured
            # output mode. Bounded: one extra request, inside the same deadline.
            if not (request.tools and _bad_tool_call(exc)):
                raise
            remaining = timeout - (time.perf_counter() - started)
            if remaining < 3:
                raise
            return await self._complete(
                request.model_copy(update={"tools": []}), remaining, started
            )

    async def _complete(self, request: LLMRequest, timeout: float, started: float) -> LLMResponse:
        body = self._body(request)
        with span(
            "llm.complete", **{"gen_ai.system": self.name, "gen_ai.request.model": self.model}
        ):
            try:
                async with httpx.AsyncClient(transport=self._transport) as client:
                    response = await asyncio.wait_for(
                        client.post(
                            f"{self._base_url}/chat/completions",
                            json=body,
                            headers={"authorization": f"Bearer {self._api_key}"},
                            timeout=timeout,
                        ),
                        timeout=timeout,
                    )
            except (TimeoutError, httpx.TimeoutException) as exc:
                raise ProviderTimeoutError(
                    f"Groq did not respond within {timeout:.0f}s",
                    details={"model": self.model, "timeout_seconds": timeout},
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    f"Groq could not be reached: {type(exc).__name__}",
                    details={"model": self.model},
                ) from exc

        if response.status_code >= 400:
            raise self._error(response)

        data = response.json()
        message = (data.get("choices") or [{}])[0].get("message") or {}
        usage = data.get("usage") or {}
        tool_calls = [
            ToolCall(
                id=str(call.get("id") or f"call_{index}"),
                name=str((call.get("function") or {}).get("name") or ""),
                arguments=_arguments((call.get("function") or {}).get("arguments")),
            )
            for index, call in enumerate(message.get("tool_calls") or [])
        ]
        text = message.get("content") or ""
        parsed = None if tool_calls else _parse(text, request.response_schema)
        return LLMResponse(
            text=text,
            parsed=parsed,
            tool_calls=tool_calls,
            provider_state=message,
            model=str(data.get("model") or self.model),
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=(data.get("choices") or [{}])[0].get("finish_reason"),
        )

    def _body(self, request: LLMRequest) -> dict[str, Any]:
        system = request.system_instruction or ""
        schema = request.response_schema
        body: dict[str, Any] = {"model": self.model, "temperature": request.temperature}
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
                for spec in request.tools
            ]
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = False
            if schema is not None:
                # Tools and structured output cannot be combined on Groq: state
                # the schema, and parse the final answer from the text.
                system += _schema_instruction(schema)
        elif schema is not None:
            # Strict mode guarantees the schema by constrained decoding, on the models
            # that support it. Best-effort mode was seen live leaving out a required
            # field, which turned a writer's whole JSON into the answer text.
            strict = self.model in STRICT_MODELS
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": strict,
                    "schema": strict_schema(schema.model_json_schema())
                    if strict
                    else schema.model_json_schema(),
                },
            }
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        messages.extend(_messages(request.turns()))
        body["messages"] = fit(messages)
        return body

    def _error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        try:
            message = str(response.json().get("error", {}).get("message", ""))
        except ValueError:
            message = response.text[:300]
        if status == 429:
            scope = "day" if re.search(r"per day|\(RPD\)|\(TPD\)", message, re.I) else "minute"
            details: dict[str, Any] = {
                "status": status,
                "quota_scope": scope,
                "provider": self.name,
            }
            with contextlib.suppress(ValueError):
                details["retry_after"] = float(response.headers.get("retry-after", ""))
            return ProviderRateLimitError(
                f"Groq quota exceeded ({scope} limit). {message}", details=details
            )
        if status in (401, 403):
            return ProviderAuthError("Groq rejected the API key", details={"status": status})
        if status in _UNAVAILABLE:
            return ProviderUnavailableError(
                f"Groq server error {status}: {message}", details={"status": status}
            )
        return ProviderError(f"Groq error {status}: {message}", details={"status": status})


_CUT = " … [cut to fit the model's request limit]"


def fit(messages: list[dict[str, Any]], limit: int = MAX_REQUEST_CHARS) -> list[dict[str, Any]]:
    """Cut the longest tool results until the request fits Groq (ADR-023).

    Tool results are what grows — a page per read. The newest message is never
    cut: it is what the model is answering. Nothing is cut below MIN_TOOL_CHARS.
    Every cut makes the request strictly smaller, so this ends.
    """
    size = sum(len(str(m.get("content") or "")) for m in messages)
    while size > limit:
        candidates = [
            i
            for i, m in enumerate(messages[:-1])
            if m["role"] == "tool" and len(m["content"].removesuffix(_CUT)) > MIN_TOOL_CHARS
        ]
        if not candidates:
            break
        longest = max(candidates, key=lambda i: len(messages[i]["content"]))
        before = messages[longest]["content"]
        base = before.removesuffix(_CUT)
        keep = max(MIN_TOOL_CHARS, min(len(base) // 2, len(before) - (size - limit) - len(_CUT)))
        after = base[:keep] + _CUT
        if len(after) >= len(before):
            break
        messages[longest] = {**messages[longest], "content": after}
        size -= len(before) - len(after)
    return messages


def _bad_tool_call(exc: ProviderError) -> bool:
    return exc.details.get("status") == 400 and "tool call validation failed" in exc.message.lower()


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "\n\nWhen you give your final answer (not a tool call), reply with ONLY a JSON object "
        "that matches this JSON Schema, with no text before or after it:\n"
        + json.dumps(schema.model_json_schema())
    )


def _messages(turns: list[Turn]) -> list[dict[str, Any]]:
    """AMOS turns as OpenAI-style messages. A model turn from any provider is
    rebuilt from its text and tool calls — Groq needs no signature."""
    messages: list[dict[str, Any]] = []
    for turn in turns:
        if turn.role == "tool":
            messages.extend(
                {
                    "role": "tool",
                    "tool_call_id": outcome.call_id,
                    "content": json.dumps(outcome.to_model_payload(), default=str),
                }
                for outcome in turn.tool_outcomes
            )
        elif turn.role == "model":
            message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
            if turn.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in turn.tool_calls
                ]
            messages.append(message)
        else:
            messages.append({"role": "user", "content": turn.text or ""})
    return messages


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except TypeError, ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _parse(text: str, schema: type[BaseModel] | None) -> BaseModel | None:
    """The schema'd answer from the text, tolerating a ```json fence. None if it
    does not validate — the agent's own fallback handles that, as for Gemini."""
    if schema is None or not text.strip():
        return None
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1)
    elif not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = candidate[start : end + 1]
    try:
        return schema.model_validate_json(candidate)
    except ValidationError:
        return None
