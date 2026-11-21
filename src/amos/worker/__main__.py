"""Run a worker: `python -m amos.worker`"""

from __future__ import annotations

import asyncio
import contextlib

from amos.agents.schemas import RunOptions
from amos.api.dependencies import build_provider, build_registry, build_run_agent
from amos.auth import Actor
from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory
from amos.memory.episodic import EpisodicMemory
from amos.observability import configure_logging
from amos.rag.embeddings import GeminiEmbeddings
from amos.worker.runner import Worker


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    # Built once, shared by every run's agent (V1.7): the provider holds an HTTP
    # client and the registry its embedding clients, and neither is per run.
    provider = build_provider(settings)
    registry = build_registry(settings, factory)

    def agent_for(options: RunOptions | None) -> object:
        return build_run_agent(
            settings, options, provider=provider, registry=registry, session_factory=factory
        )

    episodic = None
    if settings.memory_enabled:
        embeddings = GeminiEmbeddings(
            settings.require_api_key(),
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )

        def episodic(session: object, actor: Actor) -> EpisodicMemory:
            return EpisodicMemory(session, embeddings, actor)  # type: ignore[arg-type]

    try:
        worker = Worker(
            factory,
            agent_for,
            episodic=episodic,
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
