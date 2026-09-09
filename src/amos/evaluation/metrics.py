"""Deterministic scorers.

**These are preferred over LLM-judged metrics wherever a question can be settled
by code**, and most can be. Whether the output validated, whether the expected
tool ran, whether citations are present, whether the run completed — all are
facts in the trace, not opinions.

That ordering matters because an LLM judge shares failure modes with the model it
is judging (`judge.py`). A deterministic check is reproducible, free, and cannot
be talked into agreeing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from amos.agents.schemas import AgentResult


@dataclass
class GoalCase:
    """One end-to-end evaluation case.

    Expectations are deliberately *loose and checkable* rather than exact-match on
    the answer text. Asserting a model's exact wording tests the wording, and
    breaks on every harmless rephrasing — the classic way an LLM test suite
    becomes noise everyone learns to ignore.
    """

    goal: str
    #: Tools the run should use. A subset check, not equality — an extra
    #: retrieval is not a failure.
    expects_tools: frozenset[str] = frozenset()
    #: Substrings the answer should contain (case-insensitive). Used for facts
    #: with one correct value, like an arithmetic result.
    expects_in_answer: tuple[str, ...] = ()
    #: Sources that should be cited if retrieval ran.
    expects_citations: frozenset[str] = frozenset()
    #: True when the honest answer is "I could not find that".
    expects_refusal: bool = False
    note: str = ""


@dataclass
class CaseScore:
    goal: str
    completed: bool = False
    output_valid: bool = False
    tools_correct: bool = False
    answer_correct: bool = False
    citations_present: bool = False
    refused_correctly: bool = False
    tokens: int = 0
    latency_ms: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Every applicable deterministic check succeeded."""
        return not self.failures


def score_case(case: GoalCase, result: AgentResult | None, error: str | None = None) -> CaseScore:
    """Score one run against its expectations. Never raises."""
    score = CaseScore(goal=case.goal)

    if result is None:
        score.failures.append(f"run failed: {error or 'unknown error'}")
        return score

    score.tokens = result.total_tokens
    score.latency_ms = result.latency_ms
    score.completed = result.outcome in ("COMPLETED", "PARTIALLY_COMPLETED")
    if not score.completed:
        score.failures.append(f"outcome was {result.outcome}")

    # Output validity is structural: it is an AgentResponse with a non-empty
    # answer. That the model returned *something* is not the same as valid.
    score.output_valid = bool(result.response.answer.strip())
    if not score.output_valid:
        score.failures.append("empty answer")

    used = {outcome.name for outcome in result.tool_outcomes if outcome.succeeded}
    score.tools_correct = case.expects_tools <= used
    if not score.tools_correct:
        score.failures.append(f"expected tools {sorted(case.expects_tools)}, used {sorted(used)}")

    answer = result.response.answer.lower()
    missing = [s for s in case.expects_in_answer if s.lower() not in answer]
    score.answer_correct = not missing
    if missing:
        score.failures.append(f"answer missing {missing}")

    if case.expects_citations:
        cited = _cited_sources(result)
        score.citations_present = bool(case.expects_citations & cited)
        if not score.citations_present:
            score.failures.append(
                f"expected a citation from {sorted(case.expects_citations)}, cited {sorted(cited)}"
            )

    if case.expects_refusal:
        score.refused_correctly = _looks_like_refusal(result.response.answer)
        if not score.refused_correctly:
            score.failures.append("should have refused but produced a confident answer")

    return score


def _cited_sources(result: AgentResult) -> set[str]:
    sources: set[str] = set()
    for outcome in result.tool_outcomes:
        if not outcome.succeeded or not outcome.output:
            continue
        passages = outcome.output.get("passages")
        if isinstance(passages, list):
            sources.update(
                str(p["citation"]) for p in passages if isinstance(p, dict) and p.get("citation")
            )
    return sources


#: Phrases that indicate the model declined rather than invented an answer.
#: A keyword check is crude, and deliberately so: the alternative is asking a
#: model whether a model refused, which is circular.
_REFUSAL_MARKERS = (
    "not find",
    "does not",
    "do not have",
    "no information",
    "not mention",
    "not contain",
    "not specified",
    "not documented",
    "cannot find",
    "unable to find",
    "no relevant",
)


def _looks_like_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)
