"""Running the evaluation suite.

Reports **deterministic and judged metrics separately, always.** They are not
comparable evidence: a deterministic check is reproducible and free, while a
judged score comes from a model in the same family as the one being judged.
Averaging them into one number would hide that difference, which is exactly what
a single headline score usually does.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from amos.agents.schemas import AgentResult
from amos.errors import AmosError
from amos.evaluation.judge import GroundednessJudge
from amos.evaluation.metrics import CaseScore, GoalCase, score_case
from amos.observability import log_event

logger = logging.getLogger(__name__)


@dataclass
class SuiteResult:
    scores: list[CaseScore] = field(default_factory=list)
    #: (score, judged). Unjudged entries are excluded from the mean rather than
    #: counted as zero.
    groundedness: list[tuple[float, bool]] = field(default_factory=list)
    judge_failures: int = 0

    @property
    def total(self) -> int:
        return len(self.scores)

    @property
    def measured(self) -> list[CaseScore]:
        """Cases that produced evidence about quality.

        A rate-limited case is excluded rather than counted as a failure: it says
        nothing about the system, and including it would make the score a
        measurement of the free tier.
        """
        return [s for s in self.scores if s.measured]

    @property
    def unmeasurable(self) -> int:
        return self.total - len(self.measured)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.measured if s.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.measured) if self.measured else 0.0

    def rate(self, attribute: str) -> float:
        """Pass rate for one deterministic check, over the cases it applies to."""
        applicable = self.measured
        if not applicable:
            return 0.0
        return sum(1 for s in applicable if getattr(s, attribute)) / len(applicable)

    @property
    def mean_groundedness(self) -> float | None:
        """None rather than 0.0 when nothing could be judged.

        Returning 0.0 would report a perfect-but-unjudged suite as maximally
        ungrounded, which is worse than reporting no number.
        """
        valid = [score for score, judged in self.groundedness if judged]
        return sum(valid) / len(valid) if valid else None

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.scores)

    def summary(self) -> str:
        lines = [
            f"cases            {self.passed}/{len(self.measured)} passed ({self.pass_rate:.0%})",
        ]
        if self.unmeasurable:
            lines.append(
                f"unmeasurable     {self.unmeasurable} "
                f"(rate-limited or timed out — excluded, not counted as failures)"
            )
        lines += [
            "",
            "deterministic:",
            f"  completion     {self.rate('completed'):.0%}",
            f"  output valid   {self.rate('output_valid'):.0%}",
            f"  tool selection {self.rate('tools_correct'):.0%}",
            f"  answer content {self.rate('answer_correct'):.0%}",
        ]
        refusal_cases = [
            s
            for s in self.measured
            if s.refused_correctly or "should have refused" in " ".join(s.failures)
        ]
        if refusal_cases:
            correct = sum(1 for s in refusal_cases if s.refused_correctly)
            lines.append(f"  refusal        {correct}/{len(refusal_cases)}")

        grounded = self.mean_groundedness
        lines += [
            "",
            "LLM-judged (weaker evidence — same model family as the judged system):",
            f"  groundedness   {grounded:.2f}" if grounded is not None else "  groundedness   n/a",
        ]
        if self.judge_failures:
            lines.append(f"  judge failures {self.judge_failures} (excluded from the mean)")
        lines += ["", f"cost             {self.total_tokens} tokens"]
        return "\n".join(lines)


class EvaluationHarness:
    """Runs goals and scores them."""

    def __init__(
        self,
        agent: object,
        judge: GroundednessJudge | None = None,
        *,
        pace_seconds: float = 0.0,
    ) -> None:
        self._agent = agent
        self._judge = judge
        # gemini-3.5-flash-lite is limited to 15 requests per MINUTE (a different
        # quota shape from flash's 20/day), and one case can cost several calls.
        # Firing them back to back guarantees a 429 partway through the suite —
        # which then looks like a quality failure unless paced.
        self._pace = pace_seconds

    async def run(self, cases: list[GoalCase]) -> SuiteResult:
        suite = SuiteResult()

        for index, case in enumerate(cases):
            if index and self._pace:
                await asyncio.sleep(self._pace)
            result: AgentResult | None = None
            error: str | None = None
            try:
                result = await self._agent.run(case.goal)  # type: ignore[attr-defined]
            except AmosError as exc:
                error = f"{type(exc).__name__}: {exc.message}"
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"

            score = score_case(case, result, error)
            suite.scores.append(score)
            log_event(logger, "eval.case", passed=score.passed, failures=len(score.failures))

            if self._judge is not None and result is not None:
                verdict = await self._judge.score(
                    case.goal, result.response.answer, _evidence(result)
                )
                suite.groundedness.append((verdict.score, verdict.judged))
                if not verdict.judged:
                    suite.judge_failures += 1

        return suite


def _evidence(result: AgentResult) -> list[str]:
    """Only what the run actually retrieved.

    The judge must not see the corpus — it would then be checking whether support
    exists somewhere, not whether the answer used it.
    """
    evidence: list[str] = []
    for outcome in result.tool_outcomes:
        if not outcome.succeeded or not outcome.output:
            continue
        passages = outcome.output.get("passages")
        if isinstance(passages, list):
            evidence.extend(
                f"{p.get('citation', '?')}: {str(p.get('content', ''))[:600]}"
                for p in passages
                if isinstance(p, dict)
            )
        else:
            evidence.append(f"{outcome.name}: {str(outcome.output)[:400]}")
    return evidence
