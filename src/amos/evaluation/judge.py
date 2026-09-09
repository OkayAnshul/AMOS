"""LLM-as-judge, and its limits.

## The honest problem

The judge is the same family of model as the one being judged. It shares training
data, shares blind spots, and is agreeable by construction — a model asked "is
this answer supported?" tends to say yes. **A judged score is weaker evidence
than a deterministic check, and every judged number in AMOS is reported as such.**

So this module exists for exactly one metric — **groundedness** — because it is
the one question that cannot be settled by code: *is this answer actually
supported by the passages that were retrieved?* String overlap does not answer
it (a correct paraphrase shares few words; a fabrication can share many).

Three constraints that make the judgement less bad:

1. **The judge sees only the retrieved evidence**, never the corpus. It cannot go
   looking for support the answer never had — the same reason the V0.7 critic has
   no tools.
2. **Temperature 0**, so a score is at least reproducible.
3. **It is asked to name unsupported claims**, not just to score. A judge that
   must point at specific text has less room to be vaguely agreeable, and the
   output is checkable by a human.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from amos.llm.base import LLMProvider, LLMRequest
from amos.observability import log_event

logger = logging.getLogger(__name__)

JUDGE_INSTRUCTION = """You judge whether an answer is supported by the evidence given.

You are NOT judging whether the answer is true in general. You are judging whether
each claim it makes appears in the evidence provided. A claim that is true but
absent from the evidence is UNSUPPORTED.

Score 0.0 to 1.0:
  1.0  every claim appears in the evidence
  0.5  the main claim is supported, some details are not
  0.0  the answer is not supported by this evidence at all

If the evidence is empty and the answer declines to answer, that is 1.0 — declining
when there is nothing to cite is correct behaviour, not a failure.

Name the specific unsupported claims. "Somewhat supported" is useless.
"""


class GroundednessVerdict(BaseModel):
    """A judgement, or an explicit record that judging failed.

    `judged` is a separate boolean rather than a sentinel value in `score`. The
    first attempt used `score = -1` for "the judge broke", which the field's own
    `ge=0.0` bound rejected outright — and which would have been poor design even
    if it had worked, because a magic number in a numeric field gets averaged by
    accident. An unjudged case must be *excluded* from the mean, and that is
    easier to get right when it is a different field.
    """

    score: float = Field(default=0.0, ge=0.0, le=1.0)
    judged: bool = Field(
        default=True, description="False when the judge failed; excluded from aggregates."
    )
    unsupported_claims: list[str] = Field(default_factory=list, max_length=10)
    reasoning: str = Field(default="", max_length=1000)


class GroundednessJudge:
    """Scores whether an answer is supported by its evidence."""

    def __init__(self, provider: LLMProvider, *, timeout: float = 60.0) -> None:
        self._provider = provider

        self._timeout = timeout

    async def score(self, goal: str, answer: str, evidence: list[str]) -> GroundednessVerdict:
        """Judge one answer. Never raises.

        A failed judgement returns `judged=False` and is excluded from
        aggregates, rather than silently counting as a pass or a fail. A metric
        that quietly absorbs its own failures is worse than one reporting fewer
        samples.
        """
        evidence_text = "\n\n".join(f"[{i + 1}] {e[:800]}" for i, e in enumerate(evidence))
        prompt = (
            f"Goal: {goal}\n\nAnswer to judge:\n{answer}\n\n"
            f"Evidence available to the answerer:\n{evidence_text or '(none)'}"
        )
        try:
            response = await self._provider.complete(
                LLMRequest(
                    prompt=prompt,
                    system_instruction=JUDGE_INSTRUCTION,
                    response_schema=GroundednessVerdict,
                    temperature=0.0,
                ),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(logger, "judge.failed", error=type(exc).__name__)
            return GroundednessVerdict(
                judged=False, reasoning=f"judge failed: {type(exc).__name__}"
            )

        if isinstance(response.parsed, GroundednessVerdict):
            return response.parsed
        return GroundednessVerdict(judged=False, reasoning="judge output failed validation")
