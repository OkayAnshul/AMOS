"""API contract tests. No network: the agent is injected with a FakeProvider."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from amos.agents.agent import GroundedAgent
from amos.api.app import create_app
from amos.config import Settings
from amos.errors import ProviderRateLimitError, ProviderTimeoutError
from amos.llm.fake import AlwaysFailsProvider, FakeProvider


def build_client(provider: object, **agent_kwargs: object) -> TestClient:
    settings = Settings(gemini_api_key="test-key", env="test", log_level="WARNING")
    agent = GroundedAgent(provider, **agent_kwargs)  # type: ignore[arg-type]
    return TestClient(create_app(settings, agent=agent))


def test_health_endpoint(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_submit_goal_returns_structured_result(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "What is the answer?"})

    assert response.status_code == 200
    body = response.json()
    assert body["response"]["answer"] == "42"
    assert body["response"]["confidence"] == "high"
    assert body["repair_count"] == 0
    assert body["request_id"]
    assert len(body["llm_calls"]) == 1


def test_repair_is_visible_in_the_response(valid_json: str) -> None:
    """The client can see that a repair happened — it is not hidden."""
    with build_client(FakeProvider(["garbage", valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"})

    assert response.status_code == 200
    assert response.json()["repair_count"] == 1


def test_empty_goal_returns_422(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": ""})
    assert response.status_code == 422


def test_missing_goal_field_returns_422(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={})
    assert response.status_code == 422


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


def test_unrepairable_output_returns_502() -> None:
    with build_client(FakeProvider(["garbage"]), max_repair_attempts=1) as client:
        response = client.post("/v1/goals", json={"goal": "x"})

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "OutputValidationError"


def test_request_id_is_returned_in_header(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"})
    assert response.headers["x-request-id"]


def test_client_supplied_request_id_is_honoured(valid_json: str) -> None:
    """Lets a caller correlate their logs with ours — and becomes the V0.9 trace id."""
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"}, headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


def test_openapi_schema_is_generated(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/v1/goals" in response.json()["paths"]
