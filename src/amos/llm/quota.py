"""Which quota a Gemini 429 hit: one that resets in a minute, or one that resets tomorrow.

The API attaches a `retryDelay` to **both**, so the delay cannot tell them apart
(it said `57s` on a daily limit on 2026-09-14). The quota's id can:
`error.details[].violations[].quotaId` in Google's `QuotaFailure` payload, e.g.
`EmbedContentRequestsPerDayPerProjectPerModel-FreeTier`.

https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto
"""

from __future__ import annotations

from typing import Literal

QuotaScope = Literal["day", "minute"]

_QUOTA_FAILURE = "type.googleapis.com/google.rpc.QuotaFailure"


def quota_ids(body: object) -> list[str]:
    """Every `quotaId` in a 429 body. Tolerates any shape, returns [] for none."""
    if isinstance(body, list) and len(body) == 1:
        body = body[0]
    if not isinstance(body, dict):
        return []
    error = body.get("error", body)
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return []
    ids: list[str] = []
    for detail in details:
        if not isinstance(detail, dict) or detail.get("@type") != _QUOTA_FAILURE:
            continue
        for violation in detail.get("violations") or []:
            if isinstance(violation, dict) and isinstance(violation.get("quotaId"), str):
                ids.append(violation["quotaId"])
    return ids


def quota_scope(body: object) -> QuotaScope | None:
    """`day` if any violated quota is daily, else `minute` if any is per-minute.

    A daily violation wins: if both are exhausted, waiting a minute fixes
    nothing. `None` means the body named no recognisable quota — callers keep
    their old behaviour (wait and retry) rather than guess.
    """
    ids = quota_ids(body)
    if any("PerDay" in quota_id for quota_id in ids):
        return "day"
    if any("PerMinute" in quota_id for quota_id in ids):
        return "minute"
    return None
