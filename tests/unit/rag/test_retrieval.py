"""Retrieval: ranking, grounding, and the refusal path."""

from __future__ import annotations

import uuid

import pytest

from amos.rag.embeddings import FakeEmbeddings
from amos.rag.retrieval import SearchKnowledgeTool
from amos.rag.store import InMemoryVectorStore, StoredChunk, VectorStore
from amos.tools.base import ToolCall, ToolStatus

CORPUS = {
    "adr.md": "Why pgvector not Qdrant. Chunk and embedding live in one row and one "
    "transaction so a partial failure cannot leave the index disagreeing.",
    "retry.md": "Retry policy uses exponential backoff with full jitter because tasks "
    "failing together would otherwise retry together indefinitely.",
    "state.md": "The task state machine raises on illegal transitions. Only the state "
    "module moves a task between states.",
}


async def build_store() -> tuple[VectorStore, FakeEmbeddings]:
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


def call(query: str, top_k: int = 3) -> ToolCall:
    return ToolCall(id="s1", name="search_knowledge", arguments={"query": query, "top_k": top_k})


async def test_relevant_passage_ranks_first() -> None:
    store, embeddings = await build_store()
    tool = SearchKnowledgeTool(store, embeddings, min_score=0.0)

    outcome = await tool.execute(call("pgvector Qdrant transaction"))

    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["passages"][0]["citation"] == "adr.md"


async def test_results_carry_citations() -> None:
    """An answer without a citation cannot be checked, which defeats the point."""
    store, embeddings = await build_store()
    outcome = await SearchKnowledgeTool(store, embeddings, min_score=0.0).execute(
        call("jitter backoff retry")
    )
    assert outcome.output is not None
    assert all(p["citation"] for p in outcome.output["passages"])


async def test_empty_retrieval_instructs_refusal_rather_than_returning_nothing() -> None:
    """The grounding rule. An empty list is something the model can ignore and
    then answer from memory, presenting it as retrieved."""
    store, embeddings = await build_store()
    tool = SearchKnowledgeTool(store, embeddings, min_score=0.99)

    outcome = await tool.execute(call("something entirely unrelated to the corpus"))

    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["found"] == 0
    assert "Do NOT answer from your own knowledge" in outcome.output["instruction"]


async def test_min_score_filters_weak_matches() -> None:
    store, embeddings = await build_store()
    permissive = await SearchKnowledgeTool(store, embeddings, min_score=0.0).execute(
        call("state machine transitions")
    )
    strict = await SearchKnowledgeTool(store, embeddings, min_score=0.95).execute(
        call("state machine transitions")
    )
    assert permissive.output is not None and strict.output is not None
    assert strict.output["found"] <= permissive.output["found"]


async def test_top_k_is_respected() -> None:
    store, embeddings = await build_store()
    outcome = await SearchKnowledgeTool(store, embeddings, min_score=0.0).execute(
        call("pgvector", top_k=2)
    )
    assert outcome.output is not None
    assert len(outcome.output["passages"]) <= 2


async def test_query_is_embedded_with_the_query_task_type() -> None:
    """Retrieval is asymmetric: questions and passages embed differently."""
    store, embeddings = await build_store()
    await SearchKnowledgeTool(store, embeddings).execute(call("anything"))
    assert embeddings.query_calls == ["anything"]


@pytest.mark.parametrize(
    "bad",
    [{"query": ""}, {"query": "x", "top_k": 0}, {"query": "x", "top_k": 99}],
)
async def test_invalid_arguments_are_rejected(bad: dict[str, object]) -> None:
    store, embeddings = await build_store()
    outcome = await SearchKnowledgeTool(store, embeddings).execute(
        ToolCall(id="s", name="search_knowledge", arguments=bad)
    )
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_scores_are_similarity_not_distance() -> None:
    """Higher must mean better, or every threshold in the system is inverted."""
    store, embeddings = await build_store()
    outcome = await SearchKnowledgeTool(store, embeddings, min_score=0.0).execute(
        call("pgvector Qdrant transaction")
    )
    assert outcome.output is not None
    scores = [p["score"] for p in outcome.output["passages"]]
    assert scores == sorted(scores, reverse=True)
    assert 0.0 <= scores[0] <= 1.0
