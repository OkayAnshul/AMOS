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
    """A run that raised must count as a failure, not vanish from the denominator."""
    score = score_case(GoalCase(goal="g"), None, error="ProviderTimeoutError")
    assert not score.passed
    assert "ProviderTimeoutError" in score.failures[0]


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
