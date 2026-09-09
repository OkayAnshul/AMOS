# 16 — Evaluation

**Written at V1.0, with measured numbers.** Like `10-rag-architecture.md`, this was a stub until
there was something to measure — metrics chosen before there is anything to measure are metrics
chosen for how they sound.

## Results

Six end-to-end goals, run against the live system (`make eval`):

```
cases            6/6 passed (100%)

deterministic:
  completion     100%
  output valid   100%
  tool selection 100%
  answer content 100%
  refusal        1/1

LLM-judged (weaker evidence — same model family as the judged system):
  groundedness   1.00

cost             21252 tokens
```

Alongside the component measurements that already existed:

| What | Command | Result |
|---|---|---|
| End-to-end goals | `make eval` | 6/6 deterministic, groundedness 1.00 |
| Retrieval | `make retrieval` | recall@5 100%, recall@1 91.7%, MRR 0.958 |
| Routing | `make routing` | 10/10 |
| Tests | `make test` | 480 passing |

**What these numbers do not show:** six goals, twelve retrieval questions and ten routing cases,
all written by the person who built the system. That is enough to catch a regression and nowhere
near enough to characterise quality. Every one of these sets would need to be an order of
magnitude larger, and written by someone else, before "100%" meant much.

## Two kinds of evidence, never averaged

**Deterministic checks are preferred wherever a question can be settled by code**, and most can:
did the run complete, did it produce a valid response, did the expected tool run, does the answer
contain the required fact, was the right source cited, did it refuse when it should. All are facts
in the trace.

**LLM-as-judge is used for exactly one metric — groundedness** — because it is the only question
here that code cannot settle: *is this answer actually supported by the passages retrieved?*
String overlap does not answer it (a correct paraphrase shares few words; a fabrication can share
many).

They are reported **separately and never combined into a headline score**, because they are not
comparable evidence. The judge is the same model family as the system it judges: it shares
training data, shares blind spots, and is agreeable by construction. Three mitigations, none of
which make it strong evidence:

1. **The judge sees only what the run retrieved**, never the corpus — so it cannot find support the
   answer never had. Same reasoning as the V0.7 critic having no tools.
2. **Temperature 0**, so a score is at least reproducible.
3. **It must name the unsupported claims**, not merely score. A judge that has to point at specific
   text has less room to be vaguely agreeable, and the output is checkable by a human.

**Only deterministic checks gate CI.** A flaky judge must not block a release, and a threshold on a
subjective score invites tuning the threshold until it passes.

## The golden goal set

Six cases, chosen to cover failure modes rather than to be representative:

| Case | Checks |
|---|---|
| 17% of 2340 | uses the calculator rather than doing arithmetic in its head |
| 44% of 250 minus 10 | composes two steps |
| why pgvector over Qdrant | retrieves and cites the right ADR |
| the daily request quota | finds a number that exists in the corpus and nowhere in training data |
| **Kubernetes autoscaling policy** | **refuses** — the corpus says Kubernetes is not used |
| thundering herd | retrieves a mechanism described without the word used in the question |

Expectations are **loose and checkable** — tool used, substring present, source cited, refusal
detected — never exact-match on answer text. Asserting a model's exact wording tests the wording,
breaks on harmless rephrasing, and is how an LLM test suite becomes noise everyone learns to
ignore.

The set is deliberately small: every case costs real API calls, and **a suite that cannot be run is
not a gate**.

### The refusal case matters most

The corpus states Kubernetes is not used. Asked for its "autoscaling policy and configured replica
counts" — a question that presupposes both exist — a confident invented answer is the failure RAG
exists to prevent, and the one a user is least equipped to detect.

The system answers:

> *"Kubernetes is listed under the roadmap for beyond V1.0... Therefore, AMOS does not have a
> Kubernetes autoscaling policy or configured replica counts."*

## Two findings from running it

### A rate limit is not a quality failure

The first run scored 5/6, with the failure reading `ProviderRateLimitError`. That is an
infrastructure limit saying nothing about answer quality — scoring it as a failure makes the metric
report *"the system answered badly"* when it means *"we could not measure"*.

Cases are now classed **unmeasurable** rather than failed, excluded from the rates, reported
separately, and they do not fail the CI gate. Without that distinction the suite score is partly a
measurement of the free tier.

It also surfaced a fourth quota shape: `gemini-3.5-flash-lite` is **15 requests per minute**, where
`gemini-3.5-flash` is 20 per day. The suite paces itself 20s between cases as a result.

### The metric caught itself before it caught the system

The second run scored 5/6 with `refusal 0/1` — "should have refused but produced a confident
answer". The most important case in the suite, apparently failing.

Reading the actual output showed **the system had refused correctly**. The model had phrased the
refusal in a way the keyword detector did not cover, so a correct answer was scored as a fabricated
one.

**A crude metric does not merely under-measure — it manufactures false failures that are
indistinguishable from real ones until you go and read the output.** I came close to reporting a
brittle detector as a quality finding about the system.

The marker list is now much broader, every phrasing seen in a live run is pinned as a regression
test, and there is a matching test asserting that confident inventions are *still* caught — because
broadening a detector until everything looks like a refusal would pass the exact failure it exists
to catch.

**Refusal detection remains the least reliable metric here**, and is labelled as such in the code.

## CI

`.github/workflows/ci.yml` — listed as technical debt from V0.3 onward, closed here.

- Runs the suite **with** a database (Postgres service) and **without** one, because "most tests
  run with no database" is a claim, and an untested claim is a wish.
- Applies migrations in **both directions**. V0.4 shipped an irreversible migration that was caught
  by hand; this makes that automatic.
- **No API key.** Every test in the default suite uses `FakeProvider` (N-14), so CI passing without
  a key is that requirement being enforced rather than merely documented.
- **Does not run the evaluation suite.** It costs real quota; running it per push would exhaust the
  day's budget in a few commits. It is run deliberately and its numbers recorded here.

## Not done

- **Sets are too small and self-authored.** Six goals, twelve retrieval questions, ten routing
  cases, all written by the person who built the system
- **No regression tracking over time** — each run prints a number; nothing stores or trends them
- **No per-metric thresholds**; the gate is all-deterministic-checks-pass
- **No cost budget per run**, only token reporting
- **No adversarial cases** — no prompt injection, no deliberately misleading corpus entries
- **No human evaluation** to calibrate the judge, which is what would tell you how much the
  groundedness score is worth
