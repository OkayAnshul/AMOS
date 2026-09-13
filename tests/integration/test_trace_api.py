"""The trace endpoint through HTTP, against a real database."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.tool_agent import ToolUsingAgent
from amos.api.app import create_app
from amos.api.persistence import RunService
from amos.llm.fake import FakeProvider
from amos.tools.base import ToolCall
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import isolated_settings, valid_response_json


@pytest.fixture
def client_with_db(db_factory: async_sessionmaker[AsyncSession]) -> TestClient:
    provider = FakeProvider(
        [
            [ToolCall(id="c1", name="calculator", arguments={"expression": "2340*0.17"})],
            valid_response_json(answer="397.8"),
        ]
    )
    agent = ToolUsingAgent(provider, ToolRegistry([CalculatorTool()]))
    service = RunService(agent, db_factory)
    return TestClient(create_app(isolated_settings(), agent=agent, run_service=service))


def test_goal_returns_a_run_id(client_with_db: TestClient, auth: dict[str, str]) -> None:
    with client_with_db as client:
        response = client.post("/v1/goals", json={"goal": "What is 17% of 2340?"}, headers=auth)
    assert response.status_code == 200
    assert response.json()["run_id"]


def test_full_round_trip_submit_then_fetch_the_trace(
    client_with_db: TestClient, db_factory: async_sessionmaker[AsyncSession], auth: dict[str, str]
) -> None:
    """The V0.3 demo, as a test."""
    with client_with_db as client:
        submitted = client.post("/v1/goals", json={"goal": "What is 17% of 2340?"}, headers=auth)
        run_id = submitted.json()["run_id"]

        trace = client.get(f"/v1/runs/{run_id}", headers=auth)

    assert trace.status_code == 200
    body = trace.json()
    assert body["run_id"] == run_id
    assert body["status"] == "COMPLETED"
    assert body["goal"] == "What is 17% of 2340?"
    assert len(body["llm_calls"]) == 2
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["tool_name"] == "calculator"
    assert body["tool_calls"][0]["status"] == "ok"
    assert body["total_tokens"] > 0


def test_unknown_run_returns_404(client_with_db: TestClient, auth: dict[str, str]) -> None:
    with client_with_db as client:
        response = client.get(f"/v1/runs/{uuid.uuid4()}", headers=auth)
    assert response.status_code == 404


def test_malformed_run_id_returns_422(client_with_db: TestClient, auth: dict[str, str]) -> None:
    with client_with_db as client:
        response = client.get("/v1/runs/not-a-uuid", headers=auth)
    assert response.status_code == 422


def test_idempotency_header_deduplicates(client_with_db: TestClient, auth: dict[str, str]) -> None:
    key = f"demo-{uuid.uuid4()}"
    with client_with_db as client:
        first = client.post(
            "/v1/goals", json={"goal": "x"}, headers={"idempotency-key": key, **auth}
        )
        second = client.post(
            "/v1/goals", json={"goal": "x"}, headers={"idempotency-key": key, **auth}
        )
    assert first.json()["run_id"] == second.json()["run_id"]


def test_trace_endpoint_returns_503_without_persistence(
    valid_json: str, auth: dict[str, str]
) -> None:
    """Honest failure: the endpoint says persistence is off rather than 404ing."""
    agent = ToolUsingAgent(FakeProvider([valid_json]), ToolRegistry([CalculatorTool()]))
    app = create_app(isolated_settings(), agent=agent, run_service=RunService(agent, None))
    with TestClient(app) as client:
        response = client.get(f"/v1/runs/{uuid.uuid4()}", headers=auth)
    assert response.status_code == 503


# ---------- authentication at the HTTP boundary (V1.4) ----------


def test_an_unauthenticated_goal_is_rejected_before_any_model_call(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """401 before the agent runs.

    The ordering is the point: authentication that happens *after* the work has
    been done has already spent the tokens, and on a 20-request/day quota an
    unauthenticated caller could exhaust the budget without ever getting an
    answer.
    """
    provider = FakeProvider([valid_response_json()])
    agent = ToolUsingAgent(provider, ToolRegistry([CalculatorTool()]))
    service = RunService(agent, db_factory)

    with TestClient(create_app(isolated_settings(), agent=agent, run_service=service)) as client:
        response = client.post("/v1/goals", json={"goal": "anything"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert provider.call_count == 0, "the model was called for an unauthenticated request"


def test_a_wrong_key_is_rejected(client_with_db: TestClient) -> None:
    with client_with_db as client:
        response = client.post(
            "/v1/goals",
            json={"goal": "anything"},
            headers={"Authorization": "Bearer definitely-not-a-real-key"},
        )
    assert response.status_code == 401


def test_a_malformed_header_is_rejected_the_same_way(client_with_db: TestClient) -> None:
    """One failure path for missing, malformed and wrong keys — the distinction
    is not useful to a client, and enumerating it tells an attacker which half
    of their guess was right."""
    with client_with_db as client:
        missing = client.post("/v1/goals", json={"goal": "x"})
        malformed = client.post(
            "/v1/goals", json={"goal": "x"}, headers={"Authorization": "Basic abc"}
        )

    assert missing.status_code == malformed.status_code == 401
    assert missing.json() == malformed.json()


def test_one_user_cannot_fetch_anothers_trace_over_http(
    client_with_db: TestClient,
    db_factory: async_sessionmaker[AsyncSession],
    auth: dict[str, str],
) -> None:
    """End to end, through the API a client actually uses."""
    import asyncio

    from amos.auth import create_user
    from amos.database.engine import session_scope

    with client_with_db as client:
        submitted = client.post("/v1/goals", json={"goal": "What is 17% of 2340?"}, headers=auth)
        run_id = submitted.json()["run_id"]

        async def other_key() -> str:
            async with session_scope(db_factory) as session:
                _actor, key = await create_user(session, f"intruder-{uuid.uuid4().hex[:6]}")
                return key

        intruder = asyncio.run(other_key())
        response = client.get(f"/v1/runs/{run_id}", headers={"Authorization": f"Bearer {intruder}"})

    # 404, not 403: confirming a run exists but is not yours is itself a leak.
    assert response.status_code == 404
