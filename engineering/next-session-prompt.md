# Next-session prompt

Paste this to start each session of the demo track (`docs/19-roadmap.md`, V1.5–V2.0). It does not
change between sessions; `current-state.md` says where the track stands.

```
Resume AMOS. Read docs/working-agreement.md, engineering/current-state.md, the latest
engineering/session-log.md entry, and "V1.5–V2.0 — The demo track" in docs/19-roadmap.md.

Goal: a demo-ready AMOS v2.0 on localhost that I show and explain to my teacher — research and
planning goals (web search, trip plans, comparisons) answered with citations and watched live on
a premium "mission control" web page.

This session:
1. Verify first: Postgres is up (I ran `make up`); run `make check`. Report any failure before
   anything else.
2. Plan the next unfinished milestone of the track: files, tests, ADR draft, acceptance criteria,
   estimate. Challenge the track where it is over- or under-engineered. Verify any free-tier limit
   on the provider's own page today; never from memory.
3. After I approve: build it on a feature branch. ADR before code, FakeProvider and recorded
   fixtures in tests, no network in tests. For the web page, do a real design pass and check it in
   a browser with screenshots.
4. Close per the session protocol: logs updated, the milestone's interview doc written,
   `make attribution-check`, merge, release, push.

Keep explanations short and link official docs.
```

## Before the first session (Anshul)

- `make up` in your own terminal (sessions cannot start containers while the old account
  switcher's environment scrub is active; see `current-state.md` → Environment).
- `make ingest` today and again tomorrow — the corpus rebuild needs two days of embedding quota.
- A free Groq API key in `.env` as `GROQ_API_KEY`, before V1.6.
