# Interview — Web research and the run budget (V1.9)

**Advance gate: V1.10 does not begin until these can be answered unaided.**

V1.9 lets the researcher answer questions about the world with web citations — without giving a
model an open network — and caps what any one run may spend. ADR-018 and ADR-019.

---

### Why SearXNG, and not a search API?

There was no keyed search provider available, and Gemini 3's free tier allows zero grounded
searches. SearXNG is a metasearch engine you run yourself: no key, no account, a container beside
Postgres. It is **pinned** to a build two weeks old on the day it was chosen, bound to
`127.0.0.1`, and configured to inherit its defaults except three things — JSON output on (without
it the API answers 403), the limiter off, and the secret from the environment. All read from the
project's own documentation on the day.

### How good is it?

Measured, not assumed: ten fixed queries, ten answered, twenty results each in 0.6–1.2 s, about 45
of the top 50 on topic. The weakness, stated in the log: **every result came from one upstream
engine**; three others were unresponsive. If that one starts refusing, `web_search` returns
nothing and the agent says it could not find the answer — a visible failure, not a silent one.

### What stops `fetch_page` from being an open network?

**Provenance.** It reads a URL only if `web_search` returned that exact URL earlier in the same
run. The model can read what search found, and nothing else — which is the whole research workflow
and no more. On top of that, the shared SSRF guard: `https` only, every address the name resolves
to must be public, redirects followed only within the same site with each hop re-checked, 500 KB
and 20 000 characters at most, and only HTML or plain text.

### Where does the provenance come from?

From the **stored run record** (V1.7): this run's own `web_search` rows. Tool rows are written as
each call finishes, so a search's results are stored before the model's next turn can ask to fetch
one. Nothing in memory to leak between runs, nothing lost on a restart — and with no database,
there is no provenance, so the tool fetches nothing. It fails **closed**.

### The old SSRF check had a gap. What was it?

`http_get` used `gethostbyname`, which returns one IPv4 address. A hostname with a public A record
and an internal AAAA record would have passed. The shared guard uses `getaddrinfo` and checks
**every** address, IPv6 included — private, loopback, link-local, reserved, multicast and
unspecified are all refused. Extracting it was the occasion: two copies of a guard drift, and the
one that drifts is the hole.

### A web page tells the model to ignore its instructions. What happens?

The page's text is returned — the model has to read a page to cite it — but inside an output marked
`untrusted: true` whose instruction says it is material, not orders, and the researcher's own
instruction says the same. That is **mitigation, not prevention**: no prompt reliably stops a model
being influenced. What *does* hold regardless is structural: the researcher has no write tools,
`remember_fact` is the only state it can change, every call is recorded, and the critic reviews
the answer. A fixture page with exactly this injection is in the tests.

### What is the run budget, and why is it code?

Forty model calls, forty tool calls and eight page reads per run, by default. Model calls are
charged in `RecordingProvider` — every real attempt, retries included, because each spends quota.
Tool calls and page reads are charged in `_invoke`. A budget is a guarantee, and guarantees are
code: a prompt asking the model to be frugal is a hope.

### What happens when a run hits it?

It depends on what hits it, and each choice is deliberate:
- **A tool call** past the limit is *denied with a reason* the model can read, so it answers with
  what it has.
- **A model call** past the limit raises `BudgetExhaustedError`, which the executor **does not
  retry** — the budget does not refill. The task fails permanently; others that finished stand, and
  the run ends partial with the reason in its record.
- **Synthesis is exempt**, so a run that spent its budget can still combine what its tasks found.

Each limit, when first reached, is recorded as a `budget.exhausted` event and shown on the page.

### What did the first live web runs find?

Two bugs, neither visible in unit tests. The critic was judging web answers **blind**: its evidence
for any non-retrieval tool was the first 600 characters of the output dictionary — the URL and the
instruction, not the page. And a researcher that would not stop searching **failed three times
over**: each attempt hit the five-round cap and raised, and the executor retried the same failure.
Nineteen model calls, no answer.

### How was the second one fixed?

At the cap, the agent now makes **one** more call with no tools offered, asking for an answer from
what it gathered, with a caveat saying so. Still bounded — exactly one extra call — and a model
that asks for a tool even then fails as before. The web instruction also got a stopping rule: two
searches and three pages, then answer. The same question then completed in nine calls.

### Wasn't a fallback like that deleted once?

Yes — `_finalise()`, at V0.7, because it had never fired across four milestones. That was right for
the workloads then. "It never fired" is evidence about the workloads so far; web research is the
first that loops, and the fallback came back with a test that proves it fires.

---

## What this milestone does *not* claim

- Not injection-proof. The defences are structural (no write tools, recording, review), not a
  prompt that a model always obeys.
- Not robust search: one upstream engine served every measured result.
- No JavaScript-rendered pages — a page that builds its text in the browser reads as empty, and the
  page says "0 characters".
- The budget's numbers are reasoned from a few live runs, not measured across many.
