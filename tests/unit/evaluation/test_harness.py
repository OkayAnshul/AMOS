"""The harness, and the separation it maintains between kinds of evidence."""

from __future__ import annotations

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.errors import ProviderTimeoutError
from amos.evaluation.harness import EvaluationHarness, SuiteResult
from amos.evaluation.judge import GroundednessJudge, GroundednessVerdict
from amos.evaluation.metrics import CaseScore, GoalCase
from amos.llm.fake import FakeProvider


class StubAgent:
    def __init__(self, answer: str = "397.8", raises: Exception | None = None) -> None:
        self.answer = answer
        self.raises = raises

    async def run(self, goal: str) -> AgentResult:
        if self.raises:
            raise self.raises
        return AgentResult(
            request_id="r",
            response=AgentResponse(answer=self.answer, reasoning="x", confidence=Confidence.HIGH),
            total_tokens=50,
        )


async def test_the_suite_scores_every_case() -> None:
    cases = [GoalCase(goal="a", expects_in_answer=("397.8",)), GoalCase(goal="b")]
    suite = await EvaluationHarness(StubAgent()).run(cases)

    assert suite.total == 2
    assert suite.passed == 2


async def test_a_crashing_case_is_counted_as_failed_not_skipped() -> None:
    """Otherwise a suite that crashes on half its cases reports 100%."""
    suite = await EvaluationHarness(StubAgent(raises=ProviderTimeoutError("x"))).run(
        [GoalCase(goal="a")]
    )
    assert suite.total == 1
    assert suite.passed == 0


async def test_one_crashing_case_does_not_abort_the_suite() -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, goal: str) -> AgentResult:
            self.calls += 1
            if self.calls == 1:
                raise ProviderTimeoutError("x")
            return await StubAgent().run(goal)

    suite = await EvaluationHarness(Flaky()).run([GoalCase(goal="a"), GoalCase(goal="b")])
    assert suite.total == 2, "the second case still ran"


# ---------- keeping the two kinds of evidence apart ----------


def test_deterministic_and_judged_metrics_are_reported_separately() -> None:
    """They are not comparable evidence, and one headline number would hide that."""
    suite = SuiteResult(scores=[CaseScore(goal="g", completed=True)], groundedness=[(0.9, True)])
    summary = suite.summary()

    assert "deterministic:" in summary
    assert "LLM-judged" in summary
    assert "same model family" in summary, "the caveat must travel with the number"


def test_a_failed_judgement_is_excluded_rather_than_counted() -> None:
    """A metric that quietly absorbs its own failures is worse than one reporting
    fewer samples."""
    suite = SuiteResult(groundedness=[(1.0, True), (0.0, False), (0.5, True)])
    assert suite.mean_groundedness == 0.75


def test_groundedness_is_none_when_nothing_could_be_judged() -> None:
    """Reporting 0.0 would present a perfect-but-unjudged suite as maximally
    ungrounded."""
    assert SuiteResult(groundedness=[(0.0, False), (0.0, False)]).mean_groundedness is None


async def test_the_judge_sees_only_what_the_run_retrieved() -> None:
    """It must not be able to find support the answer never had — the same
    reason the V0.7 critic has no tools."""
    judge = GroundednessJudge(FakeProvider(['{"score": 1.0, "reasoning": "ok"}']))
    suite = await EvaluationHarness(StubAgent(), judge).run([GoalCase(goal="a")])

    assert suite.groundedness == [(1.0, True)]
    # No retrieval happened, so the evidence handed to the judge was empty.
    assert "(none)" in judge._provider.calls[0].prompt  # type: ignore[attr-defined]


async def test_a_broken_judge_does_not_break_the_suite() -> None:
    judge = GroundednessJudge(FakeProvider(["not valid json"]))
    suite = await EvaluationHarness(StubAgent(), judge).run([GoalCase(goal="a")])

    assert suite.judge_failures == 1
    assert suite.mean_groundedness is None
    assert suite.total == 1, "deterministic scoring still happened"


def test_a_verdict_carries_its_unsupported_claims() -> None:
    verdict = GroundednessVerdict(
        score=0.5, unsupported_claims=["the replica count"], reasoning="partly"
    )
    assert verdict.unsupported_claims == ["the replica count"]
