"""Pre-flight for the demo: `make demo` (V2.0).

Checks everything a live demo depends on, says exactly what to fix if a check
fails, and prints how the run will go. Read-only: it changes nothing.
"""

from __future__ import annotations

import asyncio
import sys
import urllib.request

from sqlalchemy import text

from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory, session_scope
from amos.database.usage import usage_today

OK, WARN, FAIL = "ok  ", "warn", "FAIL"


def _line(status: str, what: str, detail: str) -> None:
    print(f"  [{status}] {what:<22} {detail}")


async def _checks() -> int:
    settings = get_settings()
    failures = 0
    print("AMOS demo pre-flight\n")

    if settings.gemini_api_key:
        _line(OK, "Gemini key", f"set; model {settings.llm_model}")
    else:
        failures += 1
        _line(FAIL, "Gemini key", "AMOS_GEMINI_API_KEY is empty in .env")

    engine = create_engine(settings)
    try:
        async with session_scope(create_session_factory(engine)) as session:
            revision = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
            from amos.api.views import _migration_head

            head = _migration_head()
            if head and revision != head:
                failures += 1
                _line(FAIL, "Migrations", f"at {revision}, code expects {head}: run `make migrate`")
            else:
                _line(OK, "Migrations", f"at head ({revision})")

            docs, chunks = (
                await session.execute(
                    text(
                        "SELECT count(*), coalesce(sum(chunk_count), 0) FROM documents "
                        "WHERE user_id IS NULL"
                    )
                )
            ).one()
            if chunks < 500:
                failures += 1
                _line(FAIL, "Corpus", f"{chunks} passages: run `make ingest`, then `make backup`")
            else:
                _line(OK, "Corpus", f"{docs} documents, {chunks} passages")

            users = (
                (await session.execute(text("SELECT name FROM users ORDER BY name")))
                .scalars()
                .all()
            )
            _line(OK if users else FAIL, "Users", ", ".join(users) or "none: run `make migrate`")
            failures += 0 if users else 1

            usage = await usage_today(session)
            left = settings.llm_daily_request_limit - usage["calls_today"]
            status = OK if left >= 150 else WARN if left >= 60 else FAIL
            failures += status == FAIL
            _line(
                status,
                "Quota today",
                f"{usage['calls_today']} of {settings.llm_daily_request_limit} model calls used; "
                f"{left} left (a full demo pass needs ~120). Resets at midnight Pacific.",
            )
    except Exception as exc:  # noqa: BLE001 - a pre-flight reports, it does not crash
        failures += 1
        _line(FAIL, "Database", f"{type(exc).__name__}: run `make up` and `make migrate`")
    finally:
        await engine.dispose()

    try:
        with urllib.request.urlopen(f"{settings.searxng_url.rstrip('/')}/healthz", timeout=3) as r:
            ok = r.status == 200
    except OSError:
        ok = False
    _line(
        OK if ok else WARN,
        "Web search",
        "SearXNG answering" if ok else "not running: `make web-up` (queries 8 and 9 need it)",
    )

    print()
    if failures:
        print(f"{failures} check(s) failed. Fix them, then run `make demo` again.")
        return 1
    print("Ready. The page is at http://127.0.0.1:8000/ — paste the presenter's API key.")
    print("Script: docs/27-demo-script.md. Queries 8 and 9 take 1-3 minutes each.")
    return 0


def main() -> int:
    return asyncio.run(_checks())


if __name__ == "__main__":
    sys.exit(main())
