"""V0.1's no-tools agent, still reachable and still tested.

Kept because it remains the right choice for goals needing no tools, and because
the repair loop is only exercised end-to-end here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from amos.agents.agent import GroundedAgent
from amos.api.app import create_app
from amos.llm.fake import FakeProvider
from tests.conftest import isolated_settings


def build_client(provider: object, **kwargs: object) -> TestClient:
    settings = isolated_settings()
    agent = GroundedAgent(provider, **kwargs)  # type: ignore[arg-type]
    app = create_app(settings, agent=agent)  # type: ignore[arg-type]
    return TestClient(app)


def test_grounded_agent_still_serves_goals(valid_json: str) -> None:
    with build_client(FakeProvider([valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"})
    assert response.status_code == 200
    assert response.json()["response"]["answer"] == "42"


def test_repair_is_visible_through_the_api(valid_json: str) -> None:
    with build_client(FakeProvider(["garbage", valid_json])) as client:
        response = client.post("/v1/goals", json={"goal": "x"})
    assert response.status_code == 200
    assert response.json()["repair_count"] == 1


def test_unrepairable_output_returns_502() -> None:
    with build_client(FakeProvider(["garbage"]), max_repair_attempts=1) as client:
        response = client.post("/v1/goals", json={"goal": "x"})
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "OutputValidationError"
