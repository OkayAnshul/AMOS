"""SSRF tests.

The URL is model-supplied. Every check must happen before a request is made, so
these tests assert on rejection without any network access.
"""

from __future__ import annotations

import pytest

from amos.tools.base import ToolCall, ToolStatus
from amos.tools.builtin import DEFAULT_ALLOWLIST, HttpGetTool


@pytest.fixture
def tool() -> HttpGetTool:
    return HttpGetTool()


def call(url: str) -> ToolCall:
    return ToolCall(id="h1", name="http_get", arguments={"url": url})


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.python.org/3/",          # not https
        "file:///etc/passwd",
        "ftp://docs.python.org/",
        "gopher://docs.python.org/",
    ],
)
async def test_non_https_schemes_are_rejected(tool: HttpGetTool, url: str) -> None:
    outcome = await tool.execute(call(url))
    assert outcome.status is ToolStatus.INVALID_ARGS


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/",
        "https://attacker.net/payload",
        "https://evil-github.com/",   # would pass a naive endswith("github.com")
        "https://github.com.evil.net/",
    ],
)
async def test_hosts_off_the_allowlist_are_rejected(tool: HttpGetTool, url: str) -> None:
    outcome = await tool.execute(call(url))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "allowlist" in (outcome.error or "")


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/admin",
        "https://127.0.0.1/",
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://192.168.1.1/",
        "https://10.0.0.1/",
    ],
)
async def test_internal_addresses_are_rejected(tool: HttpGetTool, url: str) -> None:
    """Blocked by the allowlist first; the IP check is defence in depth."""
    outcome = await tool.execute(call(url))
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_allowlisted_host_resolving_internally_is_rejected() -> None:
    """The case the allowlist alone would miss: a permitted *name* pointing at
    an internal address."""
    tool = HttpGetTool(allowlist={"localhost"})
    outcome = await tool.execute(call("https://localhost/"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "internal address" in (outcome.error or "")


def test_subdomains_of_allowlisted_hosts_are_permitted(tool: HttpGetTool) -> None:
    assert tool._is_allowlisted("docs.python.org")
    assert tool._is_allowlisted("gist.github.com")
    assert not tool._is_allowlisted("evil-github.com")
    assert not tool._is_allowlisted("github.com.attacker.net")


def test_default_allowlist_contains_only_documentation_hosts() -> None:
    assert "docs.python.org" in DEFAULT_ALLOWLIST
    assert all("." in host for host in DEFAULT_ALLOWLIST)


async def test_malformed_url_is_rejected(tool: HttpGetTool) -> None:
    outcome = await tool.execute(call("https://"))
    assert outcome.status is ToolStatus.INVALID_ARGS
