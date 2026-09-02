# Learning Log

Concepts, why they exist, where they appear in AMOS, and what to read. **Links, not essays** —
the reading is the point. Status uses the scale in `docs/20-learning-roadmap.md`:
**Recognise → Explain → Apply → Defend.**

---

# V0.1 — pre-read

Read before implementation starts.

## ASGI and async Python
**Problem it solves:** an LLM call blocks for seconds; a threaded server burns a thread per
waiting request. Async frees the thread while waiting on I/O.
**In AMOS:** every provider call, every endpoint.
**Read:** <https://fastapi.tiangolo.com/async/> · <https://docs.python.org/3/library/asyncio.html>
**Answer before moving on:**
- When does `async def` make something *slower* than `def`?
- What happens if a blocking call is made inside an async endpoint?
**Status:** ⬜ Recognise

## Pydantic v2 validation
**Problem it solves:** model output is text and could be anything. Validation is the boundary
where "probably JSON" becomes a typed object or a caught error.
**In AMOS:** `AgentResponse`, settings, later tool arguments and plans.
**Read:** <https://docs.pydantic.dev/latest/concepts/models/> ·
<https://docs.pydantic.dev/latest/concepts/json_schema/>
**Answer:**
- Difference between parsing and validation?
- Why validate output from an LLM that was *asked* for that schema?
**Status:** ⬜ Recognise

## Structured output and function calling
**Problem it solves:** free text cannot be dispatched on. Structured output constrains the model
to a schema.
**In AMOS:** V0.1 responses; V0.2 tool selection.
**Read:** <https://ai.google.dev/gemini-api/docs/structured-output> ·
<https://ai.google.dev/gemini-api/docs/function-calling>
**Answer:**
- Does structured output *guarantee* valid JSON? If so, why does AMOS still have a repair loop?
**Status:** ⬜ Recognise

## Protocols and dependency injection
**Problem it solves:** the agent must not know which provider it is calling — for swapping
vendors, and more immediately for testing without a network.
**In AMOS:** `LLMProvider`, later `VectorStore`.
**Read:** <https://docs.python.org/3/library/typing.html#typing.Protocol> ·
<https://peps.python.org/pep-0544/>
**Answer:**
- Protocol versus ABC — why does AMOS use a Protocol here?
- How does this make `FakeProvider` possible without touching agent code?
**Status:** ⬜ Recognise

## Test fixtures and fakes
**Problem it solves:** tests hitting a rate-limited, non-deterministic, paid API are slow,
flaky and eventually blocked.
**In AMOS:** `FakeProvider` in every unit and integration test (N-14).
**Read:** <https://docs.pytest.org/en/stable/how-to/fixtures.html> ·
<https://fastapi.tiangolo.com/tutorial/testing/>
**Answer:**
- Fake versus mock versus stub?
- How do you test "the model returned malformed JSON twice, then valid JSON"?
**Status:** ⬜ Recognise

## 12-factor configuration
**Problem it solves:** secrets in code get committed; environment-specific values in code make
environments un-swappable.
**In AMOS:** `pydantic-settings`, `.env`, `.env.example`.
**Read:** <https://12factor.net/config> ·
<https://docs.pydantic.dev/latest/concepts/pydantic_settings/>
**Answer:**
- Why should a missing API key fail at startup rather than on the first request?
**Status:** ⬜ Recognise

---

# Concepts introduced later

Named now so the direction is visible; read at their milestone.

| Concept | Milestone | Why it will matter |
|---|---|---|
| Prompt injection & allowlists | V0.2 | Tool output is untrusted input (N-12) |
| **Idempotency** | V0.3 | A retry that duplicates work is a bug, not a retry |
| State machines | V0.4 | Illegal transitions must raise, not warn |
| Exponential backoff + jitter | V0.4 | Retries without jitter synchronise and stampede |
| MRL truncation + re-normalisation | V0.5 | Skipping re-normalisation breaks cosine ranking *silently* |
| HNSW parameters | V0.5 | Recall/latency tradeoff, chosen with numbers |
| `SKIP LOCKED` | V0.8 | Crash-safe claiming without a broker |
| At-least-once delivery | V0.8 | Exactly-once is unavailable; know what to do instead |
| Span cardinality | V0.9 | High-cardinality attributes destroy tracing backends |

---

# Session-level lessons

## Verify, do not recall (Session 1)
Three assumed facts were checked and all three were wrong: the Gemini SDK package name (the
recalled one is deprecated), the current model IDs (a whole major version stale), and pgvector's
index dimension limit. The third would have surfaced only at V0.5 — after an entire corpus had
been embedded at an unindexable dimension.

**Rule adopted:** check current documentation before adopting or upgrading anything. Never write
a version number, model ID or API signature from memory.
**Status:** ✅ Defend
