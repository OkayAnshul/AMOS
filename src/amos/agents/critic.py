"""The critic: judging whether an answer is supported by its evidence.

## Why the critic has no tools

A critic that can fetch new sources is doing research, and its verdict becomes
unfalsifiable — it can always go and find something that justifies whatever it
already concluded. Judging only what it was given is what makes the judgement
mean anything.

## Why the revise loop is bounded

Critic and producer can disagree forever. A critic that always finds something to
object to, paired with a producer that never satisfies it, is an infinite loop
that spends the daily quota in about ten minutes.

So revisions are capped in code (`max_revisions`, default 1). When the budget is
exhausted **the last answer is returned with the critic's objections attached as
caveats** — not discarded. A partially-supported answer plus an honest note about
what is unsupported is more useful than no answer, and hiding the objection would
be worse than either.

This mirrors the V0.4 rule: the loop bound is the guarantee, and the prompt is
only a request.
"""

from __future__ import annotations

import logging

from amos.agents.messages import CriticReport, Verdict
from amos.agents.registry import CRITIC
from amos.agents.schemas import AgentResponse, Confidence
from amos.llm.base import LLMCallRecord, LLMProvider, LLMRequest
from amos.observability import log_event

logger = logging.getLogger(__name__)


class Critic:
    """Validates an answer against the evidence that produced it."""

    def __init__(
        self, provider: LLMProvider, *, timeout: float = 60.0, temperature: float = 0.0
    ) -> None:
        self._provider = provider
        self._timeout = timeout
        self._temperature = temperature

    async def review(
        self,
        goal: str,
        answer: AgentResponse,
        evidence: list[str],
        calls: list[LLMCallRecord] | None = None,
    ) -> CriticReport:
        """Judge an answer. Never raises — a failed review accepts.

        Accepting on failure is deliberate. The critic is a quality gate, not a
        correctness requirement: if the reviewer itself breaks, blocking a
        correct answer is a worse outcome than letting an unreviewed one through.
        The alternative — failing the run because the *optional* validator failed
        — trades a real answer for a process complaint.
        """
        evidence_text = "\n\n".join(f"[{i + 1}] {e}" for i, e in enumerate(evidence))
        prompt = (
            f"Goal: {goal}\n\n"
            f"Proposed answer: {answer.answer}\n"
            f"Stated reasoning: {answer.reasoning}\n"
            f"Stated assumptions: {answer.assumptions}\n"
            f"Stated caveats: {answer.caveats}\n\n"
            f"Evidence available:\n{evidence_text or '(none)'}"
        )

        try:
            response = await self._provider.complete(
                LLMRequest(
                    prompt=prompt,
                    system_instruction=CRITIC.system_instruction,
                    response_schema=CriticReport,
                    temperature=self._temperature,
                ),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(logger, "critic.failed", error=type(exc).__name__)
            return CriticReport(
                verdict=Verdict.ACCEPT,
                reasoning=f"Review could not be performed ({type(exc).__name__}); accepted.",
            )

        if calls is not None:
            calls.append(
                LLMCallRecord(
                    provider=response.provider,
                    model=response.model,
                    prompt_tokens=response.prompt_tokens,
                    output_tokens=response.output_tokens,
                    latency_ms=response.latency_ms,
                )
            )

        if isinstance(response.parsed, CriticReport):
            log_event(
                logger,
                "critic.reviewed",
                verdict=response.parsed.verdict.value,
                unsupported=len(response.parsed.unsupported_claims),
            )
            return response.parsed

        return CriticReport(
            verdict=Verdict.ACCEPT,
            reasoning="Critic output failed validation; accepted rather than blocking.",
        )


def apply_report(answer: AgentResponse, report: CriticReport) -> AgentResponse:
    """Attach unresolved objections to the answer as caveats.

    Called when the revision budget is exhausted. The objections must reach the
    user: an answer the critic rejected, returned silently as if accepted, is
    worse than either accepting it openly or refusing.
    """
    if report.accepted:
        return answer

    caveats = list(answer.caveats)
    caveats.append(f"Unresolved review: {report.reasoning}")
    caveats.extend(f"Unsupported claim: {claim}" for claim in report.unsupported_claims)
    caveats.extend(f"Not addressed: {gap}" for gap in report.missing_from_answer)

    # NOTE: `model_copy(update=...)` does NOT re-validate, so passing the string
    # "low" here would leave `confidence` holding a str where the annotation says
    # Confidence — invisible until something does `is Confidence.LOW`. Pass the
    # enum member, not its value.
    return answer.model_copy(
        update={
            "caveats": caveats,
            # Downgraded, not preserved: an answer carrying unresolved objections
            # cannot honestly still be "high confidence".
            "confidence": Confidence.LOW,
        }
    )
