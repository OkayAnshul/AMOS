"""Today's provider usage, for the quota meter (V1.7).

**Deliberately not owner-scoped.** A free-tier quota belongs to the API key, not
to an AMOS user, so the honest figure is everything this server has spent. It
returns counts only — no goal, no content — which is why it sits outside the
scoped modules rather than as an exception inside one.

**What it cannot see:** calls made outside a run (the eval harness, the memory
trials, the routing CLI) and every embedding call. Those are not recorded, so
the meter is a floor, and says so.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

#: Daily free-tier limits reset at midnight Pacific (account page, 2026-11-19).
_MIDNIGHT_PACIFIC = (
    "date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles') AT TIME ZONE 'America/Los_Angeles'"
)


async def usage_today(session: AsyncSession, model: str | None = None) -> dict[str, Any]:
    """Today's calls to `model` — the one whose quota the meter shows — and, since
    the fallback chain (ADR-021) spends several quotas, a count per model."""
    where = "WHERE model = :model" if model else ""
    params = {"model": model} if model else {}
    row = (
        await session.execute(
            sql_text(
                "SELECT "
                f"count(*) FILTER (WHERE created_at >= {_MIDNIGHT_PACIFIC}) AS today, "
                "count(*) FILTER (WHERE created_at >= now() - interval '60 seconds') "
                "AS last_minute, "
                f"count(*) FILTER (WHERE created_at >= {_MIDNIGHT_PACIFIC} "
                "AND error LIKE 'ProviderRateLimitError%') AS rate_limited, "
                f"count(*) FILTER (WHERE created_at >= {_MIDNIGHT_PACIFIC} "
                "AND error LIKE 'ProviderUnavailableError%') AS unavailable, "
                f"coalesce(sum(prompt_tokens + output_tokens) FILTER "
                f"(WHERE created_at >= {_MIDNIGHT_PACIFIC}), 0) AS tokens, "
                f"{_MIDNIGHT_PACIFIC} AS since "
                f"FROM llm_calls {where}"
            ),
            params,
        )
    ).one()
    per_model = (
        await session.execute(
            sql_text(
                "SELECT provider, model, count(*) AS calls, "
                "count(*) FILTER (WHERE error IS NOT NULL) AS failed "
                f"FROM llm_calls WHERE created_at >= {_MIDNIGHT_PACIFIC} "
                "GROUP BY provider, model ORDER BY calls DESC"
            )
        )
    ).all()
    return {
        "model": model,
        "by_model": [
            {"provider": r.provider, "model": r.model, "calls": r.calls, "failed": r.failed}
            for r in per_model
        ],
        "calls_today": row.today,
        "calls_last_minute": row.last_minute,
        "rate_limited_today": row.rate_limited,
        "unavailable_today": row.unavailable,
        "tokens_today": int(row.tokens),
        "since": row.since.isoformat(),
    }
