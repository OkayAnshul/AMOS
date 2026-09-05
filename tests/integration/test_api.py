"""API contract tests for V0.2. No network: the agent uses FakeProvider."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from amos.agents.tool_agent import ToolUsingAgent
from amos.api.app import create_app
from amos.errors import ProviderRateLimitError, ProviderTimeoutError
from amos.llm.fake import AlwaysFailsProvider, FakeProvider
from amos.tools.base import ToolCall
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import isolated_settings, valid_response_json


def build_client(provider: object, **kwargs: object) -> TestClient:
    settings = isolated_settings()
    agent = ToolUsingAgent(provider, ToolRegistry([CalculatorTool()]), **kwargs)  # type: ignore[arg-type]
    return TestClient(create_app(settings, agent=agent))


def calc_call(expression: str) -> ToolCall:
    return ToolCall(id="c1", name="calculator", arguments={"expression": expression})


def test_health_reports_version(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "0.5.0"


def test_goal_answered_without_tools(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "What is your name?"})

    assert response.status_code == 200
    body = response.json()
    assert body["response"]["answer"] == "42"
    assert body["tool_outcomes"] == []


def test_tool_execution_is_visible_in_the_response() -> None:
    """The caller can see which tools ran, with what result and at what cost."""
    provider = FakeProvider([[calc_call("2340 * 0.17")], valid_response_json(answer="397.8")])
    with build_client(provider) as client:
        response = client.post("/v1/goals", json={"goal": "What is 17% of 2340?"})

    body = response.json()
    assert response.status_code == 200
    assert len(body["tool_outcomes"]) == 1
    outcome = body["tool_outcomes"][0]
    assert outcome["name"] == "calculator"
    assert outcome["status"] == "ok"
    assert outcome["output"]["result"] == pytest.approx(397.8)


def test_tool_failure_is_reported_not_hidden() -> None:
    provider = FakeProvider(
        [
            [ToolCall(id="x", name="no_such_tool", arguments={})],
            valid_response_json(),
        ]
    )
    with build_client(provider) as client:
        response = client.post("/v1/goals", json={"goal": "x"})

    assert response.status_code == 200
    assert response.json()["tool_outcomes"][0]["status"] == "not_found"


def test_runaway_tool_loop_returns_502() -> None:
    provider = FakeProvider([[calc_call("1+1")]])  # never stops
    with build_client(provider, max_iterations=3) as client:
        response = client.post("/v1/goals", json={"goal": "x"})

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["type"] == "ToolLoopExhaustedError"
    assert body["error"]["details"]["iterations"] == 3


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ProviderTimeoutError("timed out"), 504),
        (ProviderRateLimitError("slow down"), 429),
    ],
)
def test_provider_errors_map_to_correct_status(error: Exception, expected_status: int) -> None:
    with build_client(AlwaysFailsProvider(error)) as client:
        response = client.post("/v1/goals", json={"goal": "x"})

    assert response.status_code == expected_status
    assert response.json()["error"]["type"] == type(error).__name__


@pytest.mark.parametrize("payload", [{"goal": ""}, {}, {"goal": "x" * 8001}])
def test_invalid_requests_return_422(payload: dict[str, str], valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json=payload)
    assert response.status_code == 422


def test_request_id_is_returned(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"})
    assert response.headers["x-request-id"]


def test_client_supplied_request_id_is_honoured(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"}, headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


def test_openapi_schema_is_generated(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/v1/goals" in response.json()["paths"]
