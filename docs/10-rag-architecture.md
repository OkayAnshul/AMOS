# 10 — RAG Architecture

**Written at V0.5, with measured numbers.** This document was a stub until retrieval had been
run against a real corpus, because chunk size, top-k and thresholds written in advance are
guesses that then read as decisions (ADR-007).

## Pipeline

```
Markdown → heading-aware chunking → content hash → embed (1536d, re-normalised)
        → pgvector (chunk + embedding in ONE row) → HNSW cosine index
        → retrieval-as-a-Tool → cited answer, or an explicit refusal
```

## Measured results

Corpus: AMOS's own `docs/` — **28 documents, 300 chunks**.
Golden set: **12 questions**, phrased as a user would ask, not copied from the target passages.

| k | recall@k (any valid source) | strict (primary source only) | MRR |
|---|---|---|---|
| 1 | **91.7%** (11/12) | 50.0% (6/12) | **0.917** |
| 3 | **100%** (12/12) | 83.3% (10/12) | 0.958 |
| 5 | **100%** (12/12) | 91.7% (11/12) | 0.958 |

Reproduce:

```bash
.venv/bin/python -m amos.rag.cli ingest docs
.venv/bin/python -m amos.rag.cli evaluate 5
```

### What the two columns mean, and why both are reported

The first golden set had **one** expected source per question. It scored `recall@1 = 50%`, and
every single miss had retrieved an `interview/*.md` document instead of the expected reference
document.

Those documents are written as Q&A. For a *question* query they are frequently the best semantic
match — and they genuinely answer the question. Retrieval was not failing; **the labels were
wrong**. The set assumed one correct source where the corpus has real redundancy: a decision is
stated in an ADR and explained in an interview doc.

Ground truth was widened to accept any document that actually answers the question, which moved
recall@1 from 50% to 91.7%.

**That move is exactly how a metric gets massaged until it looks good**, so both figures are
reported permanently. `strict` still counts only the single primary source. A source is added to
a question's accepted set only when it genuinely answers it — never because adding it raises the
number. The rule is written into the docstring of `GoldenQuestion` so the next person changing
the set inherits the constraint.

The real lesson: **MRR was the honest signal all along.** At k=1 it was already 0.917, meaning
the correct document was almost always rank 1 — while single-label recall said 50%.

## Embedding: the trap that fails silently

`gemini-embedding-001` returns **3072** dimensions. pgvector's HNSW index supports at most
**2000** for the `vector` type. The default output therefore **cannot be indexed** (ADR-008).

MRL truncation to 1536 solves that and creates a second problem. Measured against the live API:

```
3072 dims → L2 norm = 1.000000
1536 dims → L2 norm = 0.686517
```

Cosine distance assumes unit vectors. Passing un-normalised vectors to `vector_cosine_ops` does
not raise, does not warn, and returns **wrong rankings**. Retrieval quality degrades and nothing
anywhere reports it.

So every truncated embedding is re-normalised at the provider boundary, and
`test_truncated_vectors_would_not_be_unit_length_without_normalising` asserts it. This is the
single easiest way to build a RAG pipeline that looks fine and retrieves badly.

**Retrieval is asymmetric.** Documents are embedded with `task_type=RETRIEVAL_DOCUMENT`, queries
with `RETRIEVAL_QUERY`. A question and the passage answering it are not the same kind of text.

## Chunking

**Heading-aware first, size-based only as a fallback.** The corpus is Markdown, which carries
explicit structure, and a section is a coherent unit of meaning where an arbitrary
1000-character window is not.

| Parameter | Value | Why |
|---|---|---|
| Chunk size | 1000 chars (~250 tokens) | Far under the model's 2048-token input limit; large enough to carry an argument |
| Overlap | 150 chars | Fallback path only — a sentence answering a question can land exactly on a split |
| Minimum size | 60 chars | Below this a chunk carries too little to be worth retrieving |
| Split preference | paragraph → sentence → hard cut | Mid-sentence fragments embed poorly |

**The heading is prepended to every chunk of its section.** Without it, a chunk from the middle
of "Why pgvector, not Qdrant" loses the only words that say what it is about — which are exactly
the words a question about it would use.

The two failure modes this balances: a chunk that is too large dilutes its embedding with
unrelated content and ranks below chunks wholly about the topic; a chunk that is too small splits
the answer across two chunks so neither is useful alone.

## Storage

`chunks` holds content and `embedding vector(1536)` in the **same row**, written in the same
transaction — the consistency argument that decided ADR-001. A separate vector database would
make every write a distributed write with no shared transaction.

```sql
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
```

The query **must** use the matching `<=>` operator, or Postgres silently falls back to a
sequential scan — fast enough at 300 chunks to hide the mistake entirely.

`documents.content_hash` is UNIQUE, so re-ingesting unchanged content is a no-op. Verified:
a second `ingest docs` reports `0 documents (28 unchanged)`. Without it, ingesting twice doubles
the corpus and retrieval starts returning duplicates — which presents as a relevance problem and
is a bookkeeping one.

**Index timing:** at this corpus size it does not matter, but on a large corpus build the HNSW
index *after* loading. Inserting into an existing HNSW index is markedly slower than building one
over existing rows.

## Retrieval and grounding

Retrieval is a **Tool**, so the agent chooses when to use it. Not every goal needs the corpus —
"what is 17% of 2340" does not — and retrieving unconditionally would spend an embedding call and
pad the prompt with irrelevant passages.

| Setting | Value |
|---|---|
| `top_k` | 5 (configurable 1–10) |
| `min_score` | 0.30 cosine similarity |

**Empty retrieval returns an explicit refusal instruction**, not an empty list:

> "No relevant passages were found. Say that you could not find this in the documentation. Do NOT
> answer from your own knowledge and present it as if it came from the documents."

An empty list is something a model can quietly ignore before answering from memory — and an
unretrieved answer presented as a retrieved one is precisely the failure RAG exists to prevent.

Verified live: asked about a Kubernetes autoscaling policy AMOS does not have, it answered that
the documentation does not define one, and grounded that in what the docs *do* say.

## Operational limits

The embedding free tier is a **per-minute** quota — measured at **100**, and it counts *contents*
rather than HTTP requests, so a batch of 50 texts consumes 50 units. Unlike `generateContent`'s
daily quota, waiting is a real strategy: ingestion paces batches and honours the API's own
`retryDelay` on a 429, turning a failed ingest into a slower one.

Ingestion commits **per document**, so a rate-limited run keeps its progress rather than rolling
back 300 chunks of work.

## Not done

- **No hybrid search.** Vector-only. A keyword match on an exact identifier (`SKIP LOCKED`) would
  likely beat embeddings, and BM25 + vectors is the standard answer.
- **No reranking.** A cross-encoder over the top 20 usually beats raw top-5, at the cost of
  another model.
- **No query rewriting** for vague or multi-part questions.
- **No chunk-size sweep.** 1000/150 was chosen from reasoning and has not been compared against
  alternatives on this corpus. The harness to do that now exists.
- **No answer-level metrics.** recall@k measures whether the right passage was *retrieved*, not
  whether the answer was faithful to it. Groundedness scoring is V1.0.
- **12 questions is a small golden set** for judging retrieval quality, and it was written by the
  same person who wrote the corpus.
