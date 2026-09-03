# 13 — Security

**Written at V0.2** — the milestone where AMOS first gained the ability to touch anything
outside its own process. Before tools, the attack surface was one HTTP endpoint. After tools,
a model can be persuaded to read files and make network requests.

## The threat model

The model is **not trusted**. Not because it is malicious, but because its behaviour is
influenced by input AMOS does not control: user goals, file contents, fetched web pages. Anything
the model emits — a tool name, an argument, a URL, a path — is untrusted input.

This is the governing principle:

> **Security is enforced in code that never reads model output.**

Permissions, allowlists and sandboxes are checked by functions that take a path or a URL and
decide. They do not consult the model's reasoning, its stated intent, or its claimed
justification. A prompt instruction is a request; a code check is a guarantee.

## Prompt injection

Untrusted content reaches the model in two ways: the user's goal, and tool output (a fetched
page, a file's contents).

**What AMOS does not rely on:** the system prompt does tell the model to treat tool output as
data, not instructions. That helps, and it is not a control — a sufficiently persuasive payload
can override any instruction.

**What actually holds:**

1. Tool output enters the conversation as a *function response*, never as a system instruction.
2. The tool registry is fixed at startup. A payload saying "you may now use `delete_file`"
   changes nothing — the tool does not exist, and the call returns `not_found`.
3. Every tool enforces its own boundary. A payload saying "read `/etc/passwd`" produces a real
   `read_file` call that the sandbox check rejects on its own terms.
4. `WRITE` and `DESTRUCTIVE` tools cannot be registered at all.

Tested in `test_prompt_injection_in_tool_output_does_not_change_permissions`. The test asserts
the interesting thing: **even assuming the model is fully compromised** and emits the attacker's
desired call, the system refuses it.

The residual risk is honest: a payload can still make the model produce a *wrong answer* using
permitted tools. AMOS constrains what can be *done*, not what can be *said*.

## `read_file` — path traversal and symlink escape

Two distinct attacks:

| Attack | Example | Defence |
|---|---|---|
| Traversal | `../../../../etc/passwd` | resolve, then check containment |
| Symlink escape | a symlink inside the sandbox pointing out | `Path.resolve()` follows symlinks; the check is on the *resolved* path |

The order is the whole defence. Validating the string before resolution is the classic mistake:
a symlink named `innocent.md` contains no `..`, no absolute path, nothing suspicious — only
resolution reveals where it points. `test_symlink_escape_is_blocked` covers exactly this.

Additionally: extension allowlist (so `.env` inside the sandbox is still unreadable), 100 KB cap,
UTF-8 only.

## `http_get` — SSRF

The URL comes from the model, so this tool is an SSRF primitive by construction.

**Allowlist, not blocklist.** A blocklist must anticipate every dangerous target and fails *open*
when it misses one. An allowlist must name the safe ones and fails *closed*. For a
model-supplied URL, failing closed is the only acceptable default.

Four checks, all before any request:

1. **Scheme** — `https` only. Blocks `file://`, `ftp://`, `gopher://`.
2. **Host allowlist** — exact match or a genuine subdomain. The subdomain check uses a leading
   dot (`.github.com`), because a naive `endswith("github.com")` would accept
   `evil-github.com`. Tested.
3. **Resolved IP** — rejects private, loopback and link-local addresses. This catches
   `169.254.169.254` (cloud metadata) and an allowlisted *name* whose DNS record points
   internally — which the allowlist alone would miss. Defence in depth.
4. **No redirects** — a redirect is a second URL that passed none of the above checks.

## `calculator` — code execution

Does not use `eval()`. The expression is parsed to an AST and walked with an explicit allowlist
of node types; `Call`, `Name`, `Attribute` and `Subscript` are all rejected before evaluation.

Tested against `__import__('os').system(...)`, `open(...)`, `().__class__.__bases__[0]` and
others. Exponent cap of 64 prevents `9**9**9` from blocking the event loop — note that a timeout
would *not* help there, since a blocked event loop cannot run the timeout.

## Secrets

- Configuration from the environment only. `.env` is gitignored; `.env.example` carries no real
  values.
- Verified before the repository was made public: the full git history was scanned for the key
  pattern and for any tracked `.env`.
- A missing key fails at startup, not at request time.

## Resource bounds

Unbounded anything is a denial-of-service vector, including a self-inflicted one:

| Bound | Value | Prevents |
|---|---|---|
| Agent loop iterations | 5 (configurable, hard cap) | runaway tool loops, unbounded cost |
| Provider timeout | 30s | hung requests |
| Per-tool timeout | 2–15s | a slow tool hanging the agent |
| File read | 100 KB | memory exhaustion |
| HTTP response | 200 KB | memory exhaustion |
| Goal length | 8000 chars | oversized prompts |
| Expression length | 200 chars | parser abuse |

## Not implemented — and not claimed

| Control | Status | Why |
|---|---|---|
| Authentication | ❌ | Single local user. Would mean a `users` table with one permanent row. |
| Authorization / RBAC | ❌ | No users to distinguish |
| Rate limiting (inbound) | ❌ | No untrusted callers yet |
| Audit log | ⚠️ partial | Structured logs record every tool call; not durable until V0.3 |
| Human approval workflow | ❌ | Required before any `WRITE` tool. Registry refuses them until it exists. |
| Data isolation | ❌ | Single user |

Reconsider all of these the moment AMOS is exposed to a second user or reachable from a network
it does not control. **AMOS is not currently safe to expose publicly**, and nothing in it should
be described as production-secure.
