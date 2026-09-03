# Interview — Foundation (V0.1)

Questions a skeptical interviewer asks about this milestone, and the answers the code supports.
**The advance gate: V0.2 does not begin until these can be answered unaided.**

---

### Why is `LLMProvider` a `Protocol` and not an abstract base class?

Structural typing. A test double satisfies the interface without importing or inheriting from
anything, so `FakeProvider` and `GeminiProvider` are interchangeable without a shared base.
An ABC would force every implementation to inherit from AMOS's class — coupling with no benefit,
since nothing is being shared except a shape.

*Follow-up — "isn't that speculative generality with one provider?"* No, and this is the load-bearing
part: **the abstraction is paid for by V0.1's own tests.** Tests must not touch the network
(free tier is ~15 RPM, non-deterministic), so a fake provider is required regardless. Multi-provider
support is a side effect of a testing requirement, not anticipatory design.

### Gemini enforces the response schema. Why is there still a repair loop?

Because schema enforcement makes valid output *likely*, not *certain*. It still fails when the
response is truncated mid-JSON (`finish_reason: MAX_TOKENS`), when generation halts for safety
or recitation, or when values satisfy JSON Schema but violate a Pydantic constraint the provider
did not enforce.

The rule is N-1: unvalidated model output never becomes control flow. The loop is defensive.

*Honest caveat:* against the real API so far, `repair_count` has been 0 every time. Whether the
loop is load-bearing or dead code is an open question — `repair_count` is recorded on every
result precisely so the answer comes from data. Queued in `engineering/experiments-log.md`.

### Why is a provider timeout not retried by the repair loop?

A timeout is a transport failure; the repair loop fixes *validation* failures. Retrying a
timeout there would burn the repair budget on something re-prompting cannot fix, and would hide
a real infrastructure problem behind what looks like a model problem. `ProviderTimeoutError`
propagates and maps to `504`. Tested: `test_provider_error_is_not_swallowed_by_repair_loop`.

### What happens on the third consecutive malformed response?

`OutputValidationError` with `attempts=3` and the last validation error attached; the API returns
`502`. Every attempt — including the failed ones — is in `llm_calls` with its token cost, so the
wasted spend is visible rather than silently absorbed.

### Why does a missing API key fail at startup rather than on first request?

Fail fast. A config error surfacing an hour later, on a user's request, is far harder to
diagnose than one at boot. `Settings.require_api_key()` raises `ConfigurationError` during app
construction, and the message names the fix (copy `.env.example`, get a key from AI Studio).

### Why does V0.1 have no database?

Nothing needs to survive a restart. A goal comes in, an answer goes out. Adding Postgres would
mean Docker, a schema, migrations and connection lifecycle — real complexity against no
requirement — and would break "every milestone is runnable" by requiring infrastructure to
demo. ADR-006. Persistence arrives at V0.3, where durable runs *are* the milestone.

### How does V0.1 avoid becoming a rewrite at V0.3?

Four seams, each justified by a V0.1 need:

| Seam | V0.1 justification | Absorbs later |
|---|---|---|
| `LLMProvider` protocol | tests need a fake | other providers |
| `AgentResult` + `LLMCallRecord` | structured logging needs the fields | become `steps` / `llm_calls` rows |
| validate-and-repair loop | malformed JSON is a real failure | tool-arg validation (V0.2), plan validation (V0.4) |
| request id in every log line | debugging needs it | OTel `trace_id` (V0.9) |

`LLMCallRecord`'s fields are deliberately the `llm_calls` columns from `docs/05-data-model.md`,
so V0.3 is a serialisation change rather than a redesign.

### Why return `llm_calls` and `repair_count` to the client?

Cost and reliability should not be invisible to whoever is paying for them. A caller can see
token spend and whether the model had to be corrected. It is also the smallest possible version
of the V0.3 trace endpoint — the same instinct, one milestone early.

### Why `502` for `OutputValidationError` rather than `500`?

The request was valid and AMOS behaved correctly; an upstream dependency failed to produce
usable output. That is a bad gateway. A `500` would suggest an AMOS defect and send someone
debugging the wrong system.

### Fake vs mock — which is `FakeProvider`, and why does it matter?

A **fake**: a working implementation with real behaviour (it parses, it counts tokens, it
advances a script), just not a real one. A mock asserts on calls. The distinction matters because
the fake exercises the actual async code path and actual validation logic — a mock returning a
canned object would test almost nothing about the repair loop.

`test_fake_provider.py` tests the fake itself, because if test infrastructure lies, every test
above it lies too.

---

## What V0.1 does NOT demonstrate

Say this before an interviewer has to ask:

- No persistence, no concurrency, no distributed anything
- **Not** "multi-agent" — there is one agent
- **Not** RAG — no retrieval exists
- "Grounded" here means *states assumptions and admits uncertainty*, not *cites retrieved
  sources*. That meaning arrives at V0.5.
