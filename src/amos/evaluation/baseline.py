"""A stored scorecard, so a regression is visible (V1.2, ADR-011).

`make eval` printed a number and discarded it, which made "did that change make
things worse?" unanswerable unless somebody remembered the previous figure. This
keeps the last measured scorecard in a **committed JSON file** so a score change
shows up in a diff, in git history, next to the commit that caused it. A database
table would put that history somewhere `git log` cannot see.

**Only deterministic metrics are stored and compared.** The judged score is
recorded for context and never gates: it comes from a model in the same family as
the one being judged, and a threshold on a subjective number invites tuning the
threshold rather than fixing the system.

A baseline is **per corpus and per model**. Both are recorded, and a comparison
across different ones is reported as incomparable rather than silently treated as
a regression — the numbers would be measuring a different system.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Committed, and deliberately not under `docs/` — it is evidence, not prose.
DEFAULT_PATH = Path("engineering/eval-baseline.json")

#: The deterministic rates that gate. Judged metrics are excluded by design.
GATED_METRICS = ("completed", "output_valid", "tools_correct", "answer_correct")

#: How far a rate may fall before it counts as a regression. Not zero: with six
#: cases one flipping is 17 points, so a zero-tolerance gate would fire on noise
#: that is indistinguishable from a real change at this sample size. Recorded
#: here rather than buried in a comparison, because it is a judgement call.
TOLERANCE = 0.001


@dataclass
class Baseline:
    """The last measured scorecard, and what it was measured against."""

    measured_at: str
    model: str
    corpus_chunks: int
    cases: int
    pass_rate: float
    rates: dict[str, float] = field(default_factory=dict)
    #: Context only. Never compared, never gates.
    mean_groundedness: float | None = None
    total_tokens: int = 0

    @classmethod
    def from_suite(cls, suite: Any, *, model: str, corpus_chunks: int) -> Baseline:
        return cls(
            measured_at=datetime.now(UTC).date().isoformat(),
            model=model,
            corpus_chunks=corpus_chunks,
            cases=len(suite.measured),
            pass_rate=round(suite.pass_rate, 4),
            rates={name: round(suite.rate(name), 4) for name in GATED_METRICS},
            mean_groundedness=(
                round(suite.mean_groundedness, 4) if suite.mean_groundedness is not None else None
            ),
            total_tokens=suite.total_tokens,
        )

    def save(self, path: Path = DEFAULT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> Baseline | None:
        """The stored baseline, or None when there is not one yet."""
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Comparison:
    """What changed since the stored baseline."""

    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    #: Set when the baseline measured a different system, so the numbers are not
    #: comparable. Reported rather than treated as a pass or a failure.
    incomparable: str | None = None

    @property
    def regressed(self) -> bool:
        return bool(self.regressions) and self.incomparable is None

    def summary(self) -> str:
        if self.incomparable:
            return f"baseline not comparable: {self.incomparable}"
        lines = []
        for entry in self.regressions:
            lines.append(f"  REGRESSION  {entry}")
        for entry in self.improvements:
            lines.append(f"  improved    {entry}")
        return "\n".join(lines) if lines else "  no change against the stored baseline"


def compare(current: Baseline, stored: Baseline) -> Comparison:
    """Current against stored, on deterministic metrics only."""
    if stored.model != current.model:
        return Comparison(incomparable=f"measured on {stored.model}, this run used {current.model}")
    if stored.corpus_chunks != current.corpus_chunks:
        return Comparison(
            incomparable=(
                f"measured against {stored.corpus_chunks} chunks, "
                f"this run used {current.corpus_chunks}"
            )
        )

    result = Comparison()
    for name in GATED_METRICS:
        was, now = stored.rates.get(name), current.rates.get(name)
        if was is None or now is None:
            continue
        if now < was - TOLERANCE:
            result.regressions.append(f"{name}: {was:.0%} -> {now:.0%}")
        elif now > was + TOLERANCE:
            result.improvements.append(f"{name}: {was:.0%} -> {now:.0%}")
    return result
