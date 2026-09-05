"""Chunking.

The chunk is the unit of retrieval, so chunking decides what the system *can*
find. Two failure modes bound the choice:

- **Too large** — a chunk containing the answer plus three unrelated paragraphs
  dilutes its embedding, so it ranks below chunks that are wholly about the topic.
- **Too small** — the answer gets split across two chunks and neither contains
  enough to be useful on its own.

AMOS's corpus is Markdown documentation, which carries explicit structure, so
chunking is **heading-aware**: split on headings first, and only fall back to
size-based splitting for sections that are too long. A section is a coherent
unit of meaning; an arbitrary 1000-character window is not.

Overlap exists for the fallback path only. When a long section must be split
mid-prose, a sentence answering a question can land exactly on the boundary;
overlap means it survives intact in one of the two pieces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: ~250 tokens at 4 chars/token, far under gemini-embedding-001's 2048 limit.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 150
#: Below this, a chunk carries too little to be worth retrieving on its own.
MIN_CHUNK_SIZE = 60

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    """A retrievable piece of a document."""

    content: str
    index: int
    heading: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.content)


def chunk_markdown(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    source: str | None = None,
) -> list[Chunk]:
    """Split Markdown into chunks, respecting headings where possible."""
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    sections = _split_on_headings(text)
    chunks: list[Chunk] = []

    for heading, body in sections:
        content = body.strip()
        if len(content) < MIN_CHUNK_SIZE:
            continue

        # The heading is prepended to every piece of its section. Without it, a
        # chunk from the middle of "## Why pgvector, not Qdrant" loses the only
        # words that say what it is about — and those are exactly the words a
        # question about it would use.
        prefix = f"{heading}\n\n" if heading else ""

        for piece in _split_by_size(content, chunk_size - len(prefix), overlap):
            chunks.append(
                Chunk(
                    content=prefix + piece,
                    index=len(chunks),
                    heading=heading,
                    metadata={"source": source} if source else {},
                )
            )
    return chunks


def _split_on_headings(text: str) -> list[tuple[str | None, str]]:
    """Split into (heading, body) pairs."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [(None, text)]

    sections: list[tuple[str | None, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()]
        if preamble.strip():
            sections.append((None, preamble))

    for position, match in enumerate(matches):
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), text[start:end]))
    return sections


def _split_by_size(text: str, size: int, overlap: int) -> list[str]:
    """Fall back to size-based splitting, preferring paragraph then sentence breaks.

    Cutting mid-sentence produces chunks that read as fragments and embed poorly,
    so a nearby natural boundary is preferred over an exact character count.
    """
    if len(text) <= size:
        return [text]

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            end = _best_break(text, start, end)
        piece = text[start:end].strip()
        if len(piece) >= MIN_CHUNK_SIZE:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def _best_break(text: str, start: int, end: int) -> int:
    """Nearest paragraph break, else sentence end, else the hard limit."""
    window = text[start:end]
    for marker in ("\n\n", ". ", ".\n", "\n"):
        position = window.rfind(marker)
        # Only accept a break in the last third, or chunks become tiny.
        if position > len(window) * 0.6:
            return start + position + len(marker)
    return end
