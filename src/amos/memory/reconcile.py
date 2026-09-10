"""Making the system honest about what it remembered.

## The problem

`remember_fact` is called unreliably. Worse than the missed write is the **false
claim**: the model replies *"I have noted that"* when nothing was stored. The user
cannot detect it at the time, and discovers it next session.

Three attempts at fixing this by prompting (a registry fix, disambiguated tool
descriptions, an explicit system-prompt rule) made it rarer without making it go
away. AMOS's own rule says why that is the wrong lever:

> **LLMs handle uncertainty; software handles guarantees.**

So the guarantee moves here.

## What is guaranteed, and what is not

| | |
|---|---|
| **Guaranteed** | The system never claims to have remembered something it did not store |
| **Best effort** | The fact usually does get stored — one bounded retry |
| **Not promised** | That every stated fact is always stored |

That last row is deliberate. Guaranteeing storage would mean extracting facts and
writing them without the model choosing to — more model-decided writes, which is
the thing this fix exists to reduce.

## Shape

Mirrors the V0.7 critic loop, which already solves this problem shape: detect
deterministically, allow one bounded attempt to fix it, and surface the objection
when it is not fixed.

```
stored?  ← read tool_outcomes    a FACT
claimed? ← scan the answer       a HEURISTIC

claimed and not stored → one retry → still not stored → caveat + log
anything else          → untouched, zero extra calls
```

The common paths cost nothing. Only the inconsistency costs a call.
"""

from __future__ import annotations

import logging

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.llm.base import LLMProvider
from amos.observability import log_event
from amos.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

RECONCILE_INSTRUCTION = """You previously answered a user's goal and said you had remembered
something, but no fact was actually stored.

Call remember_fact now for the durable fact the user stated. Use a short stable
subject key (like user_supervisor or project_deadline) and state the fact plainly.

If the goal did not actually contain a durable fact about the user or their
project, do not call anything and say so."""

#: Phrases indicating the answer CLAIMS a fact was stored.
#:
#: **This is the weak link, and it is a keyword scan — the same shape that
#: produced V1.0's cautionary tale**, where a brittle refusal detector scored a
#: correct answer as a failure and nearly got written up as a system defect
#: (`docs/16-evaluation.md`).
#:
#: Two things make it safe enough here, and neither is "the list is good":
#:
#: 1. **The asymmetry is favourable.** A false positive costs one wasted retry.
#:    A false negative leaves today's behaviour. Neither corrupts data nor
#:    misreports quality.
#: 2. **It is never the basis for a claim.** `stored_this_run()` is a hard fact
#:    read from tool outcomes. This heuristic only decides whether to *check*.
#:
#: Note what is deliberately absent: "recall", "remember that you said" and
#: similar. Recalling is not writing, and firing on those would trigger pointless
#: retries on every successful lookup.
_CLAIM_MARKERS = (
    "noted",
    "i have stored",
    "i've stored",
    "have been stored",
    "has been stored",
    "i have saved",
    "i've saved",
    "have been saved",
    "has been saved",
    "i have recorded",
    "i've recorded",
    "have been recorded",
    "has been recorded",
    "i will remember",
    "i'll remember",
    "committed to memory",
    "for future sessions",
    "for later sessions",
    "for future reference",
    "added to memory",
    "saved for",
    "stored for",
    "remembered for",
)


def claims_memory(answer: str) -> bool:
    """Does this answer assert that something was remembered?"""
    lowered = answer.lower()
    return any(marker in lowered for marker in _CLAIM_MARKERS)


def stored_this_run(result: AgentResult) -> bool:
    """Did a `remember_fact` call actually succeed?

    Read from tool outcomes, so this is a **fact** rather than an inference. It
    is what the whole guarantee rests on — the heuristic above only decides
    whether this is worth checking.
    """
    return any(
        outcome.name == "remember_fact" and outcome.succeeded for outcome in result.tool_outcomes
    )


class MemoryReconciler:
    """Reconciles what the answer claims with what was actually written."""

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        *,
        timeout: float = 30.0,
        enabled: bool = True,
    ) -> None:
        self._provider = provider
        self._enabled = enabled
        self._timeout = timeout
        # A registry containing ONLY remember_fact. Same mechanism as
        # AgentSpec.registry_from() — capability is the allowlist, so the retry
        # cannot retrieve, plan or wander. Re-running the full Orchestrator
        # instead would cost 8-10 calls to store one fact.
        self._memory_tools = ToolRegistry([tool for tool in tools if tool.name == "remember_fact"])

    @property
    def can_retry(self) -> bool:
        return bool(len(self._memory_tools))

    async def reconcile(self, goal: str, result: AgentResult) -> AgentResult:
        """Return a result whose claims match reality. Never raises."""
        if not self._enabled:
            return result

        stored = stored_this_run(result)
        claimed = claims_memory(result.response.answer)

        if stored or not claimed:
            # The common paths. No inconsistency, so no cost.
            return result

        log_event(logger, "memory.claim_without_write", goal_length=len(goal))

        if self.can_retry:
            retried = await self._retry(goal, result)
            if retried is not None:
                if stored_this_run(retried):
                    log_event(logger, "memory.recovered_by_retry")
                    return retried
                result = retried  # keep the retry's calls in the trace

        log_event(logger, "memory.unresolved_claim")
        return _flag_unstored(result)

    async def _retry(self, goal: str, result: AgentResult) -> AgentResult | None:
        """One bounded attempt, through the real tool.

        Uses `remember_fact` rather than writing directly, so the store still
        passes the tool's own argument validation and permission checks.
        """
        from amos.agents.tool_agent import ToolUsingAgent

        agent = ToolUsingAgent(
            self._provider,
            self._memory_tools,
            timeout=self._timeout,
            max_iterations=2,
            system_instruction=RECONCILE_INSTRUCTION,
        )
        try:
            retry = await agent.run(f"The user's original goal was: {goal}")
        except Exception as exc:  # noqa: BLE001
            # Reconciliation must never fail a run. The answer is already
            # computed and correct; losing it to protect a bookkeeping step
            # would be a strictly worse outcome.
            log_event(logger, "memory.retry_failed", error=type(exc).__name__)
            return None

        # Merge the retry's cost into the trace. V0.7's rule: the trace accounts
        # for every call, including the ones spent fixing something.
        return result.model_copy(
            update={
                "llm_calls": [*result.llm_calls, *retry.llm_calls],
                "tool_outcomes": [*result.tool_outcomes, *retry.tool_outcomes],
                "total_tokens": result.total_tokens + retry.total_tokens,
            }
        )


def _flag_unstored(result: AgentResult) -> AgentResult:
    """Correct an answer that claimed a memory it does not have.

    The caveat is added rather than the claim being rewritten: editing the
    model's prose risks changing its meaning, while an explicit contradiction
    below it is unambiguous and auditable.

    Confidence is downgraded for the same reason the V0.7 critic downgrades it —
    an answer known to contain a false statement cannot honestly stay "high".
    """
    response: AgentResponse = result.response
    caveats = [
        *response.caveats,
        "NOT STORED: this answer says something was remembered, but no fact was "
        "written to memory. Please state it again if you need it kept.",
    ]
    return result.model_copy(
        update={
            "response": response.model_copy(
                update={"caveats": caveats, "confidence": Confidence.LOW}
            )
        }
    )
