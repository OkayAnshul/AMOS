"""The demo page is served by the API, under a policy that makes "nothing from
outside" enforced rather than intended (ADR-017)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from amos.agents.tool_agent import ToolUsingAgent
from amos.api.app import CONTENT_SECURITY_POLICY, WEB_DIR, create_app
from amos.llm.fake import FakeProvider
from amos.tools.builtin import CalculatorTool
from amos.tools.registry import ToolRegistry
from tests.conftest import isolated_settings, valid_response_json


def client() -> TestClient:
    agent = ToolUsingAgent(FakeProvider([valid_response_json()]), ToolRegistry([CalculatorTool()]))
    return TestClient(create_app(isolated_settings(), agent=agent))


def test_the_page_is_served_at_the_root_with_a_strict_policy() -> None:
    with client() as c:
        response = c.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    policy = response.headers["content-security-policy"]
    assert policy == CONTENT_SECURITY_POLICY
    assert "default-src 'self'" in policy and "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert '<script type="module" src="/static/js/main.js">' in response.text


def test_every_asset_the_page_names_is_served() -> None:
    with client() as c:
        page = c.get("/").text
        referenced = re.findall(r'(?:src|href)="(/static/[^"]+)"', page)
        assert referenced, "the page references no assets — the test is not looking"
        for path in referenced:
            assert c.get(path).status_code == 200, path
        for module in sorted((WEB_DIR / "static" / "js").glob("*.js")):
            response = c.get(f"/static/js/{module.name}")
            assert response.status_code == 200
            assert "javascript" in response.headers["content-type"]


def test_no_page_file_names_an_outside_url() -> None:
    """No CDN, no font service, no analytics: the CSP would block them anyway,
    and this catches the attempt before a browser does. XML namespaces are
    identifiers, never fetched, so the SVG one is allowed."""
    allowed = {"http://www.w3.org/2000/svg"}
    offenders = []
    for path in sorted(WEB_DIR.rglob("*")):
        if path.suffix not in {".html", ".css", ".js", ".svg"}:
            continue
        for url in re.findall(r"https?://[^\s\"'`)<>]+", path.read_text()):
            if url not in allowed:
                offenders.append(f"{path.relative_to(WEB_DIR)}: {url}")
    assert not offenders, offenders


def test_model_text_never_reaches_innerHTML() -> None:
    """Answers, documents and tool output are untrusted (N-12). The page builds
    DOM nodes and sets textContent; an innerHTML assignment anywhere is the
    one line that would turn a prompt injection into script."""
    for path in sorted((WEB_DIR / "static" / "js").glob("*.js")):
        source = path.read_text()
        unsafe = re.findall(
            r"\.(?:innerHTML|outerHTML)\b|insertAdjacentHTML|\beval\(|new Function", source
        )
        assert not unsafe, f"{path.name}: {unsafe}"


def test_the_typeface_ships_with_its_licence() -> None:
    fonts = WEB_DIR / "static" / "fonts"
    assert (fonts / "InterVariable.woff2").stat().st_size > 100_000
    assert "SIL Open Font License" in (fonts / "Inter-LICENSE.txt").read_text()


def test_the_page_is_packaged_with_the_service() -> None:
    """Hatchling packages everything under src/amos, so the page ships in the
    wheel; this pins that it lives there and not beside the repository."""
    assert Path(__file__).resolve().parents[2] / "src" / "amos" / "web" == WEB_DIR
