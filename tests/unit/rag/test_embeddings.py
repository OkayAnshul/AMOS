"""Embedding invariants — chiefly the one that fails silently."""

from __future__ import annotations

import pytest

from amos.rag.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    FakeEmbeddings,
    l2_norm,
    normalise,
)


def test_dimensions_fit_pgvectors_hnsw_limit() -> None:
    """pgvector indexes `vector` up to 2000 dims. Exceed it and the index cannot
    be created at all — discovered after embedding a whole corpus, if unchecked."""
    assert EMBEDDING_DIMENSIONS <= 2000


def test_normalise_produces_a_unit_vector() -> None:
    assert l2_norm(normalise([3.0, 4.0])) == pytest.approx(1.0)


def test_normalise_handles_a_zero_vector_without_nan() -> None:
    """A zero vector cannot be normalised; it must not produce NaNs or crash
    ingestion halfway through a corpus."""
    assert normalise([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_normalising_preserves_direction() -> None:
    original = [1.0, 2.0, 3.0]
    scaled = normalise(original)
    ratios = [s / o for s, o in zip(scaled, original, strict=True)]
    assert all(r == pytest.approx(ratios[0]) for r in ratios)


def test_truncated_vectors_would_not_be_unit_length_without_normalising() -> None:
    """The V0.5 trap, as a test.

    Truncating an L2-normalised vector leaves it un-normalised. Measured on the
    real API: 3072 dims -> norm 1.000000; the same vector cut to 1536 -> 0.686517.
    pgvector's cosine distance assumes unit vectors and will not complain — it
    just ranks wrongly.
    """
    unit = normalise([1.0] * 3072)
    assert l2_norm(unit) == pytest.approx(1.0)

    truncated = unit[:1536]
    assert l2_norm(truncated) < 0.99, "truncation breaks normalisation"
    assert l2_norm(normalise(truncated)) == pytest.approx(1.0)


async def test_fake_embeddings_satisfy_the_protocol() -> None:
    assert isinstance(FakeEmbeddings(), EmbeddingProvider)


async def test_fake_embeddings_are_deterministic() -> None:
    """Otherwise every retrieval test would be flaky for the wrong reason."""
    fake = FakeEmbeddings()
    assert await fake.embed_query("hello world") == await fake.embed_query("hello world")


async def test_fake_embeddings_are_normalised() -> None:
    fake = FakeEmbeddings()
    assert l2_norm(await fake.embed_query("some text")) == pytest.approx(1.0)


async def test_fake_embeddings_make_similar_texts_similar() -> None:
    """A purely random fake would make every retrieval assertion meaningless —
    nothing would ever rank above anything else."""
    fake = FakeEmbeddings(dimensions=64)
    query = await fake.embed_query("pgvector qdrant vector database")
    related = await fake.embed_query("pgvector chosen over qdrant for consistency")
    unrelated = await fake.embed_query("exponential backoff jitter retries")

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert dot(query, related) > dot(query, unrelated)


async def test_empty_input_returns_no_vectors() -> None:
    assert await FakeEmbeddings().embed_documents([]) == []
