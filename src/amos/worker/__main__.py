"""Run a worker: `python -m amos.worker`"""

from __future__ import annotations

import asyncio
import contextlib

from amos.api.dependencies import build_agent
from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory
from amos.observability import configure_logging
from amos.worker.runner import Worker


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        worker = Worker(
            factory,
            lambda: build_agent(settings, session_factory=factory),
            poll_interval=settings.worker_poll_interval,
            max_attempts=settings.worker_max_attempts,
            visibility_timeout=settings.worker_visibility_timeout,
        )
        await worker.run_forever()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    # Ctrl-C is a normal way to stop a worker, not an error worth a traceback.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
