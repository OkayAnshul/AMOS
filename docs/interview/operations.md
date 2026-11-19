# Interview — Operations (V1.5)

**Advance gate: V1.6 does not begin until these can be answered unaided.**

The milestone that made the demo's foundations boring: quotas that fail honestly, a corpus that can
be rebuilt in a day and restored in seconds, and a test that stopped lying. Note what is still *not*
claimed: no automated backups, no retention policy, and the HNSW index's health is measured only
through `make retrieval`.

---

### The embedding retry honoured the API's own `retryDelay`. Why was that wrong?

Because the API attaches a `retryDelay` to **both** of its embedding limits: 100 contents a minute,
which waiting fixes, and 1000 a day, which it does not. On 2026-09-14 the daily limit said "retry in
57s", and the loop slept through every attempt before failing anyway. The delay can't tell the two
apart. The quota's name can: `EmbedContentRequestsPerDayPerProjectPerModel-FreeTier`.

### Where does AMOS read that name from, and why not from the error message?

From `error.details[].violations[].quotaId`, Google's structured `QuotaFailure` payload
(`google/rpc/error_details.proto`). `amos.llm.quota.quota_scope` returns `day`, `minute` or `None`.
The message is prose for humans and can change wording at any time. The structured field exists for
machines. If both a daily and a per-minute quota are violated, `day` wins, because waiting a minute
fixes nothing.

### What happens if Google renames its quota ids?

`quota_scope` returns `None`, and the retry keeps its old behaviour: it waits and retries. That is
deliberate. An unrecognised error degrades to "slow" rather than to a guess. What you lose is the
fast failure, not correctness.

### Generation doesn't retry on 429. Why classify its errors too?

For the person in front of the screen. `gemini-3.5-flash` allows 20 calls a day, and a demo can
spend that. "Daily quota spent, resets at midnight Pacific" and "slow down for a minute" call for
different actions, and only the quota's scope says which one applies. V1.9's page shows it.

### The pgvector test failed intermittently for two months. What was actually wrong?

**The test asserted exact results from an approximate index, and the planner chose between the
two.** On a small table with fresh statistics, Postgres picks a sequential scan plus a sort, which
is exact, and the test passed. With stale statistics it picks the HNSW index, which is approximate.
That index carried months of dead entries from rolled-back test inserts, and its graph couldn't reach
the second row, so the test failed. Autovacuum, which refreshes statistics, decided which plan ran.

### Why not just turn on `hnsw.iterative_scan`? It exists for exactly this.

It was the planned fix, written in the decisions log before the code. Then it was **measured and
rejected**. With the index forced, `LIMIT 2` returned 1 row in every mode: off, `relaxed_order` and
`strict_order`. Iterative scan keeps exploring the graph, but an outlier with no inbound edges can't
be reached however long you explore. The README's case is filtering, where the rows are reachable
but rejected after the scan. This case was rows that couldn't be reached at all.

Worth saying in an interview: the decision log keeps the rejected entry, struck through, with the
measurement. A plan that a measurement disproved is evidence the process works.

### What does the test check now?

Two separate things it used to blur:
1. **Semantics, on an exact scan** (`enable_indexscan = off`): both rows come back, in order, with
   score 1.0 for the identical vector, and the source is joined.
2. **That the HNSW index can serve the query**, via `EXPLAIN` with sequential scans and sorts
   disabled, so the index is the only way left to produce the order. Change `<=>` to `<->` and this
   test fails. That is the regression the original docstring feared: a wrong operator silently falls
   back to a sequential scan, and the cost only shows on a large corpus.

### Why did the tests get their own database?

They shared `amos` with the demo. A rolled-back insert isn't free: it leaves a dead entry in every
index, which is how an empty `chunks` table came to carry a 78 MB vector index. Tests that commit for
real (`db_factory`) had also leaked rows, including a dead-lettered run that showed up in
`GET /v1/runs/dead-letter`, the endpoint V1.9 puts on a panel in front of an audience.
`amos_test` is created and migrated on first use, through `alembic upgrade head` rather than
`create_all`, so the tests run against what the migrations build.

### Why exclude `docs/build-along/` from the corpus?

The free tier embeds 1000 contents a day, and one chunk is one content. All of `docs/` is 1108
chunks, which takes two days. Without `build-along/` it's 850, which fits in one. `build-along/`
restates each milestone's contract, and no golden question cites it. A unit test fails if `docs/`
grows past the line, so the scope gets re-decided on purpose rather than discovered halfway through
an ingest.

### The first version of that decision also excluded `docs/interview/`. What changed?

Reading the golden set. Six retrieval questions **accept** an interview document as a correct
answer, and the corpus the old numbers were measured on included them. The claim that they
"competed with the expected sources" was the opposite of true. Corrected the same day, and the
correction is in the log.

### Is the re-measured retrieval number comparable with the old one?

No, and it isn't claimed to be. The top-level docs alone grew from the old corpus's 300 chunks to
677. Whatever is excluded, the rebuild starts a new series.

### What happened when you edited a document and re-ingested it, before V1.5?

It was indexed **twice**. `documents` is unique on `content_hash`, not `source`, so an edited file
counted as a new document and the old version's chunks stayed retrievable beside it. Now the new
version deletes earlier system-corpus versions of the same source **in the same transaction** as its
insert. If embedding the new version fails, the old one is still there. Uploads by users are never
touched.

### Walk through `make restore-drill`. Why a scratch database?

It restores the newest dump into `amos_drill`, probes a vector search there, compares every table's
row count with `amos`, prints the table, and drops the copy. It never touches `amos`. The project
already destroyed its own database once with a "reversibility check" (`alembic downgrade base`,
2026-09-13). A drill that can only damage a scratch copy can be run without thinking twice, which
is the only way a drill gets run at all.

### What does the drill not prove?

That a restore works under pressure, from an old dump, into a newer schema. It restores the
newest dump into the same schema version, minutes after taking it. `make restore` exists for the
real case, behind a typed confirmation, and reminds you to run `make migrate` afterwards. Backups
are manual, with no schedule and no retention.

### Why does `make backup` use `podman exec` rather than `pg_dump` on the host?

The host has no Postgres client, and the server's version is pinned by the container image. A
`pg_dump` run inside the container always matches the server it dumps. The cost: it needs the
container runtime, which these sessions can't start, so the drill is run from a normal terminal.
