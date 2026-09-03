"""Allowlisted HTTP GET.

The URL comes from the model, so this tool is an SSRF vector by construction. An
allowlist is used rather than a blocklist: a blocklist has to anticipate every
dangerous target, while an allowlist only has to name the safe ones. Getting a
blocklist wrong fails open; getting an allowlist wrong fails closed.

Four checks, all before any request is made:
  - scheme must be https
  - host must be on the allowlist (exact match or a registered subdomain)
  - the resolved IP must not be private, loopback or link-local (blocks
    `localhost`, `169.254.169.254` cloud metadata, and DNS entries pointing at
    internal addresses)
  - redirects are not followed, since a redirect is a second URL that never
    passed the checks above
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from amos.errors import ToolValidationError
from amos.tools.base import Permission, Tool

DEFAULT_ALLOWLIST = frozenset(
    {
        "docs.python.org",
        "fastapi.tiangolo.com",
        "docs.pydantic.dev",
        "ai.google.dev",
        "www.postgresql.org",
        "github.com",
        "raw.githubusercontent.com",
    }
)

_MAX_BYTES = 200_000


class HttpGetArgs(BaseModel):
    url: str = Field(max_length=2000, description="Full https:// URL of a documentation page")


class HttpGetTool(Tool):
    name: ClassVar[str] = "http_get"
    description: ClassVar[str] = (
        "Fetch a page over HTTPS from an allowlisted documentation site. "
        "Returns the raw response body, truncated if large."
    )
    input_schema: ClassVar[type[BaseModel]] = HttpGetArgs
    permission: ClassVar[Permission] = Permission.NETWORK_READ
    timeout_seconds: ClassVar[float] = 15.0

    def __init__(self, allowlist: frozenset[str] | set[str] | None = None) -> None:
        self._allowlist = frozenset(allowlist) if allowlist is not None else DEFAULT_ALLOWLIST

    async def _run(self, args: HttpGetArgs) -> dict[str, Any]:
        host = self._validate_url(args.url)

        async with httpx.AsyncClient(
            follow_redirects=False,  # a redirect is a URL that passed no checks
            timeout=self.timeout_seconds - 1,
        ) as client:
            response = await client.get(args.url, headers={"user-agent": "AMOS/0.2"})

        if response.is_redirect:
            raise ToolValidationError(
                f"{args.url} redirected to "
                f"{response.headers.get('location', 'elsewhere')}; redirects are not followed. "
                "Request the final URL directly."
            )

        body = response.text[:_MAX_BYTES]
        return {
            "url": args.url,
            "host": host,
            "status_code": response.status_code,
            "truncated": len(response.text) > _MAX_BYTES,
            "content": body,
        }

    def _validate_url(self, raw_url: str) -> str:
        parsed = urlparse(raw_url)

        if parsed.scheme != "https":
            raise ToolValidationError(
                f"Only https:// URLs are permitted, got '{parsed.scheme or 'no scheme'}'"
            )

        host = (parsed.hostname or "").lower()
        if not host:
            raise ToolValidationError(f"Could not parse a hostname from '{raw_url}'")

        if not self._is_allowlisted(host):
            raise ToolValidationError(
                f"Host '{host}' is not on the allowlist. Permitted: "
                f"{', '.join(sorted(self._allowlist))}"
            )

        self._reject_internal_addresses(host)
        return host

    def _is_allowlisted(self, host: str) -> bool:
        """Exact match, or a subdomain of an allowlisted host.

        The leading dot matters: without it, `evil-github.com` would match a
        naive `endswith("github.com")` check.
        """
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self._allowlist)

    @staticmethod
    def _reject_internal_addresses(host: str) -> None:
        """Defence in depth: an allowlisted name must not resolve internally.

        Guards against a DNS record for an allowlisted host being pointed at an
        internal address, and against cloud metadata endpoints.
        """
        try:
            resolved = socket.gethostbyname(host)
        except OSError as exc:
            raise ToolValidationError(f"Could not resolve host '{host}'") from exc

        address = ipaddress.ip_address(resolved)
        if address.is_private or address.is_loopback or address.is_link_local:
            raise ToolValidationError(
                f"Host '{host}' resolves to the internal address {resolved}; refusing to fetch."
            )
