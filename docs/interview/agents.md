# Interview — Tools & Agents (V0.2)

**Advance gate: V0.3 does not begin until these can be answered unaided.**

---

### `LLMProvider` is a Protocol but `Tool` is an ABC. Why the inconsistency?

It isn't one — they differ in what they share. Providers share a *shape*: nothing is inherited,
so structural typing is right, and a test fake satisfies it without importing anything.

Tools share *behaviour*. Every tool must validate its arguments and honour its timeout. That
behaviour lives in the concrete `Tool.execute()`; subclasses implement only `_run()`. So **a
tool cannot opt out of validation or timeouts** — it is given no opportunity to. If each author
had to remember, one eventually wouldn't, and that tool would be the vulnerability.

Rule of thumb: shared shape → Protocol. Shared behaviour that must not be skipped → ABC.

### What stops the tool loop from running forever?

`max_iterations`, checked by the `for` loop in `ToolUsingAgent.run`. When it is exhausted the
agent raises `ToolLoopExhaustedError` → HTTP 502.

The important part is *where* the guarantee lives. The system prompt also asks the model to stop
when it has enough information — that is a request. The loop counter is the guarantee. An
unbounded loop is an unbounded bill and a request that never returns.

Tested by `test_loop_cap_is_enforced`, which asserts the provider was called exactly 3 times —
that the agent stops *calling out*, not merely that it eventually errors.

### The model asks for a tool that doesn't exist. What happens?

It gets a `ToolOutcome` with status `not_found`, whose error message **lists the tools that do
exist**, and the loop continues. It is not an exception.

A hallucinated tool name is expected behaviour, not exceptional — the model is told what exists
and sometimes invents something else. Raising would turn a recoverable mistake into a failed
request. The agent still cannot be trapped: the failed attempt consumed an iteration, so the cap
converges regardless.

Same reasoning for invalid arguments: rejected before execution, error fed back, model corrects.

### Why validate arguments the model generated from a schema you gave it?

Because "we asked for X" is not "we received X". More importantly, the validation is the
security boundary — `read_file`'s path and `http_get`'s URL are model-supplied and hostile by
construction. Requirement N-1: unvalidated model output never becomes control flow.

The schema is generated *from* the same Pydantic model that validates, via
`input_schema.model_json_schema()`, so the declaration and the validator cannot drift apart.

### How does an allowlist defend against prompt injection?

Partially, and it is worth being precise about the limit.

The system prompt tells the model to treat tool output as data, not instructions. That is not a
control — a persuasive payload can override any instruction. What actually holds:

- The registry is fixed at startup, so "you may now use `delete_file`" changes nothing
- Each tool enforces its own boundary regardless of why it was called
- `WRITE`/`DESTRUCTIVE` tools cannot be registered at all

`test_prompt_injection_in_tool_output_does_not_change_permissions` asserts the interesting
version: **assume the model is fully compromised** and emits the attacker's call — the system
still refuses it.

Honest residual risk: injection can still make the model give a *wrong answer* using permitted
tools. AMOS constrains what can be done, not what can be said.

### Why does `read_file` resolve the path before checking it, rather than after?

Because resolution is what reveals a symlink. A symlink named `innocent.md` inside the sandbox,
pointing at `/etc/passwd`, contains no `..`, no absolute path, nothing a string filter would
catch. `Path.resolve()` follows it; the containment check then sees the real destination.

String-filtering `..` before resolution is the classic mistake. `test_symlink_escape_is_blocked`
exists specifically to catch a regression to it.

### Why an allowlist for `http_get` rather than blocking dangerous hosts?

Failure direction. A blocklist must anticipate every dangerous target and fails **open** when it
misses one. An allowlist must name the safe ones and fails **closed**.

Also note the subdomain check uses a leading dot (`.github.com`). A naive
`endswith("github.com")` would accept `evil-github.com`. That is a tested case.

And redirects are not followed — a redirect is a second URL that passed none of the checks.

### Why not `eval()` in the calculator?

`eval` on model-generated text is arbitrary code execution driven by an untrusted source.
`__import__('os').system(...)` is one string away.

Instead the expression is parsed to an AST and walked with an explicit allowlist of node types;
`Call`, `Name`, `Attribute` and `Subscript` are rejected before evaluation.

Bonus question: why cap the exponent at 64? Because `9**9**9` blocks the event loop, and a
timeout cannot save you — the timeout task cannot run on a blocked loop. The guard has to be
*before* evaluation.

### V0.2 changed the provider interface. Why wasn't that a rewrite?

Because V0.1 built the seam. `LLMRequest` gained `history` and `tools`; `LLMResponse` gained
`tool_calls`. `prompt` still works, so `GroundedAgent` and its 11 tests were untouched — that is
the evidence the seam held, not a claim about it.

The one wrinkle: `Turn` carries an opaque `provider_state`, because Gemini 3.x requires a
`thought_signature` on function-call parts to be returned **verbatim** — a reconstructed
equivalent is rejected with a 400. Some vendor continuation state cannot be modelled generically,
so it is carried as an opaque token that only the producing provider interprets. ADR-005
predicted needing such an escape hatch; this is it.

### A tool-using goal costs 2 API calls. Why did that number matter so much?

The free tier is **20 requests per day per model**, not 15 per minute. At 3 calls per goal that
is 6 goals a day; at 2 it is 10.

The third call existed because a docstring asserted that Gemini could not accept tools and a
`response_schema` in the same request — written as "verified against the API" when it had never
been tested. It can. Requesting the schema on every turn means a turn that stops calling tools
already carries the validated answer.

The lesson is the one worth keeping: **an assumption written down confidently is harder to
question than one written down as an assumption.**

---

## What V0.2 does NOT demonstrate

- Still no persistence — nothing survives a restart
- Still one agent. **Not multi-agent**, and not claimed until V0.7
- No planning or decomposition; the model calls tools within a single goal
- No retrieval, so still not RAG
- No authentication, no rate limiting, no audit trail. **Not safe to expose publicly**
