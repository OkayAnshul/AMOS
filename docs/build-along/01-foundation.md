# V0.1 — Foundation

**~1,000 lines. 15 source files, 11 test files. The largest single step, because you start from
nothing.**

---

## Where you are

An empty package and six decisions. Nothing runs.

## The problem

A single LLM call is a poor fit for anything real. Ask a chatbot to compare two designs and you get
one pass of plausible text: no structure you can dispatch on, no record of what it cost, and no
handling of the case where it returns something unusable.

The gap is not model quality. **It is that there is no system around the model.**

V0.1 builds the smallest thing that is genuinely a system: a typed service where model output is
validated before it becomes control flow, failures have types, and every request is traceable.

## What you will have at the end

```bash
curl -X POST localhost:8000/v1/goals -d '{"goal":"What is idempotency?"}'
```
→ a schema-validated answer with reasoning, assumptions, confidence and caveats, plus token cost
and latency in the logs. 41 tests passing, none touching the network.

---

## Decisions you are making here

### No database. None.

Nothing here needs to survive a restart. A goal comes in, an answer goes out.

Adding Postgres means Docker, a schema, migrations and connection lifecycle — real complexity
against no requirement — and it makes V0.1 impossible to run without infrastructure, which breaks
"every milestone is runnable".

*You will want to add it. The discomfort is the point.*

### `LLMProvider` is a Protocol, not an ABC

Providers share a **shape**, not behaviour. Nothing is inherited, so structural typing is right: a
fake satisfies the interface without importing or subclassing anything.

*Rejected:* an ABC — it would force every implementation to inherit from your class for no shared
code.

**And this is not speculative generality.** Your tests need a fake provider regardless, because the
free tier is rate-limited and non-deterministic. The abstraction is paid for by V0.1's own tests.

### The four seams

This is what makes V0.2–V1.0 additions rather than rewrites. Each is justified by something V0.1
needs *today*:

| Seam | Needed now because | Later becomes |
|---|---|---|
| `LLMProvider` Protocol | tests must not hit the network | other providers |
| `AgentResult` + `LLMCallRecord` | structured logging needs the fields | **database rows** at V0.3 |
| validate-and-repair loop | malformed JSON is a real failure | tool-arg validation (V0.2), plan validation (V0.4) |
| request id in every log line | debugging needs it | the OTel `trace_id` at V0.9 |

**None is speculative.** That is the difference between designing for evolution and
over-engineering, and if you cannot state the V0.1 justification for a seam, do not build it.

---

## Build order

Bottom-up: nothing depends on anything above it. Write and test each before moving on.

### 1. `src/amos/errors.py` (~58 lines)

**Why now** — every layer below needs somewhere to raise to. Write it first and you never invent
an ad-hoc exception under pressure.

**Write**
```python
class AmosError(Exception):
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None: ...

class ConfigurationError(AmosError): ...       # invalid config. Raised at STARTUP, never at request time
class ProviderError(AmosError): ...            # base for LLM failures
class ProviderTimeoutError(ProviderError): ...
class ProviderAuthError(ProviderError): ...
class ProviderRateLimitError(ProviderError): ...

class OutputValidationError(AmosError):
    # carries `attempts` and `last_error` — the terminal state of the repair loop
    def __init__(self, message: str, *, attempts: int, last_error: str, ...) -> None: ...
```

**Invariant** — no bare `Exception` crosses a layer boundary. Every error the API can return is one
of these, which is what lets the status-code mapping live in one table instead of scattered
`except` blocks.

**Answer key** — `git show v0.1:src/amos/errors.py`

---

### 2. `src/amos/observability.py` (~65 lines)

**Why now** — before anything that could fail. You want the request id threading through logs from
the first line of real code, not retrofitted after your first confusing bug.

**Write**
```python
_request_id: ContextVar[str | None]        # module-level, threads through async calls

def new_request_id() -> str: ...           # 16 hex chars
def set_request_id(rid: str) -> None: ...
def get_request_id() -> str | None: ...

class JsonFormatter(logging.Formatter):
    """One JSON object per line. Attaches the request id automatically."""

def configure_logging(level: str = "INFO") -> None: ...
def log_event(logger, message: str, **fields: Any) -> None: ...
```

**Invariant** — every log line is one JSON object, and carries the request id when one is set.

**Why a ContextVar** — threading a request id through every function signature would poison every
interface in the codebase for one cross-cutting concern. ContextVars follow async execution
correctly, which ordinary globals do not.

⚠️ **Trap, and it will bite you at V0.9.** `log_event(logger, message, **fields)` takes `message`
positionally. Passing `message=` as a keyword collides with it.

<details><summary>Cause</summary>

`TypeError: log_event() got multiple values for argument 'message'`. Prefix your structured fields
— `error_message`, not `message`. This broke every error path in the original build while the happy
path stayed green.
</details>

**Answer key** — `git show v0.1:src/amos/observability.py`

---

### 3. `src/amos/config.py` (~58 lines)

**Why now** — the provider needs an API key, and you want the failure mode decided before anything
reads it.

**Write**
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AMOS_", env_file=".env", extra="ignore")

    gemini_api_key: str = Field(default="")
    llm_model: str = Field(default="gemini-3.5-flash")
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)   # bounds are not decoration
    llm_max_repair_attempts: int = Field(default=2, ge=0, le=5)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    env: str = "development"
    log_level: str = "INFO"

    def require_api_key(self) -> str:
        """Return the key or raise ConfigurationError naming the fix."""

@lru_cache(maxsize=1)
def get_settings() -> Settings: ...
```

**Invariant** — a missing key fails at **startup**, not on the first request an hour later. The
error message names the fix (copy `.env.example`, get a key from AI Studio); an error that does not
tell you what to do is half an error.

**Why `require_api_key()` and not a required field** — tests need to build `Settings` without a
key. Validation at the point of use, not at construction.

⚠️ **Trap you will hit at V0.3.** `env_file=".env"` means tests inherit whatever is in *your*
`.env`. It stays invisible until you add a setting that changes behaviour, and then a test that has
not changed starts failing.

<details><summary>Cause</summary>

Build test settings with `Settings(_env_file=None, ...)`. A test that reads `.env` depends on the
machine it runs on. Latent for two milestones in the original build.
</details>

**Test first** — `tests/unit/test_config.py`: missing key raises and the message names the fix;
env prefix applies; timeout bounds reject 0 and 1000.

---

### 4. `src/amos/llm/base.py` (~85 lines) — **the seam**

**Why now** — everything above talks to the model through this. Get it right and V0.2's tools,
V0.4's planner and V0.7's agents all slot in without touching it.

**Write**
```python
class LLMRequest(BaseModel):
    prompt: str
    system_instruction: str | None = None
    response_schema: type[BaseModel] | None = None    # ask the model for STRUCTURE
    temperature: float = 0.2

class LLMResponse(BaseModel):
    text: str
    parsed: BaseModel | None = None      # None when the provider could not parse — the repair loop's trigger
    model: str
    provider: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str | None = None

class LLMCallRecord(BaseModel):
    """⚠️ These fields ARE the `llm_calls` table columns at V0.3.
    Shape it right now and persistence is a serialisation change, not a redesign."""
    provider: str; model: str
    prompt_tokens: int; output_tokens: int; latency_ms: int
    repair_attempt: int = 0
    error: str | None = None

@runtime_checkable
class LLMProvider(Protocol):
    name: str
    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse: ...
```

**Invariant** — implementations raise AMOS's typed errors, never vendor exceptions. Nothing above
this file imports a provider SDK.

**Why `parsed` is optional** — a truncated response (`MAX_TOKENS` mid-JSON) yields text but no
object. That case is exactly what the repair loop exists for.

---

### 5. `src/amos/llm/fake.py` (~97 lines) — **write this before the real provider**

**Why now** — you cannot test against a 15-requests-per-minute API. And writing the fake first
forces the Protocol to be genuinely implementable rather than shaped around one vendor.

**Write**
```python
class FakeProvider:
    """Replays a scripted sequence. Each item is either
       str        -> returned as text (parsed if it fits the schema)
       Exception  -> raised, to script failures"""
    name = "fake"
    def __init__(self, responses: Sequence[str | Exception], ...) -> None: ...
    @property
    def call_count(self) -> int: ...
    async def complete(self, request, *, timeout) -> LLMResponse: ...

class AlwaysFailsProvider:
    """Raises a given error every time."""
```

**Invariant** — malformed JSON must set `parsed=None` rather than raising. The repair-loop tests
depend on that exact behaviour.

**Test first** — `tests/unit/test_fake_provider.py`. **Test the fake itself.** If your test
infrastructure lies, every test above it lies too.

⚠️ **A trap that stayed hidden for two milestones in the original.** If you hash anything to build
fake data, do not use Python's built-in `hash()`.

<details><summary>Cause</summary>

`hash()` is randomised per process by PYTHONHASHSEED. A "deterministic" fake built on it produces
different values every run, and tests pass or fail by luck. The determinism test passed throughout,
because it compared two calls *inside one process*.

**Testing determinism requires a value fixed outside the process.** Use `hashlib.blake2b`.
</details>

---

### 6. `src/amos/llm/gemini.py` (~100 lines)

**Why now** — the fake proved the Protocol works. Now make it real.

**Write**
```python
class GeminiProvider:
    name = "gemini"
    def __init__(self, api_key: str, model: str) -> None: ...
    async def complete(self, request, *, timeout) -> LLMResponse:
        # asyncio.wait_for around client.aio.models.generate_content
        # translate vendor errors -> ProviderTimeout/Auth/RateLimit/ProviderError
    @staticmethod
    def _translate_client_error(exc) -> ProviderError:
        # 429 -> RateLimit, 401/403 -> Auth, else ProviderError
```

**Invariant** — **every external call is timeout-bounded.** Do not trust the SDK's own timeout.
This is the rule that makes the whole system's latency reasonable about.

**Verify the API, do not assume it.** Before writing: check the SDK's async path exists, check the
current model IDs, check that structured output returns what you think.

```bash
.venv/bin/python -c "from google import genai; c=genai.Client(api_key='x'); print(hasattr(c,'aio'))"
```

---

### 7. `src/amos/agents/schemas.py` (~63 lines)

**Why now** — the agent needs a contract to hold the model to.

**Write**
```python
class Confidence(StrEnum):
    HIGH = "high"; MEDIUM = "medium"; LOW = "low"

class AgentResponse(BaseModel):
    answer: str
    reasoning: str
    assumptions: list[str] = []      # what it had to assume because you were vague
    confidence: Confidence
    caveats: list[str] = []          # what could make this wrong

class GoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)

class AgentResult(BaseModel):
    """⚠️ This envelope becomes the `steps` row at V0.3."""
    request_id: str
    response: AgentResponse
    llm_calls: list[LLMCallRecord] = []
    repair_count: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
```

**Why assumptions and confidence are separate fields** — asking for them separately makes the
model's uncertainty *inspectable* instead of buried in prose, and gives V1.0's evaluation harness
something to score.

**Test first** — `tests/unit/test_schemas.py`: missing required field rejected; invalid confidence
rejected; empty goal rejected; oversized goal rejected.

---

### 8. `src/amos/agents/agent.py` (~173 lines) — **the heart of V0.1**

**Why now** — everything below it exists. This is the piece that turns a model call into a system.

**Write**
```python
SYSTEM_INSTRUCTION = """..."""      # state assumptions, set confidence honestly, don't invent
REPAIR_INSTRUCTION = """Your previous response could not be parsed... Error: {error}"""

class GroundedAgent:
    def __init__(self, provider: LLMProvider, *, timeout=30.0,
                 max_repair_attempts=2, temperature=0.2) -> None: ...

    async def run(self, goal: str) -> AgentResult:
        # for attempt in range(max_repair_attempts + 1):
        #   attempt 0 is the real try; 1..N append REPAIR_INSTRUCTION with the actual error
        #   record EVERY attempt in llm_calls, including failures — they cost tokens
        #   validated? return.  else: keep the error, loop.
        # exhausted -> raise OutputValidationError(attempts=..., last_error=...)

    @staticmethod
    def _validate(parsed: object | None, raw_text: str) -> tuple[AgentResponse | None, str]:
        """Never raises. Returns (response, "") or (None, error). The CALLER decides to retry."""
```

**Invariants**
1. Unvalidated model output never becomes control flow.
2. **A provider timeout is not caught by the repair loop.** It propagates.
3. Failed attempts appear in `llm_calls`. Wasted spend must be visible.

**Why (2) matters, and it is the question you will be asked** — a timeout is a *transport* failure;
the repair loop fixes *validation* failures. Retrying it there burns the repair budget on something
re-prompting cannot fix, and hides an infrastructure problem behind what looks like a model problem.

**Test first** — `tests/unit/test_agent_repair.py`, and this is the most valuable file in V0.1:

| Test | The trap it catches |
|---|---|
| valid first response makes **one** call | repairing when nothing is wrong |
| malformed → valid repairs **once** | off-by-one in the attempt loop |
| repair prompt contains **the actual error** | a "repair" that is just a retry |
| exhausted repairs raise with `attempts=3` | silent failure |
| **provider error is NOT swallowed** | the invariant above |
| tokens accumulate across repairs | invisible cost |

**Rejected** — no repair loop at all, trusting the provider's structured output. It makes valid
JSON *likely*, not certain: truncation, safety stops, and constraints the provider does not enforce
all produce unusable output.

---

### 9. `src/amos/api/` — `dependencies.py` (~29), `app.py` (~121)

**Why now** — last, because it is the thinnest layer. It does three things and no more: validate
input, delegate, map errors to status codes.

**Write**
```python
# dependencies.py — swapping the provider is a change HERE and nowhere else
def build_provider(settings) -> LLMProvider: ...
def build_agent(settings, provider=None) -> GroundedAgent: ...

# app.py
_STATUS_MAP: list[tuple[type[AmosError], int]] = [
    (ProviderTimeoutError, 504),
    (ProviderRateLimitError, 429),
    (ProviderAuthError, 502),
    (OutputValidationError, 502),      # ← think about why 502 and not 500
    (ConfigurationError, 500),
]

def create_app(settings=None, agent=None) -> FastAPI: ...
    # lifespan: build the agent, fail fast on a missing key
    # middleware: honour or generate x-request-id, echo it back
    # exception handler: AmosError -> {"error": {"type", "message", "details"}}
    # GET  /health
    # POST /v1/goals -> AgentResult
```

**Why `agent` is injectable** — tests build an app backed by `FakeProvider`, with no key and no
network.

**Why `OutputValidationError` is 502 and not 500** — the request was valid and your code worked
correctly; an upstream dependency failed to produce usable output. A 500 would send someone
debugging the wrong system.

**Test first** — `tests/integration/test_api.py`: status mapping for each error, request-id echo,
422 on empty goal, repair count visible in the response.

---

### 10. `tests/live/test_live_smoke.py` (~42 lines)

One test that touches the real API. **Skipped by default**:

```python
pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.getenv("AMOS_RUN_LIVE_TESTS") != "1", reason="opt-in")]
```

**Why opt-in** — the free tier is 15 requests/minute and non-deterministic. Network tests in the
main suite would be slow, flaky, and would exhaust your quota on the first CI run.

**Why keep it at all** — fakes cannot test what they do not model. This project shipped a bug that
every scripted test passed and only the real API caught.

---

## Checkpoint

**Do not skip this.** Passing tests is not a working application.

```bash
.venv/bin/python -m pytest -q            # 41 passed, 1 skipped
.venv/bin/mypy src                       # clean
.venv/bin/python -m amos                 # must actually start

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Explain idempotency in two sentences."}' | jq
```

You should get an answer, reasoning, assumptions, confidence and caveats — and a log line with the
request id, model, tokens and latency.

```bash
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live -v
```

---

## What this unlocks

The provider seam means V0.2 can add tools without touching `GroundedAgent`. The `AgentResult`
envelope means V0.3's persistence is a serialisation change. The request id means V0.9's tracing is
an afternoon.

**None of that was built for those milestones.** Each was paid for by something V0.1 needed. That
is the whole trick.

Next: [`02-tools.md`](02-tools.md) — where the agent gets to affect the world, and you build the
security boundary that decides what it may touch.
