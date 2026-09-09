# Interview — Evaluation (V1.0)

The final milestone. Its job is making every quality claim in the repo checkable by a command.

---

### How do you measure whether an agent system is any good?

Split the question into what code can settle and what it cannot.

**Most of it can.** Did the run complete, did it produce a valid structured response, did the
expected tool actually run, does the answer contain the required fact, was the right source cited,
did it refuse when the corpus had nothing — all are facts in the trace, not opinions. They are
reproducible, free, and cannot be talked into agreeing.

**One thing cannot:** is the answer *supported by the passages retrieved*? String overlap does not
answer it — a correct paraphrase shares few words, a fabrication can share many. That is the only
place an LLM judge is used.

### Why not just use LLM-as-judge for everything? It's easier.

Because the judge is the same model family as the system it judges. It shares training data,
shares blind spots, and is agreeable by construction — a model asked "is this supported?" tends
toward yes.

So a judged score is **weaker evidence** than a deterministic check, and AMOS reports them
separately and never averages them into one headline number. Combining them hides exactly the
difference that matters.

Three mitigations, none of which make it strong evidence: the judge sees only what the run
retrieved (so it cannot find support the answer never had — same reasoning as the V0.7 critic
having no tools), temperature 0 for reproducibility, and it must *name* the unsupported claims
rather than only scoring, because a judge that has to point at specific text has less room to be
vaguely agreeable.

And only the deterministic checks gate CI. A flaky judge must not block a release, and a threshold
on a subjective score invites tuning the threshold until it passes.

### Your golden set is six cases. Isn't that far too small?

Yes, and it is labelled as such everywhere the numbers appear.

It is a **regression gate, not a characterisation of quality**. Six goals catch "the calculator
stopped being called" or "refusals broke"; they say almost nothing about behaviour in general. The
same caveat applies to twelve retrieval questions and ten routing cases — and all of them were
written by the person who built the system, which is its own bias.

The constraint driving the size is real: every case costs API calls against a free tier, and **a
suite that cannot be run is not a gate**. The honest framing is that the numbers are floors, not
descriptions.

### Why don't you assert the expected answer text?

Because that tests the wording. A model rephrasing "397.8" as "approximately 397.8" is not a
regression, and a suite that fails on it is a suite people learn to ignore.

Expectations are loose and checkable: which tool ran, which substring is present, which source was
cited, whether it refused. Each is verifiable by a script and stable across harmless rephrasing.

### What is the most important case in your suite, and why?

The Kubernetes one. The corpus states Kubernetes is not used; the goal asks for its "autoscaling
policy and configured replica counts" — a question presupposing both exist.

A confident invented answer there is **the failure RAG exists to prevent**, and the one a user is
least equipped to detect: it is fluent, specific, and wrong. Every other failure mode is more
visible than this one.

### Tell me about a time your evaluation gave you the wrong answer.

Twice, in the first two runs, and both were the metric rather than the system.

**First:** a case failed with `ProviderRateLimitError`. That is an infrastructure limit and says
nothing about answer quality — scoring it as a failure makes the suite report "the system answered
badly" when it means "we could not measure". Cases are now classed *unmeasurable*, excluded from
the rates and from the gate. Without that, part of the score is a measurement of the free tier.

**Second, and worse:** the refusal case scored 0/1 — "should have refused but produced a confident
answer". The most important case in the suite, apparently failing in the worst possible way.

Reading the actual output showed **the system had refused correctly**. It phrased the refusal in
wording my keyword detector did not cover, so a correct answer was scored as a fabrication.

**A crude metric does not merely under-measure — it manufactures false failures indistinguishable
from real ones until you read the output.** I came very close to writing up a brittle detector as a
quality finding about the system.

The fix: a much broader marker list, every live phrasing pinned as a regression test, and a
matching test asserting confident inventions are *still* caught — because broadening a detector
until everything looks like a refusal would pass the exact failure it exists to catch. Refusal
detection is labelled the least reliable metric in the suite.

### Why doesn't CI run the evaluation suite?

It costs real API calls against a 20-request/day quota. Running it per push would exhaust the
day's budget in a few commits, and a gate that cannot run is worse than no gate — it fails for
reasons unrelated to the change.

CI runs the 480 deterministic tests, applies migrations in both directions, and runs the suite both
with and without a database. The evaluation suite is run deliberately, by a person, and its numbers
are recorded in `docs/16-evaluation.md`.

### Your CI has no API key. Deliberate?

Yes, and it enforces a requirement rather than documenting one. Every test in the default suite
uses `FakeProvider` (requirement N-14, since V0.1). CI passing with no key is proof of that — if
someone adds a test that calls the real API, CI fails immediately rather than quietly consuming
quota.

---

## What V1.0 does NOT demonstrate

- **Six goals, twelve retrieval questions, ten routing cases** — all self-authored. Enough for
  regression, nowhere near characterisation
- **No regression tracking over time** — each run prints a number; nothing stores or trends them
- **No adversarial cases** — no prompt injection, no deliberately misleading corpus entries
- **No human evaluation to calibrate the judge**, which is what would tell you what a groundedness
  score of 1.00 is actually worth
- **No per-metric thresholds** — the gate is all-deterministic-checks-pass
- No cost budget per run, only token reporting
