"""Quoted findings, checked in code (ADR-022) — no model, no network."""

from __future__ import annotations

from amos.agents.findings import check_findings, normalise_text
from amos.agents.schemas import AgentResponse, AgentResult, Confidence, Finding, FindingStatus
from amos.orchestration.executor import _findings_block
from amos.tools.base import ToolOutcome, ToolStatus

PAGE = "https://www.goibibo.com/hotels/the-hosteller-anjuna/"
OTHER = "https://example.org/goa-food"


def page(url: str, text: str) -> ToolOutcome:
    return ToolOutcome(
        call_id="c",
        name="fetch_page",
        arguments={"url": url},
        status=ToolStatus.OK,
        output={"url": url, "final_url": url, "title": "", "text": text},
    )


READ = [
    page(
        PAGE, "The Hosteller Goa Anjuna. Dorm beds from Rs. 933 per night; private rooms Rs 2,499."
    ),
    page(OTHER, "A thali at Ritz Classic costs about ₹350 — the fish thali is the best seller."),
    ToolOutcome(
        call_id="s",
        name="web_search",
        arguments={},
        status=ToolStatus.OK,
        output={
            "results": [
                {
                    "url": "https://zostel.com/anjuna",
                    "title": "Zostel Anjuna",
                    "snippet": "Dorms from ₹650/night",
                }
            ]
        },
    ),
]


def one(fact: str, quote: str, source: str) -> FindingStatus:
    (checked,) = check_findings([Finding(fact=fact, quote=quote, source=source)], READ)
    return checked.status


def test_a_quote_on_its_page_with_its_figure_is_checked() -> None:
    assert (
        one("The Hosteller Anjuna: dorm ₹933 a night", "Dorm beds from Rs. 933 per night", PAGE)
        is FindingStatus.CHECKED
    )


def test_currency_spacing_case_and_thousands_separators_do_not_matter() -> None:
    assert one("Private room ₹2499", "PRIVATE ROOMS ₹2499", PAGE) is FindingStatus.CHECKED
    assert normalise_text("Rs. 2,499") == normalise_text("₹2499")


def test_a_paraphrase_is_not_a_quote() -> None:
    assert one("Dorm ₹933", "dorm beds cost 933 rupees", PAGE) is FindingStatus.QUOTE_NOT_FOUND


def test_a_figure_the_quote_does_not_contain_fails() -> None:
    """The quote is real, but the fact's number was not taken from it."""
    assert (
        one("Dorm ₹899 a night", "Dorm beds from Rs. 933 per night", PAGE)
        is FindingStatus.FIGURE_NOT_IN_QUOTE
    )


def test_a_quote_on_a_different_page_is_found_and_reattributed() -> None:
    (checked,) = check_findings(
        [
            Finding(
                fact="Ritz Classic thali ₹350",
                quote="A thali at Ritz Classic costs about ₹350",
                source=PAGE,
            )
        ],
        READ,
    )
    assert checked.status is FindingStatus.CHECKED
    assert checked.source_seen is True and checked.found_in == OTHER


def test_a_source_the_task_never_read_is_marked() -> None:
    (checked,) = check_findings(
        [
            Finding(
                fact="Zostel dorm ₹650",
                quote="Dorms from ₹650/night",
                source="https://never-read.example",
            )
        ],
        READ,
    )
    assert checked.status is FindingStatus.CHECKED and checked.source_seen is False
    assert checked.found_in == "https://zostel.com/anjuna", (
        "found in a search snippet the task did see"
    )


def test_an_elided_quote_needs_each_part_in_order() -> None:
    assert (
        one("Dorm ₹933", "The Hosteller Goa Anjuna ... Rs. 933 per night", PAGE)
        is FindingStatus.CHECKED
    )
    assert (
        one("Dorm ₹933", "Rs. 933 per night ... The Hosteller Goa Anjuna", PAGE)
        is FindingStatus.QUOTE_NOT_FOUND
    )


def test_a_tiny_quote_proves_nothing() -> None:
    assert one("₹933", "933", PAGE) is FindingStatus.QUOTE_NOT_FOUND


def test_the_hand_off_separates_facts_from_unconfirmed_claims() -> None:
    findings = [
        Finding(fact="Dorm ₹933", quote="Dorm beds from Rs. 933 per night", source=PAGE),
        Finding(fact="Dorm ₹899", quote="made up words here", source=PAGE),
    ]
    result = AgentResult(
        request_id="r",
        response=AgentResponse(
            answer="a", reasoning="r", confidence=Confidence.HIGH, findings=findings
        ),
        findings=check_findings(findings, READ),
    )
    block = _findings_block(result)
    facts, doubts = block.split("Not confirmed")
    assert "Dorm ₹933" in facts and PAGE in facts
    assert "Dorm ₹899" in doubts and "Dorm ₹899" not in facts
