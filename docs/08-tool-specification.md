# 08 — Tool Specification

**Written at V0.2.** Describes the tool system as built.

## What a tool is

A deterministic capability an agent may invoke. Tools are the only way an agent affects
anything outside itself, which makes them the security boundary of the whole system.

```
Tool
├── name              stable identifier the model calls
├── description       what it does and when to use it — the model reads this
├── input_schema      Pydantic model; validation AND declaration
├── permission        blast radius (see below)
├── timeout_seconds   hard bound, enforced by the base class
└── _run()            the implementation
```

## Why `Tool` is an ABC, when `LLMProvider` is a Protocol

They differ in what they share. Providers share a *shape* — nothing is inherited, so structural
typing is right. Tools share *behaviour*: every one must validate its arguments and honour its
timeout.

That behaviour lives in `Tool.execute()`, which is concrete. Subclasses implement `_run()` and
never touch `execute()`. The result is that **a tool cannot opt out of validation or timeouts**
— it is given no opportunity to. If each tool author were responsible for remembering, one
eventually would not, and that tool would be the vulnerability.

## Schema: one source of truth

`Tool.spec()` derives the model-facing declaration from `input_schema.model_json_schema()`, and
the same Pydantic model validates incoming arguments. It is never hand-written.

A hand-maintained declaration drifts from the validator, and then the model is told one thing
while the code enforces another — the model supplies what it was told to and gets rejected, with
no way to discover why. Generating both from one definition makes the drift impossible.

Gemini accepts the Pydantic JSON Schema directly via `parameters_json_schema`, so no translation
layer exists to introduce bugs.

**Constraint:** tool input schemas must be **flat** — no nested models. Pydantic emits `$ref`
and `$defs` for nested types, which complicates provider compatibility. If a nested schema is
ever genuinely needed, flatten it or write the ADR.

## Permissions

Ordered by blast radius:

| Permission | Meaning | V0.2 |
|---|---|---|
| `PURE` | No I/O, no side effects | `calculator` |
| `READ_LOCAL` | Reads the local filesystem, sandboxed | `read_file` |
| `NETWORK_READ` | Outbound HTTPS GET, allowlisted | `http_get` |
| `WRITE` | Modifies state | **refused by the registry** |
| `DESTRUCTIVE` | Irreversible | **refused by the registry** |

`WRITE` and `DESTRUCTIVE` are not merely undocumented — `ToolRegistry.register()` raises on
them. They require the human-approval workflow in `docs/13-security.md`, which is not built.
Naming the boundary in an enum and enforcing it in code is more honest than leaving it
unmentioned, and it means crossing it has to be a deliberate act.

Every V0.2 tool is **deterministic and reversible**. No payments, no deletion, no sending.

## The execution contract

`execute()` **never raises**. Every path returns a `ToolOutcome`:

| Status | Cause |
|---|---|
| `ok` | Succeeded |
| `not_found` | The model named a tool that does not exist |
| `invalid_args` | Arguments failed schema validation, or the tool rejected them |
| `timeout` | Exceeded `timeout_seconds` |
| `denied` | Permission refused |
| `error` | Unexpected exception, caught and wrapped |

Failures are **data, not exceptions**, because the model needs to be told what went wrong so it
can correct itself. An exception would unwind the loop and turn a recoverable mistake into a
failed request.

Every outcome — including failures — is fed back to the model via
`ToolOutcome.to_model_payload()`.

## The tools

### `calculator` (PURE)
AST-walked arithmetic with an explicit node allowlist. **Does not use `eval()`** — `eval` on
model-generated text is arbitrary code execution driven by an untrusted source. Exponents are
capped at 64 because `9**9**9` blocks the event loop, and a timeout cannot rescue a blocked
event loop.

### `read_file` (READ_LOCAL)
Reads UTF-8 text within a sandbox root. Path is **resolved first, then checked** for containment
— string filtering of `..` misses symlinks entirely. Extension allowlist, 100 KB cap.

### `http_get` (NETWORK_READ)
HTTPS only, host allowlist, DNS resolution checked against private/loopback/link-local ranges,
redirects **not followed**. Details in `docs/13-security.md`.

## Adding a tool

1. Subclass `Tool`, set the class variables, implement `_run()`.
2. Flat Pydantic input schema, with `description` on every field — the model reads them.
3. Write the failure tests first: invalid arguments, timeout, and whatever hostile input applies.
4. Register it in `build_default_registry()`.
5. If it needs `WRITE` or `DESTRUCTIVE`, stop — that requires the approval workflow first.
