"""Aggregates over one user's stored runs, for the insights panel (V1.7).

Computed from rows, not from OpenTelemetry: the meter only exports over OTLP and
keeps nothing readable in-process, and a figure derived from stored runs can be
re-derived by anyone with the database. Every statement here is scoped to the
owner, and a source-scan test asserts it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


async def insights(session: AsyncSession, owner: uuid.UUID) -> dict[str, Any]:
    params = {"owner": str(owner)}

    async def rows(statement: str) -> list[Any]:
        return list(await session.execute(sql_text(statement), params))

    by_status = {
        r.status: r.n
        for r in await rows(
            "SELECT status, count(*) AS n FROM runs WHERE user_id = :owner GROUP BY status"
        )
    }
    latency = (
        await rows(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, "
            "avg(total_tokens) AS tokens, count(*) AS n "
            "FROM runs WHERE user_id = :owner AND completed_at IS NOT NULL"
        )
    )[0]
    per_day = [
        {"day": r.day.isoformat(), "runs": r.n, "tokens": int(r.tokens or 0)}
        for r in await rows(
            "SELECT date_trunc('day', created_at)::date AS day, count(*) AS n, "
            "sum(total_tokens) AS tokens FROM runs "
            "WHERE user_id = :owner AND created_at > now() - interval '14 days' "
            "GROUP BY 1 ORDER BY 1"
        )
    ]
    calls_by_purpose = [
        {
            "purpose": r.purpose or "unattributed",
            "calls": r.n,
            "tokens": int(r.tokens or 0),
            "avg_latency_ms": int(r.latency or 0),
        }
        for r in await rows(
            "SELECT c.purpose, count(*) AS n, sum(c.prompt_tokens + c.output_tokens) AS tokens, "
            "avg(c.latency_ms) AS latency FROM llm_calls c JOIN runs r ON r.id = c.run_id "
            "WHERE r.user_id = :owner GROUP BY c.purpose ORDER BY n DESC"
        )
    ]
    errors = {
        r.kind: r.n
        for r in await rows(
            "SELECT split_part(c.error, ':', 1) AS kind, count(*) AS n "
            "FROM llm_calls c JOIN runs r ON r.id = c.run_id "
            "WHERE r.user_id = :owner AND c.error IS NOT NULL GROUP BY 1"
        )
    }
    tools = [
        {
            "tool": r.tool_name,
            "status": r.status,
            "calls": r.n,
            "avg_latency_ms": int(r.latency or 0),
        }
        for r in await rows(
            "SELECT t.tool_name, t.status, count(*) AS n, avg(t.latency_ms) AS latency "
            "FROM tool_calls t JOIN runs r ON r.id = t.run_id "
            "WHERE r.user_id = :owner GROUP BY 1, 2 ORDER BY n DESC"
        )
    ]
    events = {
        r.kind: r.n
        for r in await rows(
            "SELECT e.kind, count(*) AS n FROM run_events e JOIN runs r ON r.id = e.run_id "
            "WHERE r.user_id = :owner AND e.kind IN "
            "('task.retrying', 'task.timed_out', 'delegation.started', 'task.permanently_failed') "
            "GROUP BY 1"
        )
    }
    verdicts = {
        (r.verdict or "unknown"): r.n
        for r in await rows(
            "SELECT e.payload->>'verdict' AS verdict, count(*) AS n "
            "FROM run_events e JOIN runs r ON r.id = e.run_id "
            "WHERE r.user_id = :owner AND e.kind = 'critic.verdict' GROUP BY 1"
        )
    }
    agents = {
        r.agent: r.n
        for r in await rows(
            "SELECT e.agent, count(*) AS n FROM run_events e JOIN runs r ON r.id = e.run_id "
            "WHERE r.user_id = :owner AND e.kind = 'task.routed' GROUP BY 1"
        )
    }
    return {
        "runs_by_status": by_status,
        "completed_runs": latency.n,
        "latency_ms": {"p50": _int(latency.p50), "p95": _int(latency.p95)},
        "avg_tokens_per_run": _int(latency.tokens),
        "runs_per_day": per_day,
        "llm_calls_by_purpose": calls_by_purpose,
        "provider_errors": errors,
        "tool_calls": tools,
        "task_events": events,
        "critic_verdicts": verdicts,
        "routed_to": agents,
    }


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None
