"""The demo page in a real browser (V1.8, ADR-017). Opt-in: `make browser-test`.

A real server runs in a thread with a scripted provider and the test database;
Playwright drives the installed Chrome — no browser download. What this proves
that the HTTP tests cannot: the page renders a run from the read API, the lanes
draw, the inspector opens, and **a prompt injection in a model's answer is shown
as text, not executed**.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

CHROME = os.getenv("AMOS_CHROME", "/usr/sbin/google-chrome-stable")

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("AMOS_RUN_BROWSER_TESTS") != "1",
        reason="browser tests are opt-in: make browser-test",
    ),
    pytest.mark.skipif(not Path(CHROME).exists(), reason=f"no Chrome at {CHROME}"),
]

INJECTION = "<img src=x onerror=\"document.title='pwned'\">"


def _plan() -> str:
    return json.dumps(
        {
            "reasoning": "one step",
            "tasks": [{"id": "t1", "description": "add it up", "depends_on": []}],
        }
    )


def _script() -> list[str]:
    from tests.conftest import valid_response_json

    return [
        _plan(),
        json.dumps({"agent": "analyst", "reason": "it is arithmetic"}),
        valid_response_json(answer=f"The total is **42**. {INJECTION}"),
        json.dumps({"verdict": "accept", "reasoning": "supported"}),
    ]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _make_user(url: str) -> tuple[uuid.UUID, str]:
    from amos.auth import create_user
    from amos.database.engine import session_scope
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url)
    try:
        async with session_scope(async_sessionmaker(engine)) as session:
            actor, key = await create_user(session, f"browser-{uuid.uuid4().hex[:8]}")
        return actor.id, key
    finally:
        await engine.dispose()


async def _drop_user(url: str, user_id: uuid.UUID) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM runs WHERE user_id = :u"), {"u": str(user_id)})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(user_id)})
    finally:
        await engine.dispose()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    import uvicorn

    import amos.api.app as app_module
    from amos.llm.fake import FakeProvider
    from amos.llm.recording import RecordingProvider
    from amos.llm.retry import RetryingProvider
    from tests.conftest import isolated_settings
    from tests.integration.conftest import TEST_DATABASE_URL

    async def no_sleep(_: float) -> None:
        return None

    # The app builds its own provider at startup; this one is scripted.
    monkeypatch.setattr(
        app_module,
        "build_provider",
        lambda _settings: RetryingProvider(
            RecordingProvider(FakeProvider(_script())), sleep=no_sleep
        ),
    )
    settings = isolated_settings(
        database_url=TEST_DATABASE_URL, memory_enabled=False, memory_reconcile_enabled=False
    )
    user_id, key = asyncio.run(_make_user(TEST_DATABASE_URL))
    port = _free_port()
    srv = uvicorn.Server(
        uvicorn.Config(
            app_module.create_app(settings), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert srv.started, "the server did not start"
    try:
        yield f"http://127.0.0.1:{port}", key
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        asyncio.run(_drop_user(TEST_DATABASE_URL, user_id))


def test_a_run_from_the_page_draws_and_an_injection_stays_text(server: tuple[str, str]) -> None:
    from playwright.sync_api import sync_playwright

    url, key = server
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))

        # Connect with the key, as a presenter would.
        page.goto(url)
        page.fill("input[type=password]", key)
        page.click("button:has-text('Connect')")
        page.wait_for_selector(".composer")

        page.click("text=Percent, then subtract")
        page.click("button.primary:has-text('Run')")
        page.wait_for_selector(".run-head .status.ok", timeout=30_000)

        # The run drew: the planner on the orchestrator lane, the task on the
        # analyst's, the review on the critic's — from stored rows.
        assert page.locator(".seg.planner").count() == 1
        assert page.locator(".seg.task").count() == 1
        assert page.locator(".seg.critic").count() == 1
        assert "Analyst" in (page.locator("svg.lanes").text_content() or "")
        assert page.locator(".feed-row").count() >= 8

        # The injection is visible as text and created no element.
        body = page.locator(".answer-body")
        assert INJECTION in body.inner_text()
        assert body.locator("img").count() == 0
        assert body.locator("strong").inner_text() == "42", "markdown still renders"
        assert page.title() != "pwned"

        # The inspector opens on a capsule.
        page.click(".seg.task")
        assert page.locator("#inspector-title").inner_text() == "Task t1"

        browser.close()
    assert not errors, errors
