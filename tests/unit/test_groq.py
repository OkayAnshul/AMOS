"""The Groq provider (ADR-021), on recorded response shapes — no network."""

from __future__ import annotations

import json

import httpx
import pytest

from amos.agents.schemas import AgentResponse
from amos.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from amos.llm.base import LLMRequest, Turn
from amos.llm.groq import GroqProvider
from amos.tools.base import ToolCall, ToolOutcome, ToolStatus
from amos.tools.builtin import CalculatorTool

ANSWER = {
    "answer": "Paris",
    "reasoning": "known",
    "assumptions": [],
    "confidence": "high",
    "caveats": [],
}


def completion(content: str = "", tool_calls: list | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "model": "openai/gpt-oss-120b",
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 337, "completion_tokens": 82},
    }


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def provider(recorder: Recorder) -> GroqProvider:
    return GroqProvider("test-key", "openai/gpt-oss-120b", transport=httpx.MockTransport(recorder))


async def test_a_structured_answer_without_tools_uses_json_schema() -> None:
    rec = Recorder(httpx.Response(200, json=completion(json.dumps(ANSWER))))
    response = await provider(rec).complete(
        LLMRequest(prompt="capital of France?", response_schema=AgentResponse), timeout=10
    )
    assert isinstance(response.parsed, AgentResponse) and response.parsed.answer == "Paris"
    assert rec.bodies[0]["response_format"]["type"] == "json_schema"
    assert (response.provider, response.prompt_tokens, response.output_tokens) == ("groq", 337, 82)


async def test_with_tools_the_schema_goes_in_the_instruction_not_response_format() -> None:
    """Groq's docs: structured output and tool use cannot be combined."""
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "calculator", "arguments": '{"expression": "17*23"}'},
    }
    rec = Recorder(httpx.Response(200, json=completion(tool_calls=[call])))
    response = await provider(rec).complete(
        LLMRequest(prompt="17*23", tools=[CalculatorTool.spec()], response_schema=AgentResponse),
        timeout=10,
    )
    body = rec.bodies[0]
    assert "response_format" not in body
    assert body["parallel_tool_calls"] is False, "gpt-oss does not support parallel tool calls"
    assert "JSON Schema" in body["messages"][0]["content"]
    assert response.tool_calls == [
        ToolCall(id="c1", name="calculator", arguments={"expression": "17*23"})
    ]


async def test_history_from_any_model_becomes_openai_messages() -> None:
    rec = Recorder(httpx.Response(200, json=completion(json.dumps(ANSWER))))
    history = [
        Turn(role="user", text="17*23"),
        Turn(
            role="model",
            tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "17*23"})],
            model="gemini-3.5-flash-lite",
        ),
        Turn(
            role="tool",
            tool_outcomes=[
                ToolOutcome(
                    call_id="c1",
                    name="calculator",
                    arguments={},
                    status=ToolStatus.OK,
                    output={"result": 391},
                )
            ],
        ),
    ]
    await provider(rec).complete(
        LLMRequest(history=history, response_schema=AgentResponse), timeout=10
    )
    roles = [m["role"] for m in rec.bodies[0]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert rec.bodies[0]["messages"][1]["tool_calls"][0]["function"]["name"] == "calculator"
    assert rec.bodies[0]["messages"][2]["tool_call_id"] == "c1"


async def test_a_json_answer_misdelivered_as_a_tool_call_is_asked_for_again_without_tools() -> None:
    """Seen live: gpt-oss "called" a tool named json for its final answer and
    Groq refused it with a 400. One retry with no tools offered recovers."""
    bad = httpx.Response(
        400,
        json={
            "error": {
                "message": "Tool call validation failed: attempted to call tool 'json' "
                "which was not in request.tools"
            }
        },
    )
    good = httpx.Response(200, json=completion(json.dumps(ANSWER)))
    rec = Recorder(bad, good)
    response = await provider(rec).complete(
        LLMRequest(prompt="x", tools=[CalculatorTool.spec()], response_schema=AgentResponse),
        timeout=10,
    )
    assert response.parsed is not None
    assert "tools" in rec.bodies[0] and "tools" not in rec.bodies[1]
    assert rec.bodies[1]["response_format"]["type"] == "json_schema"


def test_a_fenced_or_wrapped_json_answer_still_parses() -> None:
    from amos.llm.groq import _parse

    fenced = "Here you go:\n```json\n" + json.dumps(ANSWER) + "\n```"
    assert _parse(fenced, AgentResponse) is not None
    assert _parse("no json here", AgentResponse) is None


@pytest.mark.parametrize(
    ("status", "message", "error", "scope"),
    [
        (429, "Rate limit reached on tokens per minute (TPM)", ProviderRateLimitError, "minute"),
        (429, "Rate limit reached on requests per day (RPD)", ProviderRateLimitError, "day"),
        (401, "Invalid API Key", ProviderAuthError, None),
        (503, "Service Unavailable", ProviderUnavailableError, None),
        (498, "Flex tier capacity exceeded", ProviderUnavailableError, None),
    ],
)
async def test_errors_become_amos_errors(
    status: int, message: str, error: type, scope: str | None
) -> None:
    rec = Recorder(httpx.Response(status, json={"error": {"message": message}}))
    with pytest.raises(error) as caught:
        await provider(rec).complete(LLMRequest(prompt="x"), timeout=10)
    if scope:
        assert caught.value.details["quota_scope"] == scope


async def test_a_slow_answer_is_a_timeout() -> None:
    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderTimeoutError):
        await GroqProvider("k", "m", transport=httpx.MockTransport(slow)).complete(
            LLMRequest(prompt="x"), timeout=5
        )


# ---------- fitting Groq (ADR-023) ----------


def test_the_longest_tool_results_are_cut_until_the_request_fits() -> None:
    from amos.llm.groq import MIN_TOOL_CHARS, fit

    messages = [
        {"role": "system", "content": "s" * 1000},
        {"role": "user", "content": "goal"},
        {"role": "tool", "tool_call_id": "a", "content": "p" * 15000},
        {"role": "tool", "tool_call_id": "b", "content": "q" * 9000},
        {"role": "tool", "tool_call_id": "c", "content": "newest " * 400},
    ]
    fitted = fit([dict(m) for m in messages], limit=12000)
    assert sum(len(m["content"]) for m in fitted) <= 12000 + 200
    assert fitted[-1]["content"] == messages[-1]["content"], "the newest result is never cut"
    assert fitted[0]["content"] == messages[0]["content"]
    assert all(len(m["content"]) >= MIN_TOOL_CHARS for m in fitted if m["role"] == "tool")
    assert "cut to fit" in fitted[2]["content"]


async def test_a_short_per_minute_limit_is_waited_out_once() -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    limited = httpx.Response(
        429,
        json={"error": {"message": "Rate limit reached on tokens per minute (TPM)"}},
        headers={"retry-after": "4"},
    )
    rec = Recorder(limited, httpx.Response(200, json=completion(json.dumps(ANSWER))))
    p = GroqProvider("k", "openai/gpt-oss-120b", transport=httpx.MockTransport(rec), sleep=sleep)
    response = await p.complete(LLMRequest(prompt="x", response_schema=AgentResponse), timeout=30)
    assert response.parsed is not None and waits == [4.0]


async def test_a_wait_longer_than_the_deadline_allows_is_not_taken() -> None:
    limited = httpx.Response(
        429,
        json={"error": {"message": "Rate limit reached on tokens per minute (TPM)"}},
        headers={"retry-after": "40"},
    )
    rec = Recorder(limited)
    with pytest.raises(ProviderRateLimitError):
        await provider(rec).complete(LLMRequest(prompt="x"), timeout=30)


def test_fitting_ends_even_when_nothing_more_can_be_cut() -> None:
    """Regression: a cut result gained its marker back on every pass, so a request
    that could not shrink further looped forever."""
    from amos.llm.groq import fit

    messages = [
        {"role": "system", "content": "s" * 9000},
        {"role": "tool", "tool_call_id": "a", "content": "p" * 1600},
        {"role": "tool", "tool_call_id": "b", "content": "q" * 4425},
        {"role": "user", "content": "now"},
    ]
    fitted = fit([dict(m) for m in messages], limit=12000)
    assert len(fitted) == 4
