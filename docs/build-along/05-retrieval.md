# V0.5 — Retrieval (RAG)

**+1,877 lines.** Real retrieval with citations and a **measured** recall figure — not a vector
database with a claim attached.

---

## Where you are

An agent that plans, uses tools, and records everything. It knows only what the model knows.

## The problem

It cannot answer questions about *your* documents. And the failure mode when you ask anyway is the
dangerous one: a fluent, specific, confident answer that is invented.

## What you will have at the end

Ask it *"why was pgvector chosen over Qdrant?"* and get an answer citing the ADR it came from. Ask
it about a Kubernetes policy your documentation says does not exist, and get **"the documentation
does not define one"** rather than a plausible fabrication.

Plus a number: `recall@5`, reproducible by a command.

---

## The trap that defines this milestone

Read this before writing anything.

`gemini-embedding-001` returns **3072** dimensions. pgvector's HNSW index handles at most **2000**
for the `vector` type. So the default output **cannot be indexed** — you must truncate.

Matryoshka (MRL) truncation to 1536 solves that and creates a second problem. Measured against the
live API:

```
3072 dims → L2 norm = 1.000000
1536 dims → L2 norm = 0.686517     ← 31% off unit length
```

**Cosine distance assumes unit vectors.** pgvector's `vector_cosine_ops` does not raise, does not
warn, and returns **wrong rankings**. Retrieval quietly degrades and every metric you have still
reports a number.

**Every truncated embedding must be re-normalised, and you must have a test asserting it.** This is
the single easiest way to build a RAG pipeline that looks fine and retrieves badly.

---

## Build order

### 1. `src/amos/rag/embeddings.py` (~200 lines)

```python
EMBEDDING_DIMENSIONS = 1536      # <= 2000, pgvector's HNSW limit
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY    = "RETRIEVAL_QUERY"

@runtime_checkable
class EmbeddingProvider(Protocol):
    dimensions: int
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...

def l2_norm(vector) -> float: ...
def normalise(vector) -> list[float]:
    """Unit length. A ZERO vector returns unchanged — never NaN."""

class GeminiEmbeddings:
    """Batched. Re-normalises EVERY vector at this boundary."""

class FakeEmbeddings:
    """Bag-of-words hashing with a STABLE hash. See the trap."""
```

**Invariant** — no un-normalised vector leaves this module.

**Retrieval is asymmetric.** Documents use `RETRIEVAL_DOCUMENT`, queries use `RETRIEVAL_QUERY`. A
question and the passage answering it are different kinds of text; using the document type for
queries measurably degrades recall.

⚠️ **`FakeEmbeddings` must not use Python's `hash()`.**

<details><summary>Cause</summary>

`hash()` is randomised per process. A fake built on it produces different vectors every run, so
retrieval tests pass or fail by seed.

The determinism test will pass anyway, because it compares two calls *inside one process*.
**Testing determinism requires a value fixed outside the process** — shell out to a second
interpreter, or use `hashlib.blake2b`.
</details>

**Also make the fake semantically meaningful.** Bag-of-words hashing so texts sharing words are
genuinely similar. A purely random fake makes every retrieval assertion meaningless — nothing would
ever rank above anything else.

**Test first**
```python
def test_truncated_vectors_would_not_be_unit_length_without_normalising():
    unit = normalise([1.0] * 3072)
    truncated = unit[:1536]
    assert l2_norm(truncated) < 0.99          # the trap, as an assertion
    assert l2_norm(normalise(truncated)) == pytest.approx(1.0)

def test_dimensions_fit_pgvectors_hnsw_limit():
    assert EMBEDDING_DIMENSIONS <= 2000
```

---

### 2. `src/amos/rag/chunking.py` (~110 lines)

**Chunking decides what the system *can* find.** Two failure modes bound the choice:

- **Too large** — a chunk containing the answer plus three unrelated paragraphs dilutes its
  embedding, so it ranks below chunks wholly about the topic.
- **Too small** — the answer splits across two chunks and neither is useful alone.

```python
DEFAULT_CHUNK_SIZE = 1000     # ~250 tokens, far under the model's 2048 input limit
DEFAULT_OVERLAP = 150
MIN_CHUNK_SIZE = 60

def chunk_markdown(text, *, chunk_size, overlap, source=None) -> list[Chunk]:
    """Heading-aware FIRST. Size-based only as a fallback."""
```

**Why heading-aware** — the corpus is Markdown, which carries explicit structure. A section is a
coherent unit of meaning; an arbitrary 1000-character window is not.

**The detail that matters most: prepend the heading to every chunk of its section.** Without it, a
chunk from the middle of *"Why pgvector, not Qdrant"* loses the only words saying what it is about
— and those are exactly the words a question about it would use.

Overlap exists for the **fallback path only**: when a long section is split mid-prose, a sentence
answering a question can land on the boundary.

---

### 3. `src/amos/rag/store.py` (~200 lines)

```python
@runtime_checkable
class VectorStore(Protocol):
    async def upsert(self, chunks: list[StoredChunk]) -> int: ...
    async def search(self, embedding, *, limit=5, min_score=0.0) -> list[Hit]: ...
    async def count(self) -> int: ...

class PgVectorStore: ...        # chunk text and embedding in the SAME ROW
class InMemoryVectorStore: ...  # exact brute force, for tests
```

**Why a Protocol** — the same reason as `LLMProvider`: tests need an in-memory implementation
regardless, so the abstraction is paid for by V0.5's own tests. It also keeps the "reconsider Qdrant
above ~5M vectors" ADR honest — that would be a new class, not a rewrite.

**Why the in-memory store is exact, not approximate** — a test asserting "the right chunk ranks
first" should fail because retrieval is wrong, never because an ANN index happened to miss.

**`Hit.score` is cosine *similarity* (higher is better), converted from pgvector's *distance*.**
Exposing distance would invert the intuition of every caller and every threshold.

⚠️ **The query must use `<=>`** — the operator matching the index you built with
`vector_cosine_ops`. A different operator silently falls back to a sequential scan, which at 300
chunks is fast enough to hide the mistake completely.

---

### 4. Migration: `documents` and `chunks`

**Hand-written, not autogenerated.** Alembic cannot express `CREATE EXTENSION`, and `vector` is not
a SQLAlchemy core type:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
op.execute(f"ALTER TABLE chunks ADD COLUMN embedding vector({DIMENSIONS})")
op.execute("CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)")
```

`documents.content_hash` is **UNIQUE**. Without it, ingesting twice doubles the corpus and
retrieval starts returning duplicates — which presents as a relevance problem and is bookkeeping.

**Index timing:** at 300 chunks it does not matter, but on a large corpus build HNSW *after*
loading. Inserting into an existing HNSW index is markedly slower.

---

### 5. `src/amos/rag/ingest.py` (~120 lines)

```
parse → chunk → hash → embed → store
```

⚠️ **Do not wrap the whole corpus in one transaction.**

<details><summary>Why — and the original got this wrong</summary>

The first ingest hit a rate limit partway through and **rolled back all 300 embedded chunks**. The
rollback was correct; the *boundary* was wrong.

**Transaction boundaries should follow units of useful work, not units of code.** "All 300 chunks
atomically" reads as rigour and means "lose everything on any failure". Commit **per document**.

No unit test would have caught it: `FakeEmbeddings` cannot rate-limit, and a 3-document fixture
never runs long enough to fail partway.
</details>

⚠️ **The embedding quota is a different shape from the chat quota.**

<details><summary>Measured</summary>

```
quotaId: EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier
limit:   100      (per MINUTE, not per day)
```

And it counts **contents**, not HTTP requests — a batch of 50 texts consumes 50 units, so batching
reduces round trips but not quota.

Because the window is a minute, **waiting is a real strategy** (unlike the daily chat quota).
Honour the provider's own `retryDelay` on a 429 rather than guessing a backoff; it has told you
exactly how long to wait.
</details>

---

### 6. `src/amos/rag/retrieval.py` (~110 lines) — **retrieval is a Tool**

```python
class SearchKnowledgeTool(Tool):
    permission = Permission.READ_LOCAL
    async def _run(self, args) -> dict[str, Any]:
        vector = await self._embeddings.embed_query(args.query)     # QUERY task type
        hits = await self._store.search(vector, limit=args.top_k, min_score=self._min_score)
        if not hits:
            return {"found": 0, "passages": [],
                    "instruction": "No relevant passages were found... Do NOT answer from "
                                   "your own knowledge and present it as if it came from the documents."}
        return {"found": len(hits), "passages": [...with citations...], "instruction": "..."}
```

**Why a tool and not an always-on preprocessing step** — not every goal needs the corpus.
*"What is 17% of 2340"* does not, and retrieving unconditionally spends an embedding call and pads
the prompt with irrelevant passages. As a tool it also inherits V0.2's validation, timeouts and
trace visibility.

**Invariant — empty retrieval returns an explicit refusal instruction, never an empty list.** An
empty list is something a model can quietly ignore before answering from memory. **An unretrieved
answer presented as retrieved is precisely the failure RAG exists to prevent.**

---

### 7. `src/amos/rag/evaluation.py` (~150 lines) — **without this it is not RAG**

```python
@dataclass(frozen=True)
class GoldenQuestion:
    question: str
    expected_sources: frozenset[str]     # a SET — see below
    primary_source: str = ""

async def evaluate_retrieval(store, embeddings, questions, *, k=5) -> EvaluationResult:
    """recall@k, strict recall, MRR. Misses record what WAS returned instead."""
```

**Write the questions as a user would ask them — never by copying sentences out of the target
passage.** A question built from the passage's own wording tests string overlap, not retrieval, and
reports a number that means nothing.

**Report MRR alongside recall@k.** recall@5 treats "ranked first" and "ranked fifth" as identical;
a model reading five passages does not.

### 🔎 The lesson you are most likely to hit here

Your first run may score badly and **the labels may be wrong, not the retrieval.**

In the original, `recall@1` was 50% and *every* miss had retrieved an `interview/*.md` doc — which
is Q&A-formatted, so for a *question* query it is often the best semantic match, and it **genuinely
answers the question**. The set assumed one correct source where the corpus had real redundancy.

Widening ground truth took recall@1 from 50% to 91.7%. **That is exactly how a metric gets massaged
until it looks good**, so:

1. Report **both** figures permanently — strict (primary source only) and lenient.
2. Add a source only when it genuinely answers the question, never because it raises the score.
3. Put that rule in the docstring, so whoever edits the set next inherits the constraint.

**MRR was the honest signal throughout** — 0.917 at k=1 while single-label recall said 50%. **When
two metrics disagree that sharply, suspect the measurement before the system.**

---

## Checkpoint

```bash
.venv/bin/python -m amos.rag.cli ingest docs      # ~4 min, paced for the quota
.venv/bin/python -m amos.rag.cli ingest docs      # again → "0 documents (N unchanged)"
.venv/bin/python -m amos.rag.cli evaluate 5
```

Then both behaviours:
```bash
-d '{"goal":"Search the docs and explain why pgvector was chosen over Qdrant."}'
# → cites 03-architecture-decisions.md

-d '{"goal":"Search the docs for AMOS Kubernetes autoscaling policy."}'
# → "the documentation does not define one"
```

**The second is the milestone.** Without it you have a search engine with a language model attached.

---

## What this unlocks

A `VectorStore` and an embedding provider — which V0.6's semantic memory reuses rather than
duplicating.

Next: [`06-memory.md`](06-memory.md) — where the interesting decision is what **not** to put in
vectors.
