"""Is each finding's quote on the page it came from? Checked in code (ADR-022).

A researcher returns findings — a fact, the exact words that state it, and the
source. After the task succeeds, every quote is looked for in the text that task
actually read: pages (`fetch_page`), search snippets (`web_search`) and document
passages (`search_knowledge`). The fact's numbers must be in the quote too.

What this proves is narrow and exact: the words are on that page, and the figure
is in those words. Whether the page is right is not something code can know.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from amos.agents.schemas import CheckedFinding, Finding, FindingStatus
from amos.agents.sourcing import normalise
from amos.tools.base import ToolOutcome

#: A quote shorter than this, once normalised, proves nothing.
MIN_QUOTE_CHARS = 6

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_CURRENCY = re.compile(r"\b(?:rs\.?|inr)\s*(?=\d)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{2,3}\b)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalise_text(text: str) -> str:
    """Comparable text: case, curly quotes, dashes, `Rs.`/`INR`/`₹`, thousands
    separators, markdown emphasis and spacing all made uniform."""
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).lower()
    text = _CURRENCY.sub("₹", text)
    text = re.sub(r"₹\s+", "₹", text)
    text = _THOUSANDS.sub("", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"['\"]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def numbers_in(text: str) -> set[str]:
    return set(_NUMBER.findall(normalise_text(text)))


def read_texts(outcomes: Iterable[ToolOutcome]) -> list[tuple[str, str]]:
    """(source, text) for everything a task read, one entry per page, result or passage."""
    texts: list[tuple[str, str]] = []
    for outcome in outcomes:
        output = outcome.output if outcome.succeeded else None
        if not isinstance(output, dict):
            continue
        if outcome.name == "fetch_page":
            body = f"{output.get('title', '')}\n{output.get('text', '')}"
            for key in ("url", "final_url"):
                if output.get(key):
                    texts.append((str(output[key]), body))
        elif outcome.name == "web_search":
            for result in output.get("results", []):
                if isinstance(result, dict) and result.get("url"):
                    texts.append(
                        (
                            str(result["url"]),
                            f"{result.get('title', '')} {result.get('snippet', '')}",
                        )
                    )
        elif outcome.name == "search_knowledge":
            for passage in output.get("passages", []):
                if isinstance(passage, dict) and passage.get("citation"):
                    texts.append(
                        (
                            str(passage["citation"]),
                            f"{passage.get('heading', '')}\n{passage.get('content', '')}",
                        )
                    )
    return texts


def _same_source(cited: str, read: str) -> bool:
    a, b = normalise(cited), normalise(read)
    return bool(a) and (a == b or a.endswith(f"/{b}") or b.endswith(f"/{a}"))


def _contains(text: str, quote: str) -> bool:
    """The quote is in the text; an elided quote ("a … b") needs each part, in order."""
    parts = [p.strip() for p in re.split(r"\.\.\.|…", quote) if len(p.strip()) >= 4]
    position = 0
    for part in parts or [quote]:
        found = text.find(part, position)
        if found == -1:
            return False
        position = found + len(part)
    return True


def check(finding: Finding, texts: list[tuple[str, str]]) -> CheckedFinding:
    quote = normalise_text(finding.quote)
    cited_reads = [(src, body) for src, body in texts if _same_source(finding.source, src)]
    found_in: str | None = None
    if len(quote) >= MIN_QUOTE_CHARS:
        # The cited source first; then anything else the task read, so a fact
        # attributed to the wrong page is still found — and re-attributed.
        for src, body in [*cited_reads, *(t for t in texts if t not in cited_reads)]:
            if _contains(normalise_text(body), quote):
                found_in = src
                break
    if found_in is None:
        status = FindingStatus.QUOTE_NOT_FOUND
    elif not numbers_in(finding.fact) <= numbers_in(finding.quote):
        status = FindingStatus.FIGURE_NOT_IN_QUOTE
    else:
        status = FindingStatus.CHECKED
    return CheckedFinding(
        **finding.model_dump(),
        status=status,
        source_seen=bool(cited_reads),
        found_in=found_in,
    )


def check_findings(
    findings: list[Finding], outcomes: Iterable[ToolOutcome]
) -> list[CheckedFinding]:
    texts = read_texts(outcomes)
    return [check(finding, texts) for finding in findings]
