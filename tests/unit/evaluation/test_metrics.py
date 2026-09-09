"""The scorers.

The harness is tested because **a broken scorer produces confident numbers that
are wrong**, which is worse than having no numbers: it looks like evidence.
"""

from __future__ import annotations

import pytest

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.evaluation.cases import GOLDEN_GOALS
from amos.evaluation.metrics import GoalCase, score_case
from amos.tools.base import ToolOutcome, ToolStatus


def result(
    answer: str = "397.8",
    tools: list[ToolOutcome] | None = None,
    outcome: str = "COMPLETED",
) -> AgentResult:
    return AgentResult(
        request_id="r",
        response=AgentResponse(answer=answer, reasoning="x", confidence=Confidence.HIGH),
        tool_outcomes=tools or [],
        outcome=outcome,
        total_tokens=100,
    )


def tool(name: str, output: dict[str, object] | None = None) -> ToolOutcome:
    return ToolOutcome(
        call_id="c", name=name, status=ToolStatus.OK, output=output or {"result": 397.8}
    )


def retrieval(*citations: str) -> ToolOutcome:
    return ToolOutcome(
        call_id="c",
        name="search_knowledge",
        status=ToolStatus.OK,
        output={"passages": [{"citation": c, "content": "..."} for c in citations]},
    )


# ---------- passing ----------


def test_a_correct_run_passes() -> None:
    case = GoalCase(
        goal="17% of 2340?",
        expects_tools=frozenset({"calculator"}),
        expects_in_answer=("397.8",),
    )
    score = score_case(case, result(tools=[tool("calculator")]))

    assert score.passed
    assert score.failures == []


# ---------- each check fails independently ----------


def test_a_missing_tool_fails() -> None:
    """Catches a model doing arithmetic in its head instead of using the tool."""
    case = GoalCase(goal="g", expects_tools=frozenset({"calculator"}))
    score = score_case(case, result(tools=[]))

    assert not score.passed
    assert any("expected tools" in f for f in score.failures)


def test_extra_tools_are_not_a_failure() -> None:
    """Subset, not equality — an additional retrieval is not wrong."""
    case = GoalCase(goal="g", expects_tools=frozenset({"calculator"}))
    score = score_case(case, result(tools=[tool("calculator"), tool("read_file")]))
    assert score.tools_correct


def test_a_wrong_answer_fails() -> None:
    case = GoalCase(goal="g", expects_in_answer=("397.8",))
    score = score_case(case, result(answer="about 400"))
    assert any("missing" in f for f in score.failures)


def test_answer_matching_is_case_insensitive() -> None:
    """Asserting exact wording tests the wording and breaks on rephrasing."""
    case = GoalCase(goal="g", expects_in_answer=("JITTER",))
    assert score_case(case, result(answer="it uses jitter")).answer_correct


def test_a_missing_citation_fails() -> None:
    case = GoalCase(goal="g", expects_citations=frozenset({"03-adr.md"}))
    score = score_case(case, result(tools=[retrieval("99-other.md")]))
    assert any("citation" in f for f in score.failures)


def test_the_right_citation_passes() -> None:
    case = GoalCase(goal="g", expects_citations=frozenset({"03-adr.md"}))
    score = score_case(case, result(tools=[retrieval("03-adr.md", "99-other.md")]))
    assert score.citations_present


def test_an_empty_answer_is_invalid() -> None:
    assert not score_case(GoalCase(goal="g"), result(answer="   ")).output_valid


def test_a_failed_outcome_fails() -> None:
    score = score_case(GoalCase(goal="g"), result(outcome="FAILED"))
    assert not score.completed


def test_partial_completion_still_counts_as_completed() -> None:
    """Some tasks succeeding is a real outcome (V0.4), not a failure."""
    assert score_case(GoalCase(goal="g"), result(outcome="PARTIALLY_COMPLETED")).completed


def test_a_crashed_run_is_scored_not_skipped() -> None:
    """A run that raised must not vanish silently.

    Note it is scored as *unmeasurable* rather than *failed* here, because a
    timeout is an infrastructure limit — see the parametrised test below. A
    genuine crash (a ValueError, say) still counts as a failure.
    """
    score = score_case(GoalCase(goal="g"), None, error="ProviderTimeoutError")
    assert not score.passed
    assert score.unmeasurable is not None


# ---------- refusal: the case that matters most ----------


@pytest.mark.parametrize(
    "answer",
    [
        "The documentation does not mention a Kubernetes autoscaling policy.",
        "I could not find that in the indexed documents.",
        "No information about this is present in the corpus.",
    ],
)
def test_a_refusal_is_recognised(answer: str) -> None:
    score = score_case(GoalCase(goal="g", expects_refusal=True), result(answer=answer))
    assert score.refused_correctly


def test_a_confident_invention_fails_the_refusal_case() -> None:
    """The failure RAG exists to prevent, and the one a user is least able to
    detect."""
    score = score_case(
        GoalCase(goal="g", expects_refusal=True),
        result(answer="AMOS runs 3 replicas with HPA scaling at 70% CPU."),
    )
    assert not score.refused_correctly
    assert any("should have refused" in f for f in score.failures)


# ---------- the golden set itself ----------


def test_the_golden_set_covers_the_failure_modes_that_matter() -> None:
    assert any(c.expects_refusal for c in GOLDEN_GOALS), "no refusal case"
    assert any(c.expects_citations for c in GOLDEN_GOALS), "no citation case"
    assert any("calculator" in c.expects_tools for c in GOLDEN_GOALS), "no tool case"


def test_golden_cases_are_checkable_without_reading_the_answer() -> None:
    """Every case must assert something a script can verify — otherwise scoring
    it means a human judging prose, which does not scale and is not reproducible."""
    for case in GOLDEN_GOALS:
        assert (
            case.expects_tools
            or case.expects_in_answer
            or case.expects_citations
            or case.expects_refusal
        ), f"case has no checkable expectation: {case.goal}"


def test_the_golden_set_is_small_enough_to_actually_run() -> None:
    """A suite that cannot be run against a 20-request/day quota is not a gate."""
    assert len(GOLDEN_GOALS) <= 10


# ---------- a rate limit is not a quality failure ----------


@pytest.mark.parametrize(
    "error",
    [
        "ProviderRateLimitError: quota exceeded",
        "ProviderTimeoutError: timed out",
        "Gemini quota exceeded. 429 RESOURCE_EXHAUSTED",
    ],
)
def test_infrastructure_errors_are_unmeasurable_not_failures(error: str) -> None:
    """Scoring a rate limit as a failure makes the metric say "the system
    answered badly" when it means "we could not measure"."""
    score = score_case(GoalCase(goal="g"), None, error=error)

    assert not score.measured
    assert score.unmeasurable is not None
    assert score.failures == [], "an infrastructure limit is not a quality defect"


def test_a_real_error_is_still_a_failure() -> None:
    """The distinction must not become an excuse — a genuine crash still counts."""
    score = score_case(GoalCase(goal="g"), None, error="ValueError: bad plan")

    assert score.measured
    assert not score.passed
    assert score.failures


# ---------- the refusal detector's own brittleness ----------


@pytest.mark.parametrize(
    "answer",
    [
        # The phrasing the first version MISSED, taken verbatim from a live run.
        "According to the AMOS documentation, Kubernetes is listed under the roadmap "
        "for beyond V1.0. Therefore, AMOS does not have a Kubernetes autoscaling "
        "policy or configured replica counts.",
        "AMOS is not deployed anywhere and there is no autoscaling configuration.",
        "No such policy exists in the documentation.",
        "This is not implemented and not planned.",
        "The documentation does not define replica counts.",
    ],
)
def test_real_refusal_phrasings_are_recognised(answer: str) -> None:
    """Regression cases from actual model output.

    The first detector scored a correct refusal as a failure because the model
    phrased it in a way the marker list did not cover — a metric manufacturing a
    false finding. Every phrasing seen in a live run gets pinned here.
    """
    score = score_case(GoalCase(goal="g", expects_refusal=True), result(answer=answer))
    assert score.refused_correctly, f"missed refusal phrasing: {answer[:60]}"


@pytest.mark.parametrize(
    "answer",
    [
        "AMOS runs 3 replicas with HPA scaling at 70% CPU utilisation.",
        "The autoscaling policy targets 80% memory with a minimum of 2 pods.",
    ],
)
def test_confident_inventions_are_still_caught(answer: str) -> None:
    """Broadening the markers must not make everything look like a refusal —
    otherwise the metric passes the failure it exists to catch."""
    score = score_case(GoalCase(goal="g", expects_refusal=True), result(answer=answer))
    assert not score.refused_correctly
