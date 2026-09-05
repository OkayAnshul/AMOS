"""Ingestion and evaluation entry points.

python -m amos.rag.cli ingest docs/
python -m amos.rag.cli evaluate
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory, session_scope
from amos.observability import configure_logging
from amos.rag.embeddings import GeminiEmbeddings
from amos.rag.evaluation import AMOS_GOLDEN_SET, evaluate_retrieval
from amos.rag.ingest import Ingestor
from amos.rag.store import PgVectorStore


async def ingest(directory: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    embeddings = GeminiEmbeddings(
        settings.require_api_key(), dimensions=settings.embedding_dimensions
    )

    try:
        async with session_scope(factory) as session:
            report = await Ingestor(session, PgVectorStore(session), embeddings).ingest_directory(
                Path(directory)
            )
        print(
            f"ingested {report.documents_ingested} documents "
            f"({report.documents_skipped} unchanged), {report.chunks_created} chunks"
        )
    finally:
        await engine.dispose()


async def evaluate(k: int = 5) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    embeddings = GeminiEmbeddings(
        settings.require_api_key(), dimensions=settings.embedding_dimensions
    )

    try:
        async with session_scope(factory) as session:
            store = PgVectorStore(session)
            print(f"corpus: {await store.count()} chunks")
            result = await evaluate_retrieval(store, embeddings, AMOS_GOLDEN_SET, k=k)

        print(result.summary())
        if result.misses:
            print(f"\n{len(result.misses)} miss(es):")
            for question, expected, actual in result.misses:
                print(f"  Q: {question}")
                print(f"     expected {expected}, got {actual[:3]}")
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging("WARNING")
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    command = sys.argv[1]
    if command == "ingest":
        asyncio.run(ingest(sys.argv[2] if len(sys.argv) > 2 else "docs"))
    elif command == "evaluate":
        asyncio.run(evaluate(int(sys.argv[2]) if len(sys.argv) > 2 else 5))
    else:
        print(f"unknown command: {command}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
