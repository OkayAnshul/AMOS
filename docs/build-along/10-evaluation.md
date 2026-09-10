# V1.0 — Evaluation

**+1,023 lines.** The last milestone. Its job is making every quality claim in your repository
checkable by a command.

---

## Where you are

A system that plans, routes, uses tools, retrieves, remembers, runs asynchronously, survives
crashes and emits standard telemetry.

**And you have no idea whether it is any good.**

## The problem

You can say it retrieves well, routes correctly, refuses when it should. Those are assertions. An
interviewer's next question is *"how do you know?"* and the honest answer today is "I tried it a
few times."

---

## The decision that shapes everything here

**Split the question into what code can settle and what it cannot.**

**Most of it can.** Did the run complete, is the output valid, did the expected tool run, does the
answer contain the required fact, was the right source cited, did it refuse when the corpus had
nothing. These are **facts in the trace**, not opinions — reproducible, free, and impossible to talk
into agreeing.

**One thing cannot:** is the answer *supported by the passages retrieved*? String overlap does not
answer it — a correct paraphrase shares few words, a fabrication can share many.

That is the only place an LLM judge belongs.

### Why not judge everything? It is easier.

Because **the judge is the same model family as the system it judges.** It shares training data,
shares blind spots, and is agreeable by construction — asked "is this supported?", it tends toward
yes.

So a judged score is **weaker evidence**, and it is reported separately and **never averaged into a
headline number**. Combining them hides exactly the difference that matters. And only the
deterministic checks gate CI: a flaky judge must not block a release, and a threshold on a
subjective score invites tuning the threshold until it passes.

---

## Build order

### 1. `src/amos/evaluation/metrics.py` (~197 lines)

```python
@dataclass
class GoalCase:
    goal: str
    expects_tools: frozenset[str] = frozenset()      # SUBSET check, not equality
    expects_in_answer: tuple[str, ...] = ()          # case-insensitive substrings
    expects_citations: frozenset[str] = frozenset()
    expects_refusal: bool = False
    note: str = ""

@dataclass
class CaseScore:
    completed; output_valid; tools_correct; answer_correct
    citations_present; refused_correctly
    failures: list[str]
    unmeasurable: str | None = None      # ← see below
    @property
    def passed(self) -> bool: ...

def score_case(case, result, error=None) -> CaseScore:
    """Never raises."""
```

**Why expectations are loose and checkable, never exact-match on answer text** — asserting a
model's exact wording tests the wording. "397.8" rephrased as "approximately 397.8" is not a
regression, and a suite that fails on it becomes noise everyone learns to ignore.

**Why `expects_tools` is a subset check** — an extra retrieval is not wrong.

### 🔎 A rate limit is not a quality failure

The most important structural decision in this file, and the original got it wrong first.

The first run scored 5/6 with the failure reading `ProviderRateLimitError`. That is an
infrastructure limit — **scoring it as a failure makes the suite report "the system answered badly"
when it means "we could not measure".**

Classify those as **unmeasurable**: excluded from the rates, reported separately, and not failing
the gate. Without that distinction, part of your score is a measurement of the free tier.

```python
if any(marker in message for marker in ("RateLimit", "Timeout", "quota")):
    score.unmeasurable = message
else:
    score.failures.append(f"run failed: {message}")
```

---

### 2. `src/amos/evaluation/cases.py` — the golden set

Six cases, chosen to cover **failure modes**, not to be representative:

| Case | Checks |
|---|---|
| 17% of 2340 | uses the calculator rather than doing arithmetic in its head |
| 44% of 250 minus 10 | composes two steps |
| why pgvector over Qdrant | retrieves and cites the right document |
| the daily request quota | finds a number in your corpus that is nowhere in training data |
| **a policy your docs say does not exist** | **refuses** |
| thundering herd | retrieves a mechanism described without the word in the question |

**Keep it small.** Every case costs real API calls, and **a suite that cannot be run is not a gate.**

**The refusal case matters most.** Ask for the "autoscaling policy and configured replica counts" of
something your documentation says you do not use — a question presupposing both exist. A confident
invented answer there is the failure RAG exists to prevent, and **the one a user is least equipped
to detect**: fluent, specific, and wrong.

---

### 3. `src/amos/evaluation/judge.py` (~120 lines)

```python
class GroundednessVerdict(BaseModel):
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    judged: bool = True                    # ← NOT a sentinel in `score`
    unsupported_claims: list[str] = []
    reasoning: str = ""

class GroundednessJudge:
    async def score(self, goal, answer, evidence) -> GroundednessVerdict:
        """Never raises. Failure returns judged=False, EXCLUDED from aggregates."""
```

⚠️ **Do not use `score = -1` as a "judge failed" sentinel.**

<details><summary>Two reasons, and the first is immediate</summary>

The field's own `ge=0.0` rejects it outright.

And it would be poor design even if it constructed: **a magic number in a numeric field gets
averaged by accident.** An unjudged case must be *excluded* from the mean, and that is easier to get
right when it is a different field.
</details>

**Three mitigations, none of which make it strong evidence:**

1. **The judge sees only what the run retrieved**, never the corpus — so it cannot find support the
   answer never had. Same reasoning as the critic having no tools.
2. **Temperature 0**, so a score is at least reproducible.
3. **It must name the unsupported claims**, not merely score. A judge that has to point at specific
   text has less room to be vaguely agreeable, and the output is checkable by a human.

---

### 4. `src/amos/evaluation/harness.py` (~185 lines)

```python
@dataclass
class SuiteResult:
    scores: list[CaseScore]
    groundedness: list[tuple[float, bool]]     # (score, judged)
    @property
    def measured(self) -> list[CaseScore]: ...  # excludes unmeasurable
    @property
    def mean_groundedness(self) -> float | None:
        """NONE, not 0.0, when nothing could be judged."""
    def summary(self) -> str:
        """Deterministic and judged sections SEPARATE, with the caveat attached."""
```

**Why `None` rather than `0.0`** — returning 0.0 reports a perfect-but-unjudged suite as maximally
ungrounded, which is worse than reporting no number.

**Pace the suite.** `gemini-3.5-flash-lite` is 15 requests **per minute** (a different quota shape
again — `flash` is 20 per day). Firing cases back to back guarantees a 429 partway through, which
then looks like a quality failure unless you have built the `unmeasurable` distinction above.

**The caveat travels with the number.** Print *"LLM-judged (weaker evidence — same model family as
the judged system)"* in the summary itself, not in a document nobody opens.

---

### 5. CI — the debt you have been carrying since V0.3

```yaml
jobs:
  test:      # with a Postgres service
  no-database:  # WITHOUT one
```

Three things it must do:

**Run the suite with and without a database.** "Most tests run with no database" is a claim, and an
untested claim is a wish.

**Apply migrations in both directions.** You shipped an irreversible one at V0.4 and caught it by
hand. Make that automatic.

**Run with no API key.** Every test uses `FakeProvider`, so CI passing without a key turns "tests
never touch the network" from documentation into something *enforced* — if someone adds a test that
calls the real API, CI fails immediately rather than quietly consuming quota.

**And do not run the evaluation suite in CI.** It costs real quota; running it per push would
exhaust the day's budget in a few commits, and a gate that fails for reasons unrelated to the change
is worse than no gate.

---

## Checkpoint

```bash
make check        # lint + types + the full suite
make eval         # the golden set — costs real quota, run deliberately
```

Expected shape:
```
cases            6/6 passed (100%)

deterministic:
  completion     100%
  tool selection 100%
  refusal        1/1

LLM-judged (weaker evidence — same model family as the judged system):
  groundedness   1.00
```

### 🔎 Expect the metric to be wrong before the system is

This happened twice in the original, and the second nearly became a false finding.

The refusal case scored **0/1** — "should have refused but produced a confident answer". The most
important case in the suite, apparently failing in the worst possible way.

**Reading the actual output showed the system had refused correctly.** It phrased the refusal in
wording the keyword detector did not cover.

> **A crude metric does not merely under-measure. It manufactures false failures that are
> indistinguishable from real ones until you go and read the output.**

The fix has two halves and the second matters as much as the first: broaden the markers, **and add
a test asserting confident inventions are still caught** — because broadening a detector until
everything matches passes the exact failure it exists to catch.

That is now the third time a metric was wrong before the system was, after V0.5's golden set and the
allowlist bug. **When a number surprises you, read the output before you write it up.**

---

## You are done

Every quality claim is now reproducible:

```bash
make eval         # end-to-end goals
make retrieval    # recall@k and MRR
make routing      # agent routing accuracy
make test         # the suite
```

**And the caveat that must accompany all of them:** these sets are small and were written by the
person who built the system. They are a **regression gate, not a characterisation of quality**. The
most valuable next engineering work is not a feature — it is enlarging them and having somebody else
write them.

---

## What is actually left

Not code. **The interview documents.**

You have built the thing. `docs/interview/*.md` is ten documents of questions about it, and
answering them unaided is the gate this whole project was structured around:

> *"A milestone is not complete until you can answer that module's questions unaided."*

That is the half that turns "I built this" into "I built this and can defend every decision in it".
It is also the only half nobody can do for you.
