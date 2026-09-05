"""Retrieval evaluation.

Without a number, "we have RAG" means "there is a vector database in the repo".
This module produces the number.

**recall@k** — of the questions in the golden set, for how many does the correct
document appear in the top *k* results? It is the right first metric because it
bounds everything downstream: a passage that is not retrieved cannot be used,
however good the model is.

**MRR** (mean reciprocal rank) is also reported because recall@5 treats "ranked
first" and "ranked fifth" identically, while a model reading five passages does
not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from amos.rag.embeddings import EmbeddingProvider
from amos.rag.store import VectorStore


@dataclass(frozen=True)
class GoldenQuestion:
    """A question, with every source that legitimately answers it.

    `expected_sources` is a set rather than a single value because the corpus has
    genuine redundancy: a decision is stated in an ADR *and* explained in an
    interview document. Both are correct answers, and scoring one as a miss
    measures the labelling, not the retrieval.

    This was learned the hard way — see docs/10-rag-architecture.md. The first
    version of this set had one source per question, and every k=1 "miss"
    turned out to be a document that answered the question perfectly well.

    The obvious hazard: widening labels after seeing results is how a metric gets
    massaged until it looks good. The guard is that both numbers are reported,
    and a source is only added when it genuinely answers the question — never
    because adding it raises the score.
    """

    question: str
    expected_sources: frozenset[str]
    primary_source: str = ""
    note: str = ""

    @staticmethod
    def of(question: str, *sources: str, note: str = "") -> GoldenQuestion:
        return GoldenQuestion(
            question=question,
            expected_sources=frozenset(sources),
            primary_source=sources[0],
            note=note,
        )


@dataclass
class EvaluationResult:
    k: int
    total: int
    hits: int
    strict_hits: int = 0
    reciprocal_ranks: list[float] = field(default_factory=list)
    misses: list[tuple[str, str, list[str]]] = field(default_factory=list)

    @property
    def recall_at_k(self) -> float:
        """Any legitimately-answering source counts."""
        return self.hits / self.total if self.total else 0.0

    @property
    def strict_recall_at_k(self) -> float:
        """Only the single primary source counts.

        Reported alongside the lenient figure so widening the labels is visible
        rather than silent.
        """
        return self.strict_hits / self.total if self.total else 0.0

    @property
    def mrr(self) -> float:
        if not self.reciprocal_ranks:
            return 0.0
        return sum(self.reciprocal_ranks) / len(self.reciprocal_ranks)

    def summary(self) -> str:
        return (
            f"recall@{self.k} = {self.recall_at_k:.2%} ({self.hits}/{self.total})   "
            f"strict = {self.strict_recall_at_k:.2%} ({self.strict_hits}/{self.total})   "
            f"MRR = {self.mrr:.3f}"
        )


async def evaluate_retrieval(
    store: VectorStore,
    embeddings: EmbeddingProvider,
    questions: list[GoldenQuestion],
    *,
    k: int = 5,
) -> EvaluationResult:
    """Score retrieval against a golden set.

    Misses are recorded with what *was* returned, because "recall is 80%" is a
    scoreboard while "these four questions failed, and here is what came back
    instead" is something you can act on.
    """
    result = EvaluationResult(k=k, total=len(questions), hits=0)

    for item in questions:
        vector = await embeddings.embed_query(item.question)
        hits = await store.search(vector, limit=k, min_score=0.0)
        sources = [hit.source or str(hit.document_id) for hit in hits]

        rank = next(
            (i + 1 for i, source in enumerate(sources) if source in item.expected_sources),
            None,
        )
        if any(source == item.primary_source for source in sources):
            result.strict_hits += 1

        if rank is not None:
            result.hits += 1
            result.reciprocal_ranks.append(1.0 / rank)
        else:
            result.reciprocal_ranks.append(0.0)
            result.misses.append((item.question, item.primary_source, sources))

    return result


#: Golden set over AMOS's own documentation.
#:
#: Written to be answerable from the corpus and phrased as a user would ask —
#: NOT by copying sentences out of the documents. A question built from the
#: target passage's own wording tests string overlap, not retrieval, and would
#: report a recall figure that means nothing.
AMOS_GOLDEN_SET: list[GoldenQuestion] = [
    GoldenQuestion.of(
        "Why was pgvector chosen instead of a dedicated vector database?",
        "03-architecture-decisions.md",
    ),
    GoldenQuestion.of(
        "What stops a tool-calling loop from running forever?",
        "08-tool-specification.md",
        "interview/agents.md",
        note="Both state the iteration cap; the interview doc explains why.",
    ),
    GoldenQuestion.of(
        "How does the system prevent reading files outside the project directory?",
        "13-security.md",
        "interview/agents.md",
        note="Resolve-then-check containment is covered in both.",
    ),
    GoldenQuestion.of(
        "Why do retries need randomness added to the waiting time?",
        "11-orchestration.md",
        "interview/orchestration.md",
        "17-failure-recovery.md",
    ),
    GoldenQuestion.of(
        "What happens to remaining work when one step of a plan fails permanently?",
        "17-failure-recovery.md",
        "11-orchestration.md",
        "interview/orchestration.md",
    ),
    GoldenQuestion.of(
        "How is a duplicate submission of the same request handled?",
        "06-api-specification.md",
        "interview/persistence.md",
        "05-data-model.md",
    ),
    GoldenQuestion.of(
        "Which container image and volume path does the database use?",
        "18-deployment.md",
    ),
    GoldenQuestion.of(
        "Why is there no authentication yet?",
        "13-security.md",
        "05-data-model.md",
    ),
    GoldenQuestion.of(
        "What are the different kinds of memory the system distinguishes?",
        "04-domain-model.md",
    ),
    GoldenQuestion.of(
        "Why does the project avoid microservices?",
        "03-architecture-decisions.md",
        "02-system-architecture.md",
    ),
    GoldenQuestion.of(
        "What is the daily limit on model requests?",
        "21-technology-baseline.md",
    ),
    GoldenQuestion.of(
        "Why must tests avoid calling the real model API?",
        "15-testing.md",
        "21-technology-baseline.md",
    ),
]
