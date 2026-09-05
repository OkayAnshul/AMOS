"""The in-memory store is test infrastructure — if it lies, retrieval tests lie."""

from __future__ import annotations

import uuid

import pytest

from amos.rag.store import InMemoryVectorStore, StoredChunk, VectorStore, _cosine_similarity


def chunk(content: str, embedding: list[float], index: int = 0) -> StoredChunk:
    return StoredChunk(
        document_id=uuid.uuid4(), chunk_index=index, content=content, embedding=embedding
    )


def test_in_memory_store_satisfies_the_protocol() -> None:
    assert isinstance(InMemoryVectorStore(), VectorStore)


async def test_search_orders_by_similarity() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            chunk("exact", [1.0, 0.0], 0),
            chunk("orthogonal", [0.0, 1.0], 1),
            chunk("close", [0.9, 0.1], 2),
        ]
    )
    hits = await store.search([1.0, 0.0], limit=3)
    assert [h.content for h in hits] == ["exact", "close", "orthogonal"]


async def test_min_score_excludes_weak_hits() -> None:
    store = InMemoryVectorStore()
    await store.upsert([chunk("a", [1.0, 0.0], 0), chunk("b", [0.0, 1.0], 1)])
    hits = await store.search([1.0, 0.0], limit=5, min_score=0.5)
    assert [h.content for h in hits] == ["a"]


async def test_upsert_replaces_rather_than_duplicates() -> None:
    """Re-ingesting must not double the corpus."""
    store = InMemoryVectorStore()
    doc = uuid.uuid4()
    first = StoredChunk(document_id=doc, chunk_index=0, content="v1", embedding=[1.0, 0.0])
    second = StoredChunk(document_id=doc, chunk_index=0, content="v2", embedding=[1.0, 0.0])

    await store.upsert([first])
    await store.upsert([second])

    assert await store.count() == 1
    hits = await store.search([1.0, 0.0])
    assert hits[0].content == "v2"


async def test_dimension_mismatch_raises_rather_than_scoring_nonsense() -> None:
    store = InMemoryVectorStore()
    await store.upsert([chunk("a", [1.0, 0.0])])
    with pytest.raises(ValueError, match="dimension mismatch"):
        await store.search([1.0, 0.0, 0.0])


def test_cosine_of_identical_vectors_is_one() -> None:
    assert _cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero() -> None:
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_of_a_zero_vector_is_zero_not_nan() -> None:
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
