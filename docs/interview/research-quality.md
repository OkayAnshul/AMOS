# Interview — Research quality and a second provider (V1.11)

**Advance gate: V2.0 does not begin until these can be answered unaided.**

V1.11 made the Goa plan specific instead of generic, and kept runs alive when Gemini was busy.
ADR-021 (fallback chain), ADR-022 (quoted findings), ADR-023 (research that reads).

---

### Why was the Goa plan generic, measured rather than guessed?

The run record showed five causes: the preset asked for "typical" costs; a researcher read a
20,000-character page and handed on two sentences; booking sites built by scripts read as empty;
researchers searched eight times and read twice; and one failed research task skipped the budget
and the writer, so no plan was produced at all. Each fix below answers one of these.

### What is a finding, and what does code check about it?

A researcher returns `{fact, quote, source}`: the fact with its figure, the **exact words** on the
page that state it, and the URL. When the task succeeds, the executor looks for every quote in the
text that task actually read — pages, search snippets, document passages — after normalising case,
spacing, `Rs.`/`INR`/`₹` and thousands separators, and checks every number in the fact is in the
quote. Checked findings travel to the analyst and writer as facts; the rest travel under "not
confirmed". It proves the words are on that page; it cannot prove the page is right.

### Why a separate reader step, when the researcher already returns findings?

A small model extracts lazily at the end of a long tool loop: live runs gave 0 to 3 findings from
pages listing a dozen priced items. The reader is one focused call with one job — the task, plus
exactly the text the task read — and its findings join the researcher's. A reader that invents a
quote gains nothing: the check marks it.

### Why cap searches in code when the prompt already said "at most two"?

Because the prompt was ignored (8 searches, 2 pages). A rule the model can break is a suggestion.
The fourth `web_search` in one task attempt is refused with an instruction to read what it has.
Off-topic results (a dictionary, a shop) are dropped before the model sees them.

### Why cut pages to passages, and what does the cut lose?

A full page made Gemini time out and Groq refuse the request (its 8,000 tokens a minute). With
`looking_for`, a long page becomes its most relevant blocks — scored by the words asked for, plus
figures and currency — with their neighbours, since a name often sits one block above its price.
It can cut the one sentence that mattered; the output says it is an excerpt and how long the page
was. Every later check sees exactly the excerpt the agent saw.

### When does a task run with some of its inputs missing?

When it **combines** several results (two or more dependencies), all of them have finished, and at
least one succeeded. It is told which inputs are missing and why. A task built on one result still
needs it. The run stays `PARTIALLY_COMPLETED`; the plan says what is missing instead of not existing.

### How does the fallback chain work, and what can it not do?

Gemini flash-lite, then Gemma 4 (its own quota, same infrastructure), then Groq `gpt-oss-120b`
(other infrastructure). A 503, a rate limit, a timeout or a schema the model could not fill moves a
call on — inside the **same deadline**, so ADR-009's bounds hold. It cannot hand a Gemini tool loop
to a *different* Gemini model: Gemini 3 rejects function calls in its history without its own
signature. Groq accepts any history, so it can always take over.

### Why does the writer prefer Groq, and why not everything?

The writer and planner decide a run's quality and call no tools, so they get the strongest model.
Groq's free tier allows 8,000 tokens a minute — about one research loop — so it cannot be the
workhorse. Requests are trimmed to fit it, and a short per-minute 429 is waited out once.

### Why strict structured output on Groq?

Best-effort mode left out a required field once, and the writer's whole JSON became the answer
text. Strict mode constrains decoding to the schema; it needs every property required and no extra
properties, so AMOS derives that schema from the Pydantic one. When strict mode cannot fill a schema
it answers 400 — treated as "try the next model", not as a failure.

### Why not use several free keys to get more quota?

Google's API terms forbid working around documented limits, and keys in one project share one quota
anyway. A second *provider* with its own free tier, used as a fallback, is within the rules.

**Read:** <https://console.groq.com/docs/structured-outputs> · <https://console.groq.com/docs/rate-limits> ·
<https://aistudio.google.com/docs/thought-signatures>
