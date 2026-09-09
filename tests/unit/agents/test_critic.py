"""The critic, and the bound on its argument with the producer."""

from __future__ import annotations

import json

from amos.agents.critic import Critic, apply_report
from amos.agents.messages import CriticReport, Verdict
from amos.agents.schemas import AgentResponse, Confidence
from amos.errors import ProviderTimeoutError
from amos.llm.fake import AlwaysFailsProvider, FakeProvider


def answer(text: str = "the answer", confidence: Confidence = Confidence.HIGH) -> AgentResponse:
    return AgentResponse(answer=text, reasoning="because", confidence=confidence)


def report_json(verdict: str, **extra: object) -> str:
    payload: dict[str, object] = {"verdict": verdict, "reasoning": "r"}
    payload.update(extra)
    return json.dumps(payload)


async def test_accepts_a_supported_answer() -> None:
    critic = Critic(FakeProvider([report_json("accept")]))
    report = await critic.review("goal", answer(), ["evidence"])
    assert report.accepted


async def test_rejects_and_names_the_unsupported_claims() -> None:
    """A verdict alone gives the producer nothing to act on."""
    critic = Critic(
        FakeProvider([report_json("revise", unsupported_claims=["X is not in any source"])])
    )
    report = await critic.review("goal", answer(), [])
    assert not report.accepted
    assert report.unsupported_claims == ["X is not in any source"]


async def test_a_broken_critic_accepts_rather_than_blocking() -> None:
    """The critic is a quality gate, not a correctness requirement. Failing the
    run because the optional validator broke trades a real answer for a process
    complaint."""
    critic = Critic(AlwaysFailsProvider(ProviderTimeoutError("timed out")))
    report = await critic.review("goal", answer(), ["evidence"])
    assert report.accepted
    assert "could not be performed" in report.reasoning


async def test_unparseable_critic_output_accepts() -> None:
    critic = Critic(FakeProvider(["not json"]))
    assert (await critic.review("goal", answer(), [])).accepted


async def test_critic_records_its_token_cost() -> None:
    """Review is not free; it must show up in the trace."""
    calls: list[object] = []
    await Critic(FakeProvider([report_json("accept")])).review(
        "goal",
        answer(),
        [],
        calls,  # type: ignore[arg-type]
    )
    assert len(calls) == 1


# ---------- unresolved objections ----------


def test_unresolved_objections_are_attached_as_caveats() -> None:
    """An answer the critic rejected, returned silently as if accepted, is worse
    than either accepting it openly or refusing."""
    report = CriticReport(
        verdict=Verdict.REVISE,
        reasoning="two claims unsupported",
        unsupported_claims=["claim A"],
        missing_from_answer=["the second half of the question"],
    )
    revised = apply_report(answer(), report)

    assert any("Unresolved review" in c for c in revised.caveats)
    assert any("claim A" in c for c in revised.caveats)
    assert any("second half" in c for c in revised.caveats)


def test_confidence_is_downgraded_when_objections_stand() -> None:
    """An answer carrying unresolved objections cannot honestly remain high
    confidence."""
    report = CriticReport(verdict=Verdict.REVISE, reasoning="unsupported")
    revised = apply_report(answer(confidence=Confidence.HIGH), report)
    assert revised.confidence is Confidence.LOW


def test_an_accepted_answer_is_returned_untouched() -> None:
    original = answer()
    accepted = CriticReport(verdict=Verdict.ACCEPT, reasoning="fine")
    assert apply_report(original, accepted) is original
