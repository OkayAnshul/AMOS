"""Retrieval, exposed as a Tool.

Retrieval is a tool rather than an always-on preprocessing step, deliberately.
Not every goal needs the corpus — "what is 17% of 2340" does not — and forcing a
retrieval on every request would spend an embedding call and pad the prompt with
irrelevant passages for no benefit. Making it a tool lets the agent decide, using
the same machinery that already validates arguments and enforces timeouts.

**Grounding rule:** when nothing relevant comes back, the tool says so explicitly
rather than returning an empty list that the model can quietly ignore and answer
from memory. An unretrieved answer presented as a retrieved one is the failure
mode RAG exists to prevent.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from amos.rag.embeddings import EmbeddingProvider
from amos.rag.store import VectorStore
from amos.tools.base import Permission, Tool

#: Below this cosine similarity a hit is noise. Measured on the AMOS corpus —
#: see docs/10-rag-architecture.md.
DEFAULT_MIN_SCORE = 0.30
DEFAULT_TOP_K = 5


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "A question or topic to look up in the indexed documents. "
            "Use the words you would expect to appear in the answer."
        ),
    )
    top_k: int = Field(
        default=DEFAULT_TOP_K,
        ge=1,
        le=10,
        description="How many passages to return.",
    )


class SearchKnowledgeTool(Tool):
    """Semantic search over the ingested corpus."""

    name: ClassVar[str] = "search_knowledge"
    description: ClassVar[str] = (
        "Search AMOS's indexed documents for passages relevant to a question. "
        "Use this whenever the answer might be in the project's documentation "
        "rather than in your own knowledge. Returns passages with citations."
    )
    input_schema: ClassVar[type[BaseModel]] = SearchKnowledgeArgs
    permission: ClassVar[Permission] = Permission.READ_LOCAL
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(
        self,
        store: VectorStore,
        embeddings: EmbeddingProvider,
        *,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._min_score = min_score

    async def _run(self, args: SearchKnowledgeArgs) -> dict[str, Any]:
        # Embedded with RETRIEVAL_QUERY, not RETRIEVAL_DOCUMENT: retrieval is
        # asymmetric, and using the document task type for queries measurably
        # degrades recall.
        vector = await self._embeddings.embed_query(args.query)
        hits = await self._store.search(vector, limit=args.top_k, min_score=self._min_score)

        if not hits:
            # Explicit, not empty. The model must be told that the corpus had
            # nothing, or it will answer from memory and present it as grounded.
            return {
                "query": args.query,
                "found": 0,
                "passages": [],
                "instruction": (
                    "No relevant passages were found in the indexed documents. "
                    "Say that you could not find this in the documentation. "
                    "Do NOT answer from your own knowledge and present it as if "
                    "it came from the documents."
                ),
            }

        return {
            "query": args.query,
            "found": len(hits),
            "passages": [
                {
                    "citation": hit.source or str(hit.document_id),
                    "heading": hit.heading,
                    "score": round(hit.score, 4),
                    "content": hit.content,
                }
                for hit in hits
            ],
            "instruction": (
                "Answer using ONLY these passages. Cite the 'citation' value of "
                "each passage you use. If they do not contain the answer, say so."
            ),
        }
