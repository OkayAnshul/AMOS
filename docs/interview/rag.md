# Interview — Retrieval (V0.5)

**Advance gate: V0.6 does not begin until these can be answered unaided.**

---

### Why 1536 dimensions and not the model's default 3072?

Because pgvector's HNSW index supports at most **2000** dimensions for the `vector` type, and
`gemini-embedding-001` returns 3072 by default. The default output is literally unindexable —
you would get either an index-creation failure or a sequential scan on every query.

`gemini-embedding-001` supports Matryoshka truncation, so AMOS requests 1536: comfortably under
the limit, and Google documents the quality loss at that size as small.

Discovered in Phase 0 by reading pgvector's README rather than assuming. Finding it at V0.5
instead would have meant re-embedding the entire corpus.

### What breaks if you skip re-normalising after truncation?

Nothing visibly — which is what makes it dangerous.

Measured on the live API: at 3072 dims the vector has L2 norm `1.000000`; truncated to 1536 it is
`0.686517`. Cosine distance assumes unit vectors. pgvector's `vector_cosine_ops` does not raise,
does not warn, and returns **wrong rankings**. Retrieval silently gets worse and every metric you
have still reports a number.

So every truncated embedding is re-normalised at the provider boundary, with a test asserting the
norm.

### Why are queries and documents embedded differently?

Retrieval is asymmetric. A question ("why was pgvector chosen?") and the passage answering it are
different kinds of text, and the model encodes them differently.
`task_type=RETRIEVAL_DOCUMENT` for passages, `RETRIEVAL_QUERY` for queries. Using the document
type for queries measurably degrades recall.

### Why is retrieval a tool rather than something that always runs?

Because not every goal needs the corpus. "What is 17% of 2340" does not, and retrieving
unconditionally spends an embedding call and pads the prompt with irrelevant passages.

Making it a tool also means it inherits everything V0.2 built: schema-validated arguments,
timeouts, permissions, and visibility in the trace.

### What happens when nothing relevant is found?

The tool returns an **explicit instruction to refuse**, not an empty list:

> "No relevant passages were found... Do NOT answer from your own knowledge and present it as if
> it came from the documents."

An empty list is something a model can quietly ignore before answering from memory. An
unretrieved answer presented as retrieved is exactly the failure RAG exists to prevent — the
system looks grounded and is not.

Verified live: asked about a Kubernetes autoscaling policy AMOS does not have, it said the
documentation does not define one.

### Why heading-aware chunking rather than fixed-size windows?

The corpus is Markdown, so it carries explicit structure. A section is a coherent unit of
meaning; an arbitrary 1000-character window is not. Size-based splitting is the fallback for
sections too long to embed.

The detail that matters most: **the heading is prepended to every chunk of its section.** A chunk
from the middle of "Why pgvector, not Qdrant" otherwise loses the only words that say what it is
about — and those are precisely the words a question about it would use.

### Your recall@1 went from 50% to 92% by changing the ground truth. Isn't that cheating?

It is the exact shape of cheating, which is why both numbers are reported permanently.

What happened: the first golden set had one expected source per question. Every miss at k=1 had
retrieved an `interview/*.md` document — which is written as Q&A, is frequently the best semantic
match for a *question*, and **genuinely answers it**. Retrieval was not failing; the labels
assumed one correct source where the corpus has real redundancy.

Three guards:
1. `strict` recall (primary source only) is still reported: 50% / 83% / 92% at k=1/3/5.
2. A source is added only when it actually answers the question, never to raise a score. That
   rule is in `GoldenQuestion`'s docstring so the next person inherits it.
3. **MRR was the honest signal throughout** — 0.917 at k=1, i.e. the right document was almost
   always rank 1, while single-label recall said 50%.

If pressed: the defensible claim is "recall@5 = 100% on a 12-question set, MRR 0.958", with the
caveats that the set is small and was written by the same person as the corpus.

### Why recall@k first rather than an answer-quality metric?

Because it bounds everything downstream. A passage that is not retrieved cannot be used, however
good the model is. Fixing generation when the real problem is retrieval is wasted effort.

MRR is reported alongside because recall@5 treats "rank 1" and "rank 5" as identical, while a
model reading five passages does not.

Answer-level metrics — groundedness, faithfulness to the retrieved text — are V1.0. recall@k does
**not** measure whether the answer was faithful to what was retrieved.

### Why does re-ingesting the same documents do nothing?

`documents.content_hash` is UNIQUE and checked before chunking. Without it, running ingestion
twice doubles the corpus and retrieval starts returning the same passage repeatedly — which
presents as a relevance problem and is actually bookkeeping.

Verified: a second `ingest docs` reports `0 documents (28 unchanged)`.

### Why does ingestion commit per document instead of once?

Because embedding 300 chunks under a per-minute quota takes minutes. A single transaction would
mean a failure at chunk 290 discards all prior work, and would hold a pooled connection open for
the entire run.

This was a real change made mid-milestone: the first ingest hit a rate limit and rolled back
everything.

### The embedding quota behaves differently from the chat quota. How?

`generateContent` is **20 requests per day** per model. Embeddings are **100 per minute**, and
the quota counts *contents*, not HTTP requests — so a batch of 50 texts consumes 50 units.
Batching reduces round trips, not quota.

Because the window is a minute rather than a day, **waiting is a real strategy**: on a 429 the
provider returns a `retryDelay`, and honouring it turns a failed ingest into a slower one.
Guessing a backoff when the server has told you exactly how long to wait is strictly worse.

---

## What V0.5 does NOT demonstrate

- **No hybrid search.** Vector-only. An exact identifier like `SKIP LOCKED` would likely be found
  better by keyword matching; BM25 + vectors is the standard answer
- **No reranking**, no query rewriting
- **No chunk-size sweep** — 1000/150 was reasoned, not compared. The harness to compare now exists
- **No groundedness metric.** recall@k says the right passage was retrieved, not that the answer
  was faithful to it
- **12 questions**, written by the same person as the corpus — a small set with an obvious bias
- Still no memory across sessions (V0.6), still one agent type (V0.7)
