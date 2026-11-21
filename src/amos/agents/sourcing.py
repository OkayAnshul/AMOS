"""Does a deliverable cite only what this run actually saw? Checked in code (ADR-020).

The writer cites inline as `(source: <url or document>)`. After it answers, every
citation — those markers, and any bare https URL — is compared with the run's
**source ledger**: search results and pages read (V1.9), retrieval citations
(V0.5), read from the stored run record (ADR-015).

What this proves is narrow and exact: a cited source was **seen** by this run.
Whether it **says** what the deliverable claims is the critic's judgement.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Protocol

from sqlalchemy import select

from amos.observability import get_current_run_id

_MARKER = re.compile(r"\(\s*sources?\s*:\s*([^)]+)\)", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)\]>\"'`]+")


def normalise(source: str) -> str:
    """Comparable form: no scheme case, fragment, query noise, `www.` or trailing slash
    or punctuation — so `https://www.x.com/a/` and `https://x.com/a` match."""
    text = source.strip().strip(".,;:!?\"'`[]<>").rstrip("/")
    if text.lower().startswith(("http://", "https://")):
        text = re.sub(r"#.*$", "", text)
        text = re.sub(r"^https?://(www\.)?", "", text, flags=re.IGNORECASE).rstrip("/")
        return text.lower()
    return text.lower()


def cited(text: str) -> set[str]:
    """Every source a deliverable cites: `(source: …)` markers (several may be
    separated by `;` or `,`), bare URLs, and the cells of a table's Source column
    (ADR-022 — a live run reported "cited 0" for a fully sourced table)."""
    sources: set[str] = set()
    for marker in [*_MARKER.findall(text or ""), *table_sources(text or "")]:
        for part in re.split(r"\s*[;,]\s*|\s+and\s+", marker):
            if part.strip() and part.strip() not in {"-", "–", "—"}:
                sources.add(part.strip())
    sources.update(_URL.findall(text or ""))
    return {s for s in (normalise(x) for x in sources) if s}


def table_sources(text: str) -> list[str]:
    """The cells under a "Source" or "Sources" header, in every markdown table."""
    found: list[str] = []
    column: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            column = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if column is None:
            names = [c.strip("* ").lower() for c in cells]
            column = next((i for i, n in enumerate(names) if n in {"source", "sources"}), -1)
            continue
        if column < 0 or len(cells) <= column or set(cells[column]) <= set("-: "):
            continue
        cell = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\2", cells[column])  # [text](url) -> url
        found.append(cell.strip("* "))
    return found


def unseen(text: str, seen: set[str]) -> list[str]:
    """Citations in `text` that match nothing this run saw. A document cited by
    name matches its path (`03-architecture-decisions.md` matches
    `docs/03-architecture-decisions.md` and the reverse)."""
    ledger = {normalise(s) for s in seen}

    def known(source: str) -> bool:
        if source in ledger:
            return True
        return any(entry.endswith(f"/{source}") or source.endswith(f"/{entry}") for entry in ledger)

    return sorted(source for source in cited(text) if not known(source))


def sources_in(output: dict[str, Any] | None, tool_name: str) -> set[str]:
    """What one tool call's output makes citable."""
    if not output:
        return set()
    found: set[str] = set()
    if tool_name == "web_search":
        found.update(
            str(r["url"]) for r in output.get("results", []) if isinstance(r, dict) and r.get("url")
        )
    elif tool_name == "fetch_page":
        found.update(str(output[k]) for k in ("url", "final_url") if output.get(k))
    elif tool_name == "search_knowledge":
        found.update(
            str(p["citation"])
            for p in output.get("passages", [])
            if isinstance(p, dict) and p.get("citation")
        )
    return found


class SourceLedger(Protocol):
    async def seen(self) -> set[str]:
        """Every source this run has seen so far."""
        ...


class RecordedSources:
    """The ledger from the stored run: this run's successful source-producing tool calls."""

    def __init__(self, session_factory: Any) -> None:
        self._factory = session_factory

    async def seen(self) -> set[str]:
        from amos.database.engine import session_scope
        from amos.database.models import ToolCallRow

        raw = get_current_run_id()
        if not raw:
            return set()
        try:
            run_id = uuid.UUID(raw)
        except ValueError:
            return set()
        async with session_scope(self._factory) as session:
            # The run's own tasks are citable too — "(source: t4)" for a figure an
            # earlier task computed, which has no url by nature.
            from amos.database.models import Task

            refs = (
                await session.execute(select(Task.plan_ref).where(Task.run_id == run_id))
            ).scalars()
            found: set[str] = set(refs)
            rows = await session.execute(
                select(ToolCallRow.tool_name, ToolCallRow.output).where(
                    ToolCallRow.run_id == run_id,
                    ToolCallRow.status == "ok",
                    ToolCallRow.tool_name.in_(["web_search", "fetch_page", "search_knowledge"]),
                )
            )
            for tool_name, output in rows:
                found |= sources_in(output, tool_name)
            return found
