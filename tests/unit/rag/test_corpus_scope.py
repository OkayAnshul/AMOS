"""The corpus must fit one day's embedding quota (decisions-log, 2026-11-19).

The free tier allows 1000 embedded contents a day, and one chunk is one
content. A corpus that outgrows that cannot be rebuilt in a day — which is what
turned the 2026-09-14 rebuild into a two-day job. This fails when `docs/` grows
past the line, so the scope is re-decided deliberately rather than discovered
halfway through an ingest.
"""

from __future__ import annotations

from pathlib import Path

from amos.rag.chunking import chunk_markdown
from amos.rag.cli import CORPUS_EXCLUDE

DOCS = Path(__file__).resolve().parents[3] / "docs"
DAILY_EMBEDDING_QUOTA = 1000
#: Left for the same day's `make retrieval` and `make eval-baseline` queries.
HEADROOM = 50


def test_the_corpus_fits_one_days_embedding_quota() -> None:
    chunks = sum(
        len(chunk_markdown(path.read_text(encoding="utf-8")))
        for path in DOCS.rglob("*.md")
        if not set(path.relative_to(DOCS).parts) & set(CORPUS_EXCLUDE)
    )
    assert chunks <= DAILY_EMBEDDING_QUOTA - HEADROOM, (
        f"docs/ is {chunks} chunks; a rebuild no longer fits one day's quota. "
        "Re-decide CORPUS_EXCLUDE in amos.rag.cli (and record why)."
    )


def test_every_excluded_name_names_something_real() -> None:
    """A misspelt exclusion excludes nothing and fails silently — the corpus just
    grows. Each name must match a directory or a file under docs/."""
    names = {part for path in DOCS.rglob("*") for part in path.relative_to(DOCS).parts}
    missing = [name for name in CORPUS_EXCLUDE if name not in names]
    assert not missing, f"CORPUS_EXCLUDE names nothing under docs/: {missing}"
