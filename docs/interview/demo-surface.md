# Interview — The demo page (V1.8)

**Advance gate: V1.9 does not begin until these can be answered unaided.**

V1.8 is the mission-control page at `/`: a live lane graph of each run, an inspector for every
call and decision, and every other part of the system around it. ADR-017.

---

### Why plain HTML, CSS and JavaScript, with no framework?

A build step is a second toolchain to install, upgrade and explain, for a page with one author and
one audience. The page's main job — redrawing a run about once a second — is client-side work,
which rules out server templates. Plain ES modules (`<script type="module">`) need nothing but a
browser and stay readable in view-source. The cost is writing each control by hand.

### How does the page show a run "live" if the API has no streaming?

It polls `GET /v1/runs/{id}` about every 800 ms and redraws from the whole stored record — the
same record a finished run reads back. Since V1.7 every call and event is written as it happens,
so the record *is* the live state. It polls the whole run, not a cursor, because a sequence orders
inserts, not commits (ADR-015). Hidden tabs slow to every 4 s.

### A synchronous run returns nothing until it finishes. How does the page watch one?

The page chooses the run's **idempotency key** itself, sends the goal, and while the POST waits it
polls `GET /v1/runs?idempotency_key=…`. The run row is committed *before* execution, so it appears
within half a second, and from then on the page watches it like any other run.

### What is drawn, and from what?

One lane per agent, from `task.routed` events. A capsule per **task attempt**, from `task.started`
to its finishing event — so a retry is a second capsule, not a hidden fact. A tick per call, from
each row's finish time minus its latency. Dependency curves from the plan's `depends_on`. Critic
reviews on their own lane, with their verdict. Delegations on the delegate's lane, dashed. Nothing
is drawn that is not a stored row.

### Model output is untrusted. How does the page stop it running as code?

Three layers. **Nothing model-written reaches `innerHTML`**: every text node is set with
`textContent`, and answers go through a small markdown renderer that *builds* DOM nodes for bold,
lists and tables and never parses HTML; a unit test fails if any script uses `innerHTML`, `eval`
or `new Function`. **The page refuses inline script** through its Content-Security-Policy. And a
browser test puts `<img onerror=…>` in an answer and asserts it appears as text and creates no
element — the test was shown to fail when a paragraph was rendered with `innerHTML`.

### What does the Content-Security-Policy do here, concretely?

`default-src 'self'` and friends: scripts, styles, fonts and API calls may only come from this
origin; no inline `<script>`, no `style=` attributes, no frames, no plugins, no form posts. The
typeface is a file in the repository, so the page makes **no third-party request**, and a test
fails if a page file names an outside URL. Styles set from JavaScript go through the CSSOM
(`el.style.x = …`), which CSP permits.

### Where is the API key kept?

In `sessionStorage` — gone when the tab closes — unless the presenter ticks "remember on this
device", which also writes `localStorage`. It is sent as `Authorization: Bearer`. A 401 sends the
page back to the connect screen. Storing a key in the browser is acceptable for one presenter on
localhost, not for a shared deployment.

### How are per-run options shown, and why are some greyed out?

Each switch starts on if the server has the feature on. Turning one off sends `options: {name:
false}`. A switch for a feature the server has off is disabled and says "Off on this server",
because ADR-016 lets a run switch features off, never on.

### What does the quota meter count, and what does it miss?

Model calls stored since midnight Pacific against the configured daily limit, plus the last minute
against the per-minute limit. It misses calls outside runs (the eval, the trials) and every
embedding call — so it is a floor, and the page says so beside it.

### How was the page checked, beyond unit tests?

In a real browser: Playwright driving the installed Chrome against the live API and real Gemini —
demo queries run from the page, screenshots in light, dark, narrow and presenter mode, reviewed and
fixed. That review found three things no unit test had: the run shortcut worked only inside the
goal box, short capsules showed fragments like "Co…", and the inspector stayed open on other pages.

### Why polling instead of server-sent events?

One presenter, one page, a run of a few hundred rows: polling the whole record is simple, needs
nothing new in the server, and cannot get out of order. SSE earns its place with many viewers or
very long runs — ADR-017's reconsider-if.

---

## What this milestone does *not* claim

- Not a multi-user product: one presenter on localhost.
- Not streaming; not a full cost record (see the meter's note).
- Prompts and replies are not shown — they are not stored. The record keeps who called, why,
  and what it cost.
- Not tested in browsers other than Chrome.
