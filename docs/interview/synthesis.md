# Interview — Complex goals: the writer (V1.10)

**Advance gate: V2.0 does not begin until these can be answered unaided.**

V1.10 turns "plan a trip" and "compare X and Y" into structured, cited deliverables. ADR-020.

---

### What does the writer do, and why does it have no tools?

It composes the deliverable — itinerary, comparison table, recommendation — from the results of
the tasks it depends on. With no tools it cannot fetch, compute or recall, so **every fact in a
deliverable traces to an earlier task's record**. It cannot delegate and cannot be delegated to:
it writes; it does not commission work.

### How does a plan for a complex goal look?

The planner learned one shape: one research task per topic, then a task for the arithmetic, then
one writing task that depends on all of them. Independent research tasks run **in parallel**.
When the final task is the writer's, its deliverable *is* the run's answer — the synthesis call
would only flatten a structured document into a paragraph.

### What is "Not sourced" for?

A good plan contains advice no source gave — "carry cash for small vendors". Banning it makes the
plan worse; presenting it like a researched fact launders it. So suggestions go under a heading
**Not sourced**, the page draws it apart in a dashed box, and the critic does not flag what is in
it — but does flag anything unsourced outside it.

### How are the writer's citations checked?

**In code.** Every `(source: …)` marker and URL in the deliverable is compared with the run's source
ledger, read from the stored record: search results, pages read, retrieval citations, and the
run's own task ids (a computed figure cites its task, `(source: t4)`). A citation the run never saw
becomes a caveat and drops confidence to low. It proves a source was **seen**, not that it **says**
the claim — that stays the critic's job, and the doc says so.

### Why did the first live plan cite task descriptions instead of URLs?

Because the writer was never given the URLs. The executor passed each upstream task's *answer*, not
the sources behind it. Now each upstream result carries its task id and "Sources it read". The
citation check had flagged every one of those task-description citations — the check worked; the
material was incomplete.

### Three research tasks in parallel — what broke?

Gemini's **15 requests a minute**. Three researchers with their routers, pages and critics passed it
together; one task's calls got 429 and were retried after half a second, then lost, and with it the
tasks that depended on it. The fix is a **client-side rate limiter**: a process-wide sliding window,
one slot under the quota, so calls *wait* for a slot instead of being refused. Waits over a second
are recorded and shown ("waited 34 s for the per-minute limit").

### Why does the limiter sit where it does in the provider stack?

`Retry → RateLimit → Record → Gemini`. Inside the retry, so a retried attempt waits for its own slot
(it spends quota). Outside the recorder, so a recorded latency is the provider's, not the queue's.
Waiting is not part of a call's own timeout, but it is inside the task timeout, which still bounds
the whole (ADR-009). It is per process: the API and a worker each have one — stated, not hidden.

### What happened when search came back empty?

The measured weakness arrived: SearXNG's one working upstream engine was suspended for "too many
requests" after a day of testing, and six of seven searches returned nothing. AMOS said so — no
invented prices. The fix was configuration, by probing each engine from this machine: Bing and
PrivacyWall answer, Google and Qwant deny access, DuckDuckGo shows a CAPTCHA. With two working
engines enabled, every query returned 19–20 results while the third was still suspended.

### What if Gemini is overloaded during the demo?

Then a run ends partial or failed, and the page shows exactly why — red failed calls, retries as
second capsules. And **History replays any earlier run exactly as it happened**, from stored rows,
so a good run recorded beforehand is the fallback.

---

## What this milestone does *not* claim

- That a cited source *says* what is claimed — only that it was seen.
- That complex goals succeed reliably on a free tier: they need 20–50 model calls and minutes of
  wall time, and depend on Gemini's load and the search engines' patience.
- That the rate limiter covers several processes at once.
