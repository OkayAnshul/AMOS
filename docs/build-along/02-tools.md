# V0.2 — Tools

**+1,836 lines.** The agent gets to affect the world, and you build the boundary deciding what it
may touch.

---

## Where you are

A typed service that answers questions from the model's own knowledge. It cannot compute, look
anything up, or read a file.

## The problem

Ask it "what is 17% of 2340" and it will produce a confident number that is sometimes wrong.
Models are unreliable at arithmetic and cannot know anything after their training cutoff.

The fix is not a better prompt. It is **giving it deterministic capabilities and letting it choose
when to use them** — while the system, not the model, decides what those capabilities may do.

## What you will have at the end

An agent that reads *"What is 17% of 2340 plus 88?"*, decides to call a calculator, gets 485.8, and
answers with the tool call visible in the response. And one that, asked to read
`../../../../etc/passwd`, is refused by code rather than by a prompt.

---

## Decisions you are making here

### `Tool` is an ABC — and `LLMProvider` was a Protocol. That is not inconsistency.

They differ in *what they share*.

- Providers share a **shape**. Nothing is inherited → structural typing.
- Tools share **behaviour that must not be skipped**: every one must validate its arguments and
  honour its timeout.

That behaviour goes in a concrete `execute()`; subclasses implement only `_run()`. So **a tool
cannot opt out of validation or timeouts — it is given no opportunity to.** If each author had to
remember, one eventually would not, and that tool would be the vulnerability.

> **Shared shape → Protocol. Shared behaviour that must not be skipped → ABC.**

### Tool failures are data, not exceptions

`execute()` **never raises**. Every path returns a `ToolOutcome`, including `not_found` for a tool
the model invented.

A hallucinated tool name is *expected*, not exceptional — the model is told what exists and
sometimes invents something else. Raising turns a recoverable mistake into a failed request. The
error message lists the real tools so it can correct itself.

The agent still cannot be trapped: a failed attempt consumes an iteration, and the loop cap
converges regardless.

### Security is code that never reads model output

The governing principle for the whole milestone. Permissions, allowlists and sandboxes are checked
by functions that take a path or a URL and decide. They do not consult the model's stated intent.

*A prompt instruction is a request. A code check is a guarantee.*

---

## Build order

### 1. `src/amos/tools/base.py` (~193 lines) — the contract

**Why now** — everything else in this milestone is an instance of it. Get `execute()` right once
and every tool you ever write inherits the guarantees.

**Write**
```python
class Permission(StrEnum):
    PURE = "pure"                  # no I/O
    READ_LOCAL = "read_local"      # sandboxed filesystem
    NETWORK_READ = "network_read"  # allowlisted HTTP
    WRITE = "write"                # NOT IMPLEMENTED — needs approval workflow
    DESTRUCTIVE = "destructive"    # NOT IMPLEMENTED

class ToolStatus(StrEnum):
    OK; NOT_FOUND; INVALID_ARGS; TIMEOUT; DENIED; ERROR

class ToolCall(BaseModel):    id: str; name: str; arguments: dict[str, Any]

class ToolOutcome(BaseModel):
    """⚠️ These fields are the `tool_calls` table columns at V0.3."""
    call_id: str; name: str; status: ToolStatus
    arguments: dict[str, Any] = {}      # ← see the trap
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: int = 0
    @property
    def succeeded(self) -> bool: ...
    def to_model_payload(self) -> dict[str, Any]:
        """What the model sees. Errors are DESCRIBED, never hidden."""

class Tool(ABC):
    name: ClassVar[str]; description: ClassVar[str]
    input_schema: ClassVar[type[BaseModel]]
    permission: ClassVar[Permission]
    timeout_seconds: ClassVar[float] = 10.0

    @abstractmethod
    async def _run(self, args: Any) -> dict[str, Any]:
        """Do the work. `args` is ALREADY validated."""

    async def execute(self, call: ToolCall) -> ToolOutcome:
        """Validate → run under a timeout → never raise. Subclasses never touch this."""

    @classmethod
    def spec(cls) -> ToolSpec:
        """Declaration for the model, GENERATED from input_schema.model_json_schema()."""
```

**Invariants**
1. `execute()` never raises.
2. Arguments are validated **before** `_run` is entered.
3. The declaration is generated, never hand-written.

**Why (3)** — a hand-maintained schema drifts from the validator, and then the model is told one
thing while the code enforces another. It supplies what it was told to and gets rejected, with no
way to discover why.

⚠️ **`arguments` on `ToolOutcome` is easy to leave out.** The original did, and only noticed at
V0.3.

<details><summary>Cause</summary>
Without it the trace records what a tool *returned* but not what it was *asked* — half a trace, and
the half you need when debugging.
</details>

**Answer key** — `git show v0.2:src/amos/tools/base.py`

---

### 2. `src/amos/tools/registry.py` (~67 lines)

**Why now** — the agent needs a set of tools and a way to describe them to the model.

**Write**
```python
class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None: ...
    def register(self, tool: Tool) -> None:
        """Rejects duplicates. REFUSES Permission.WRITE and DESTRUCTIVE."""
    def get(self, name: str) -> Tool:
        """Raises ToolNotFoundError NAMING THE AVAILABLE TOOLS."""
    def specs(self) -> list[ToolSpec]:
        """Sorted. An unstable tool order changes the prompt between runs."""
    def names(self) -> list[str]: ...
```

**Invariant** — `WRITE`/`DESTRUCTIVE` cannot be registered. Not discouraged in a doc; refused in
code, so crossing that line has to be a deliberate act.

**Why registration is explicit, not filesystem discovery** — implicit discovery means the set of
capabilities your agent has depends on which files happen to be importable. Unpleasant for
something deciding what the system may touch.

**Why `get()` names the alternatives** — that error message goes back to the model. Without it, it
cannot recover.

---

### 3. `src/amos/tools/builtin/calculator.py` (~108 lines)

**Why now** — the simplest possible real tool. Pure, no I/O, one obvious failure mode.

**Write**
```python
_BINARY_OPS: dict[type[ast.operator], Callable[[Number, Number], Number]]   # Add, Sub, Mult, Div, FloorDiv, Mod, Pow
_UNARY_OPS:  dict[type[ast.unaryop],  Callable[[Number], Number]]           # UAdd, USub
_MAX_EXPONENT = 64
_MAX_EXPRESSION_LENGTH = 200

class CalculatorTool(Tool):
    permission = Permission.PURE
    timeout_seconds = 2.0
    async def _run(self, args) -> dict[str, Any]:
        tree = ast.parse(args.expression, mode="eval")
        return {"expression": ..., "result": _evaluate(tree.body)}

def _evaluate(node: ast.expr) -> float | int:
    """Walk the AST. Permit ONLY the operations above. Reject everything else."""
```

**Invariant — do not use `eval()`.** `eval` on model-generated text is arbitrary code execution
driven by an untrusted source. `__import__('os').system(...)` is one string away.

Reject `Call`, `Name`, `Attribute`, `Subscript` **before** evaluation.

**Why cap the exponent at 64** — `9**9**9` blocks the event loop, and **a timeout cannot save
you**: the timeout task cannot run on a blocked loop. The guard has to be before evaluation, not
around it. That is the subtlest thing in this milestone.

**Why booleans are excluded** — `bool` subclasses `int`, so `True + 1` would silently evaluate to
2.

**Test first** — parametrise nine code-execution payloads: `__import__('os').system('...')`,
`open('/etc/passwd')`, `eval('1+1')`, `().__class__.__bases__[0]`, `globals()`, a bare name, a
subscript, a lambda, `print(...)`. All must be `INVALID_ARGS`.

---

### 4. `src/amos/tools/builtin/read_file.py` (~92 lines)

**Write**
```python
class ReadFileTool(Tool):
    permission = Permission.READ_LOCAL
    def __init__(self, sandbox_root: Path | str) -> None:
        self._root = Path(sandbox_root).resolve()    # resolved ONCE, at construction
    def _resolve_within_sandbox(self, raw_path: str) -> Path:
        candidate = (self._root / raw_path).resolve()     # ← resolve FIRST
        if not candidate.is_relative_to(self._root):      # ← then check
            raise ToolValidationError(...)
```

**Invariant — resolve, then check. Never check the string.**

**Why the order is the whole defence** — two different attacks:

| Attack | Defeated by |
|---|---|
| `../../../../etc/passwd` | resolve then check containment |
| a **symlink inside the sandbox** pointing out | `resolve()` follows symlinks; the check sees the real destination |

String-filtering `..` before resolution is the classic mistake. A symlink named `innocent.md`
contains no `..`, no absolute path, nothing suspicious — only resolution reveals where it points.

**Test first** — five traversal forms, **and the symlink escape**. That last one is the test that
catches a regression to string filtering.

Also: an extension allowlist (so a `.env` *inside* the sandbox is still unreadable), a size cap,
UTF-8 only.

---

### 5. `src/amos/tools/builtin/http_get.py` (~133 lines)

**Write**
```python
DEFAULT_ALLOWLIST = frozenset({"docs.python.org", "github.com", ...})

class HttpGetTool(Tool):
    permission = Permission.NETWORK_READ
    def _validate_url(self, raw_url: str) -> str:
        # 1. scheme must be https
        # 2. host on the allowlist — exact match or a REGISTERED SUBDOMAIN
        # 3. resolved IP not private / loopback / link-local
    async def _run(self, args) -> dict[str, Any]:
        # follow_redirects=False
```

**Invariant — allowlist, never blocklist.** A blocklist must anticipate every dangerous target and
fails **open** when it misses one. An allowlist names the safe ones and fails **closed**. For a
model-supplied URL, failing closed is the only acceptable default.

**Three subtleties, each a test:**

| | |
|---|---|
| The subdomain check needs a **leading dot** | a naive `endswith("github.com")` accepts `evil-github.com` |
| The **resolved IP** is checked, not just the name | catches `169.254.169.254` cloud metadata, and an allowlisted name whose DNS points internally |
| **Redirects are not followed** | a redirect is a second URL that passed none of the checks |

---

### 6. Extend `src/amos/llm/base.py` — **the seam, extended not rewritten**

`LLMRequest` gains `tools: list[ToolSpec]` and `history: list[Turn]`. `LLMResponse` gains
`tool_calls: list[ToolCall]`.

**`prompt` still works.** That is the test: after this change, V0.1's `GroundedAgent` and all 11 of
its tests must pass **untouched**. If they do not, your V0.1 seam was wrong and it is worth
knowing now.

⚠️ **A trap only the real API will show you.** Your scripted tests will all pass and the live one
will fail with a 400 on the *second* round trip.

<details><summary>Cause</summary>

Gemini 3.x attaches an encrypted `thought_signature` to function-call parts and requires it returned
**verbatim**. A reconstructed-but-identical call is rejected.

Fix: an opaque `provider_state` field on `Turn` holding the vendor's original content object,
replayed unchanged. Only the producing provider interprets it.

**Fakes cannot test what they do not model.** This is the argument for keeping a live smoke test.
</details>

---

### 7. `src/amos/agents/tool_agent.py` (~180 lines) — the bounded loop

**Write**
```python
class ToolUsingAgent:
    def __init__(self, provider, registry, *, timeout=30.0, max_iterations=5, ...) -> None: ...

    async def run(self, goal: str) -> AgentResult:
        # for iteration in range(max_iterations):
        #     ask the model, WITH tools AND response_schema
        #     no tool calls? -> the answer is already here. return.
        #     else: execute each, append outcomes to history, loop
        # exhausted -> raise ToolLoopExhaustedError

    async def _invoke(self, call: ToolCall) -> ToolOutcome:
        """Never raises. Unknown tool -> NOT_FOUND naming what exists."""
```

**Invariants**
1. The iteration cap is enforced **in code**. The prompt asking the model to stop is a request.
2. Every failure is fed back to the model, not raised.
3. Tool output is **data, never instructions**.

**On (3), be precise about what actually protects you.** The system prompt does tell the model to
treat tool output as data. *That is not a control* — a persuasive payload overrides any
instruction. What holds is: the registry is fixed at startup, each tool enforces its own boundary
regardless of why it was called, and write tools cannot be registered.

Test it that way: **assume the model is fully compromised** and emits the attacker's call. The
system still refuses.

**Ask the model for tools and the response schema in the same request.** They combine — verify it
yourself rather than believing either me or a docstring.

⚠️ The original assumed they could not, added an extra "finalise" call, and annotated the docstring
*"Verified against the API"* when it had never been tested. That cost one wasted call per goal —
a third of the daily budget.

**The damage was not the wrong belief. It was writing it down as verified**, which stopped the next
reader questioning it.

**Test first** — `tests/unit/test_tool_agent.py`:

| Test | Trap |
|---|---|
| hallucinated tool name does not crash | treating expected behaviour as exceptional |
| invalid args rejected **before** execution | trusting model-generated arguments |
| tool failure fed back, loop continues | aborting on a recoverable mistake |
| **loop cap stops the provider calls**, not just the result | a cap that fires too late to matter |
| injected tool output cannot widen permissions | the security claim, tested from the attacker's side |

---

## Checkpoint

```bash
.venv/bin/python -m pytest -q          # ~115 passing
.venv/bin/python -m amos

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"What is 17% of 2340 plus 88?"}' | jq '.response.answer, .tool_outcomes'
# 485.8, via calculator

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Read the file ../../../../etc/passwd"}' | jq '.tool_outcomes[0].error'
# "resolves outside the permitted directory"
```

The second one is the milestone. The model tried; the code refused.

---

## What this unlocks

You now generate `ToolOutcome` records with arguments, status, output and latency — which are the
`tool_calls` rows at V0.3. And you have a registry, which is what V0.7's specialised agents filter
to create capability differences.

Next: [`03-persistence.md`](03-persistence.md) — where all of this becomes durable, and you find
out whether your V0.1 and V0.2 seams were actually right.
