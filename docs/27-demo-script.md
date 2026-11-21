# 27 — Demo script

The live demo for a teacher, about 20 minutes. Every query below has run successfully from the
page; the timings are from those runs. Numbers here come from `engineering/current-state.md`.

---

## The morning of the demo

1. `make demo` — starts Postgres and SearXNG, checks the key, migrations, corpus, users, today's
   quota and web search, then serves the page. Every check should say `ok`.
2. Open <http://127.0.0.1:8000/>, paste the **presenter** key, Settings → Presenter mode on.
   Light theme for a projector in a lit room.
3. **Run queries 8 and 9 once.** Successful runs in History are the fallback if Gemini is overloaded
   later (see *When something fails*). That spends ~60 of the day's 500 model calls.
4. Run "Remember a fact" once, so Memory is not empty when you open it.

## The order

| # | Do | Takes | What it shows | Say |
|---|---|---|---|---|
| 1 | Open **System** | 1 min | model, today's quota, the bounds chain, features | "Everything a run may spend is bounded in code — per call, per task, per worker claim, per run." |
| 2 | **Percent, then subtract** | ~15 s | plan → two analyst tasks → calculator → critic | "The planner splits the goal; each task is routed to an agent; every tick is a stored model or tool call." Click a capsule: the inspector. |
| 3 | **Why pgvector** | ~10 s | retrieval with citations; open a source card | "It answers from the project's documents and shows the passage it used." |
| 4 | **An honest refusal** | ~10 s | refuses: the docs mention Kubernetes only to reject it | "Saying *I could not find that* is the correct answer here — inventing one is the worst failure." |
| 5 | **Remember a fact**, then **Recall it** | ~20 s each | memory across runs; open **Memory**, then History | "A fact persists across runs and processes; superseded values keep their history." |
| 6 | **Compare two queues** | ~70 s | web search, pages read, the writer's table, *Not sourced*, the citation check | While it runs: "Three lanes — researchers in parallel, then the writer. The writer has no tools; code checks that every source it cites was actually seen." |
| 7 | **Plan a Goa trip** | 2–3 min | three parallel researchers stacked, waits under the per-minute limit, analyst arithmetic, the itinerary | "It is waiting for a quota slot, not stuck — the wait is recorded. Watch the analyst do the arithmetic with a calculator." |
| 8 | Switch **Critic** off, run **Percent, then subtract** again | ~8 s | per-run options; fewer calls | "Features can be switched off per run — never on, if the server has them off." |
| 9 | Open **Insights**, then **Agents and tools** | 1 min | figures from stored runs; each agent's allowlist | "A tool an agent is not allowed is absent from its registry, not refused by a prompt." |

**Optional, if asked about failure:** crash recovery. Start `make worker`, queue a goal with
**Queue**, kill the worker (`Ctrl-C`) mid-run, start it again: the run resumes without redoing
finished tasks (ADR-010). Needs `AMOS_ASYNC_ENABLED=true`.

## When something fails

| What you see | What it is | Do |
|---|---|---|
| Red failed calls, a task drawn twice ("retry 1") | Gemini 503 "high demand"; AMOS retried | Say what the page shows: the retry, the deadline it shared, the task retry above it. If the run ends partial, open **History** and replay this morning's run |
| "Waited 34 s for Gemini's per-minute limit" | the client-side limiter | Normal on queries 7–8; keep talking |
| Banner: "Today's Gemini quota is used up" | the daily 429 | **History** replays every earlier run exactly; the demo continues from stored runs |
| Web pages read shows "0 characters" | a page that renders with JavaScript | Expected and honest; the researcher reads another |
| Searches return nothing | an upstream search engine blocking | System shows search status; use queries 1–5 and History |

## What each part is evidence for

| Claim | Where it is shown | Where it is explained |
|---|---|---|
| The model proposes, code disposes | bounds on System, options, budget | `docs/02-system-architecture.md`, ADR-009, ADR-019 |
| Every step is recorded | the lane graph and Activity, live and replayed | ADR-015, `docs/interview/run-record.md` |
| Answers cite sources | source cards, web pages read, citation check | ADR-020, `docs/interview/synthesis.md` |
| Honest failure | refusal, *Not sourced*, partial runs | `docs/16-evaluation.md` |
| Isolation | each user sees only their own runs, memory, uploads | ADR-013, `docs/interview/security.md` |

## Likely questions

Answered in `docs/interview/*.md` — eighteen documents, one per milestone. The ones a teacher is most
likely to reach: *why not a framework* (`foundation.md`), *why PostgreSQL for vectors*
(`rag.md`), *what stops a prompt injection* (`web-research.md`), *what happens when a worker dies*
(`reliability.md`), *how do you know it works* (`evaluation.md`).
