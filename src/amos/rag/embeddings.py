"""Embedding providers.

## The normalisation trap

`gemini-embedding-001` returns 3072 dimensions by default, and pgvector's HNSW
index supports at most 2000 for the `vector` type — so the default output cannot
be indexed (ADR-008). The model supports Matryoshka (MRL) truncation, so AMOS
asks for 1536.

**Truncating breaks L2 normalisation.** Measured against the live API:

    3072 dims -> L2 norm = 1.000000
    1536 dims -> L2 norm = 0.686517

Cosine distance assumes unit vectors. Feeding un-normalised vectors to pgvector's
`vector_cosine_ops` does not raise, does not warn, and returns **wrong rankings**
— retrieval quietly gets worse and nothing anywhere says so.

So every truncated embedding is re-normalised here, at the boundary, and a test
asserts the norm. This is the single easiest way to build a RAG pipeline that
looks fine and retrieves badly.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Protocol, runtime_checkable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from amos.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from amos.observability import log_event

logger = logging.getLogger(__name__)

#: pgvector HNSW indexes `vector` up to 2000 dims. 1536 sits comfortably under it.
EMBEDDING_DIMENSIONS = 1536

#: gemini-embedding-001 accepts 2048 input tokens. Chunking must stay well under.
MAX_INPUT_TOKENS = 2048

#: Embedding is asymmetric: a question and a passage are embedded differently.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

#: The embedding free tier is a PER-MINUTE quota (measured: 100), unlike
#: generateContent's per-day one — and it counts *contents*, not HTTP requests.
#: A batch of 50 texts consumes 50 units, so batching reduces round trips but not
#: quota. Because the window is a minute rather than a day, waiting is a real
#: strategy here: a rate-limited ingest finishes late instead of failing.
_RETRY_DELAY_PATTERN = re.compile(r"retry in (\d+(?:\.\d+)?)s")
DEFAULT_MAX_RATE_LIMIT_RETRIES = 5
#: ~25 contents per 16s stays under 100/min with headroom.
_PACE_SECONDS = 16.0


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. Mirrors `LLMProvider`'s design for the same reason:
    tests must never touch the network."""

    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def l2_norm(vector: list[float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def normalise(vector: list[float]) -> list[float]:
    """Scale to unit length.

    Mandatory after MRL truncation. A zero vector is returned unchanged rather
    than producing NaNs — it cannot be normalised and should not crash ingestion.
    """
    magnitude = l2_norm(vector)
    if magnitude == 0.0:
        return vector
    return [component / magnitude for component in vector]


class GeminiEmbeddings:
    """Gemini embeddings, MRL-truncated to a pgvector-indexable size."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        dimensions: int = EMBEDDING_DIMENSIONS,
        *,
        batch_size: int = 25,
        timeout: float = 60.0,
        max_rate_limit_retries: int = DEFAULT_MAX_RATE_LIMIT_RETRIES,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self.dimensions = dimensions
        self._batch_size = batch_size
        self._timeout = timeout
        self._max_rate_limit_retries = max_rate_limit_retries

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages for storage.

        Batched: one request per `batch_size` texts rather than one per text.
        On a quota measured in requests per day, that is the difference between
        ingesting a corpus and exhausting the day.
        """
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(await self._embed(batch, TASK_DOCUMENT))
            # Pace deliberately: the quota is per minute and counts contents, so
            # firing batches as fast as possible guarantees hitting it. Pausing
            # between batches is cheaper than waiting out a 429.
            if start + self._batch_size < len(texts):
                await asyncio.sleep(_PACE_SECONDS)
        log_event(logger, "embeddings.created", count=len(vectors), dims=self.dimensions)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed a question.

        `task_type` differs from documents deliberately: retrieval is asymmetric.
        A question and the passage answering it are not the same kind of text,
        and the model encodes them differently. Using RETRIEVAL_DOCUMENT for
        queries measurably degrades recall.
        """
        return (await self._embed([text], TASK_QUERY))[0]

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        """One request, waiting out rate limits.

        Unlike generateContent's daily quota — where waiting is pointless — the
        embedding limit resets every minute, and the API tells us how long to
        wait. Honouring that turns a failed ingest into a slower one.
        """
        for attempt in range(self._max_rate_limit_retries + 1):
            try:
                return await self._embed_once(texts, task_type)
            except ProviderRateLimitError as exc:
                if attempt == self._max_rate_limit_retries:
                    raise
                delay = _retry_delay_from(str(exc))
                log_event(
                    logger,
                    "embeddings.rate_limited",
                    attempt=attempt + 1,
                    waiting_seconds=delay,
                )
                await asyncio.sleep(delay)
        raise ProviderError("unreachable")

    async def _embed_once(self, texts: list[str], task_type: str) -> list[list[float]]:
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.embed_content(
                    model=self._model,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        output_dimensionality=self.dimensions,
                        task_type=task_type,
                    ),
                ),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(f"Embedding request exceeded {self._timeout}s") from exc
        except genai_errors.ClientError as exc:
            status = getattr(exc, "code", None)
            if status == 429:
                raise ProviderRateLimitError(f"Embedding quota exceeded. {exc}") from exc
            if status in (401, 403):
                raise ProviderAuthError("Embedding API rejected the key") from exc
            raise ProviderError(f"Embedding client error: {exc}") from exc
        except genai_errors.APIError as exc:
            raise ProviderError(f"Embedding API error: {exc}") from exc

        # Re-normalise. See the module docstring — this is not optional.
        return [normalise(list(e.values or [])) for e in (response.embeddings or [])]


class FakeEmbeddings:
    """Deterministic embeddings for tests.

    Hash-based rather than random, so the same text always yields the same
    vector and similarity between two texts is stable across runs. Vectors are
    normalised, like the real thing.
    """

    name = "fake"

    def __init__(self, dimensions: int = 16) -> None:
        self.dimensions = dimensions
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        """Bag-of-words hashing, so texts sharing words are genuinely similar.

        A purely random vector would make every retrieval test meaningless —
        nothing would ever be more relevant than anything else.
        """
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            vector[hash(word) % self.dimensions] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return normalise(vector)


def _retry_delay_from(message: str, default: float = 35.0) -> float:
    """Use the provider's own retry delay when it gives one.

    Guessing a backoff when the server has told you exactly how long to wait is
    strictly worse — too short and it fails again, too long and it idles.
    """
    match = _RETRY_DELAY_PATTERN.search(message)
    if match:
        return min(float(match.group(1)) + 2.0, 120.0)
    return default
