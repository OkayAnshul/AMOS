"""Measure memory storage reliability: `python -m amos.memory.cli [repeats]`

Costs real quota (~2 calls per trial). Capture a baseline with
AMOS_MEMORY_RECONCILE_ENABLED=false before judging any improvement.
"""

from __future__ import annotations

import asyncio
import sys

from amos.api.dependencies import build_agent
from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory
from amos.memory.trials import run_trials
from amos.observability import configure_logging


async def main(repeats: int) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    print(f"reconciler: {'ON' if settings.memory_reconcile_enabled else 'OFF (baseline)'}")
    print(f"model:      {settings.llm_model}\n")

    try:
        report = await run_trials(
            lambda: build_agent(settings, session_factory=factory), factory, repeats=repeats
        )
    finally:
        await engine.dispose()

    print(report.summary())


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
