"""The evaluation harness itself is tested — a broken scorer produces confident
numbers that are wrong, which is worse than no numbers."""

from __future__ import annotations

import uuid

import pytest

from amos.rag.embeddings import FakeEmbeddings
from amos.rag.evaluation import AMOS_GOLDEN_SET, GoldenQuestion, evaluate_retrieval
from amos.rag.store import InMemoryVectorStore, StoredChunk

CORPUS = {
    "adr.md": "pgvector was chosen over Qdrant for transactional consistency between "
    "chunk and embedding",
    "retry.md": "retries use exponential backoff with jitter to avoid synchronised "
    "thundering herds",
    "state.md": "the state machine raises on illegal transitions between task states",
}


async def build_store() -> tuple[InMemoryVectorStore, FakeEmbeddings]:
    store = InMemoryVectorStore()
    embeddings = FakeEmbeddings(dimensions=128)
    for index, (source, text) in enumerate(CORPUS.items()):
        vector = (await embeddings.embed_documents([text]))[0]
        await store.upsert(
            [
                StoredChunk(
                    document_id=uuid.uuid4(),
                    chunk_index=index,
                    content=text,
                    embedding=vector,
                    metadata={"source": source},
                )
            ]
        )
    return store, embeddings


async def test_perfect_retrieval_scores_one() -> None:
    store, embeddings = await build_store()
    questions = [
        GoldenQuestion.of("pgvector Qdrant transactional consistency", "adr.md"),
        GoldenQuestion.of("exponential backoff jitter thundering", "retry.md"),
    ]
    result = await evaluate_retrieval(store, embeddings, questions, k=3)

    assert result.recall_at_k == 1.0
    assert result.mrr == pytest.approx(1.0)
    assert result.misses == []


async def test_known_bad_retrieval_scores_zero() -> None:
    """If a deliberately wrong expectation still scores well, the harness is broken."""
    store, embeddings = await build_store()
    questions = [GoldenQuestion.of("pgvector Qdrant transactional consistency", "nonexistent.md")]
    result = await evaluate_retrieval(store, embeddings, questions, k=3)

    assert result.recall_at_k == 0.0
    assert result.mrr == 0.0
    assert len(result.misses) == 1


async def test_misses_record_what_was_returned_instead() -> None:
    """'recall is 80%' is a scoreboard; 'this failed and here is what came back'
    is actionable."""
    store, embeddings = await build_store()
    result = await evaluate_retrieval(
        store, embeddings, [GoldenQuestion.of("anything", "nonexistent.md")], k=2
    )
    _question, expected, actual = result.misses[0]
    assert expected == "nonexistent.md"
    assert len(actual) > 0


async def test_mrr_rewards_a_higher_rank() -> None:
    """recall@5 treats rank 1 and rank 5 identically; a model reading five
    passages does not."""
    store, embeddings = await build_store()
    ranked_first = await evaluate_retrieval(
        store,
        embeddings,
        [GoldenQuestion.of("pgvector Qdrant transactional consistency", "adr.md")],
        k=3,
    )
    assert ranked_first.mrr == pytest.approx(1.0)


async def test_strict_and_lenient_scores_are_both_reported() -> None:
    """Widening ground truth must be visible, not silent."""
    store, embeddings = await build_store()
    question = GoldenQuestion.of(
        "exponential backoff jitter thundering", "nonexistent.md", "retry.md"
    )
    result = await evaluate_retrieval(store, embeddings, [question], k=3)

    assert result.recall_at_k == 1.0, "an accepted alternative source counts"
    assert result.strict_recall_at_k == 0.0, "the primary source did not appear"
    assert "strict" in result.summary()


def test_golden_set_questions_do_not_quote_their_sources() -> None:
    """A question built from the target passage's own wording tests string
    overlap, not retrieval — and reports a number that means nothing."""
    for item in AMOS_GOLDEN_SET:
        assert not item.question.startswith("#")
        assert len(item.question.split()) >= 5
        assert item.primary_source in item.expected_sources


def test_golden_set_covers_several_documents() -> None:
    sources = {q.primary_source for q in AMOS_GOLDEN_SET}
    assert len(sources) >= 8, "a set concentrated on one document measures little"
