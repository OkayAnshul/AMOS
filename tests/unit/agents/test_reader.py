"""The reader step (ADR-022): one focused call over what a task read — no network."""

from __future__ import annotations

from amos.agents.findings import check_findings
from amos.agents.reader import ReaderOutput, material, merge, read_findings
from amos.agents.schemas import Finding, FindingStatus
from amos.errors import ProviderUnavailableError
from amos.llm.base import LLMRequest, LLMResponse
from amos.tools.base import ToolOutcome, ToolStatus

URL = "https://www.hostelz.com/cheapest-hostels-in-goa"
PAGE_TEXT = (
    "Sorted by Price: The Cheapest Hostels in Goa. Hippie Hostel Anjuna - from $1.72. "
    "Tamasha Goa Vagator - from $2.16. goSTOPS Goa Baga - from $2.49."
)
READ = [
    ToolOutcome(
        call_id="p",
        name="fetch_page",
        arguments={},
        status=ToolStatus.OK,
        output={"url": URL, "final_url": URL, "title": "Cheapest hostels", "text": PAGE_TEXT},
    ),
    ToolOutcome(
        call_id="s",
        name="web_search",
        arguments={},
        status=ToolStatus.OK,
        output={"results": [{"url": URL, "title": "Cheapest hostels", "snippet": "17 hostels"}]},
    ),
]


class Stub:
    name = "stub"

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return LLMResponse(text="", parsed=self.outcome, model="m", provider="stub")


def test_the_material_is_each_text_once_pages_first_and_labelled() -> None:
    text = material(READ)
    assert text.count("Hippie Hostel Anjuna") == 1, "url and final_url are one page"
    assert text.index(PAGE_TEXT[:20]) < text.index("17 hostels"), "pages before snippets"
    assert f"[source: {URL}]" in text


async def test_the_reader_finds_what_the_researcher_left_out_and_code_checks_it() -> None:
    found = ReaderOutput(
        findings=[
            Finding(
                fact="Tamasha Goa Vagator: from $2.16",
                quote="Tamasha Goa Vagator - from $2.16",
                source=URL,
            ),
            Finding(fact="Made-up Hostel: $1", quote="Made-up Hostel - from $1.00", source=URL),
        ]
    )
    stub = Stub(found)
    findings = await read_findings(stub, "find hostels with prices", READ, timeout=10)
    checked = check_findings(findings, READ)
    assert [c.status for c in checked] == [FindingStatus.CHECKED, FindingStatus.QUOTE_NOT_FOUND]
    assert "find hostels with prices" in (stub.requests[0].prompt or "")
    assert stub.requests[0].response_schema is ReaderOutput


async def test_a_failing_reader_costs_the_task_nothing() -> None:
    assert await read_findings(Stub(ProviderUnavailableError("503")), "t", READ, timeout=10) == []


async def test_a_task_that_read_nothing_makes_no_reader_call() -> None:
    stub = Stub(ReaderOutput())
    assert await read_findings(stub, "t", [], timeout=10) == []
    assert stub.requests == []


def test_merge_keeps_the_researchers_findings_first_and_each_quote_once() -> None:
    own = [Finding(fact="a", quote="Hippie Hostel Anjuna - from $1.72", source=URL)]
    extra = [
        Finding(fact="a again", quote="hippie hostel anjuna -  from $1.72", source=URL),
        Finding(fact="b", quote="goSTOPS Goa Baga - from $2.49", source=URL),
    ]
    assert [f.fact for f in merge(own, extra)] == ["a", "b"]


def test_an_overlapping_quote_is_the_same_finding() -> None:
    own = [
        Finding(
            fact="Mister Merchants ₹2,000",
            quote="Price: Rs.2,000 per person Must try: Mutton Curry",
            source=URL,
        )
    ]
    extra = [
        Finding(
            fact="Mister Merchants: Rs.2,000",
            quote="Price: Rs.2,000 per person Must try: Mutton Curry Address: Moide",
            source=URL,
        )
    ]
    assert merge(own, extra) == own
