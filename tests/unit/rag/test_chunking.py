"""Chunking decides what the system can find."""

from __future__ import annotations

from amos.rag.chunking import MIN_CHUNK_SIZE, chunk_markdown

DOC = """# Title

Intro paragraph that is long enough to survive the minimum-size filter applied
during chunking, with several words in it.

## Why pgvector

We chose pgvector over Qdrant because chunk and embedding live in one row and
one transaction, so a partial failure cannot leave the index disagreeing with
the source of truth.

## Retry policy

Retries use exponential backoff with full jitter, because tasks failing together
would otherwise retry together indefinitely.
"""


def test_splits_on_headings() -> None:
    chunks = chunk_markdown(DOC)
    headings = [c.heading for c in chunks]
    assert "Why pgvector" in headings
    assert "Retry policy" in headings


def test_heading_is_prepended_to_its_chunks() -> None:
    """A chunk from the middle of a section otherwise loses the only words that
    say what it is about — which are the words a question would use."""
    chunks = chunk_markdown(DOC)
    pgvector_chunk = next(c for c in chunks if c.heading == "Why pgvector")
    assert pgvector_chunk.content.startswith("Why pgvector")
    assert "Qdrant" in pgvector_chunk.content


def test_chunks_are_indexed_in_order() -> None:
    chunks = chunk_markdown(DOC)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_source_metadata_is_attached() -> None:
    chunks = chunk_markdown(DOC, source="docs/03-adr.md")
    assert all(c.metadata["source"] == "docs/03-adr.md" for c in chunks)


def test_long_sections_are_split_by_size() -> None:
    long_doc = "# Title\n\n## Section\n\n" + ("word " * 2000)
    chunks = chunk_markdown(long_doc, chunk_size=500, overlap=50)
    assert len(chunks) > 2
    assert all(c.char_count <= 600 for c in chunks)


def test_overlap_preserves_boundary_text() -> None:
    """A sentence answering a question can land exactly on a split point."""
    body = ". ".join(f"sentence number {i} with some filler words" for i in range(80))
    chunks = chunk_markdown(f"# T\n\n## S\n\n{body}", chunk_size=400, overlap=120)
    joined = " ".join(c.content for c in chunks)
    assert len(joined) > len(body), "overlap means text appears more than once"


def test_tiny_sections_are_dropped() -> None:
    """A three-word chunk carries too little to be worth retrieving."""
    chunks = chunk_markdown("# T\n\n## Empty\n\nx\n\n## Real\n\n" + ("content " * 40))
    assert all(c.char_count >= MIN_CHUNK_SIZE for c in chunks)


def test_document_without_headings_still_chunks() -> None:
    chunks = chunk_markdown("Just plain prose. " * 50)
    assert len(chunks) >= 1
    assert chunks[0].heading is None


def test_empty_document_produces_no_chunks() -> None:
    assert chunk_markdown("") == []


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_markdown(DOC, chunk_size=100, overlap=100)
