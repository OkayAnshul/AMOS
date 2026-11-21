"""The writer's citations are checked in code against what the run saw (ADR-020)."""

from __future__ import annotations

from amos.agents.sourcing import cited, normalise, sources_in, unseen

DELIVERABLE = """## Day 1
Dorm bed in Anjuna: ₹500 (source: https://www.hostelworld.com/goa/).
Thali lunch: ₹150 (source: https://goanearby.in/budget-eats; https://made-up.example/food)

## Not sourced
- Carry cash for small vendors.
"""


def test_markers_and_bare_urls_are_both_citations() -> None:
    found = cited(DELIVERABLE + "\nSee also https://encore.dev/articles/postgres-listen-notify.")
    assert "hostelworld.com/goa" in found
    assert "goanearby.in/budget-eats" in found
    assert "made-up.example/food" in found
    assert "encore.dev/articles/postgres-listen-notify" in found


def test_urls_compare_without_scheme_www_slash_or_fragment() -> None:
    assert normalise("https://www.Example.com/a/#top") == normalise("http://example.com/a")


def test_a_source_the_run_never_saw_is_reported() -> None:
    seen = {"https://hostelworld.com/goa", "https://goanearby.in/budget-eats"}
    assert unseen(DELIVERABLE, seen) == ["made-up.example/food"]


def test_a_document_cited_by_name_matches_its_path() -> None:
    text = "Chosen for consistency (source: 03-architecture-decisions.md)."
    assert unseen(text, {"docs/03-architecture-decisions.md"}) == []
    assert unseen(text, {"18-deployment.md"}) == ["03-architecture-decisions.md"]


def test_the_ledger_takes_sources_from_each_kind_of_tool() -> None:
    assert sources_in({"results": [{"url": "https://a.example"}]}, "web_search") == {
        "https://a.example"
    }
    assert sources_in(
        {"url": "https://b.example", "final_url": "https://b.example/x"}, "fetch_page"
    ) == {
        "https://b.example",
        "https://b.example/x",
    }
    assert sources_in({"passages": [{"citation": "10-rag.md"}]}, "search_knowledge") == {
        "10-rag.md"
    }
    assert sources_in({"value": 4}, "calculator") == set()


def test_a_task_reference_is_a_citation_when_the_task_exists() -> None:
    text = "Total ₹6,141 (source: t4)."
    assert unseen(text, {"t4"}) == []
    assert unseen(text, {"t1"}) == ["t4"]


def test_a_tables_source_column_counts_as_citations() -> None:
    """A live run reported "cited 0" for a fully sourced table (ADR-022)."""
    table = (
        "| Time | What | Cost | Source |\n"
        "|------|------|------|--------|\n"
        "| 9:00 | Dorm | ₹933 | https://www.goibibo.com/h/ |\n"
        "| 13:00 | Thali | ₹350 | [Ritz](https://example.org/food) |\n"
        "| – | Total | ₹1,283 | t3 |\n"
        "| – | Note | – | – |\n"
    )
    assert cited(table) == {"goibibo.com/h", "example.org/food", "t3"}


def test_a_table_without_a_source_column_cites_nothing() -> None:
    assert cited("| a | b |\n|---|---|\n| t1 | x |\n") == set()
