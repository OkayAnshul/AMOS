"""The outbound-request guard, shared by every tool that fetches a URL.

Extracted from `http_get` (V0.2) when `fetch_page` (V1.9) needed the same
checks: two copies of an SSRF guard drift, and the one that drifts is the hole.

The checks, all before any request: `https` only; a hostname; and **every**
address the name resolves to must be public — not private, loopback,
link-local, reserved, multicast or unspecified. (`http_get` checked only the
first IPv4 address; a name with an internal AAAA record would have passed.)
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from amos.errors import ToolValidationError

Resolver = Callable[[str], list[str]]


def resolve(host: str) -> list[str]:
    """Every address `host` resolves to, IPv4 and IPv6."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolValidationError(f"Could not resolve host '{host}'") from exc
    return sorted({str(info[4][0]) for info in infos})


def require_public_https(url: str, resolver: Resolver = resolve) -> str:
    """The URL's host, if it is https and resolves only to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ToolValidationError(
            f"Only https:// URLs are permitted, got '{parsed.scheme or 'no scheme'}'"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ToolValidationError(f"Could not parse a hostname from '{url}'")
    for raw in resolver(host):
        address = ipaddress.ip_address(raw.split("%")[0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ToolValidationError(
                f"Host '{host}' resolves to the internal address {raw}; refusing to fetch."
            )
    return host
