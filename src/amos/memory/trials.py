"""Measuring how reliably a stated fact actually gets stored.

## Why this exists before the fix does

`remember_fact` fails intermittently — the same intent, phrased differently,
sometimes calls the tool and sometimes does not. `engineering/bugs-log.md` says
what that implies:

    "a non-deterministic failure needs a repeated-trial harness to measure a
     rate, not a single assertion."

A single run proves nothing either way. Without a **baseline captured before the
fix**, any improvement afterwards is unfalsifiable — and this project has already
been burned once by a metric that looked like a finding and was not
(`docs/16-evaluation.md`).

## The two rates, and which one matters

**store rate** — of goals stating a durable fact, how many resulted in a row?
Best effort. It will never be 1.0, because it depends on model tool selection.

**false-claim rate** — of goals where nothing was stored, how many *claimed* it
had been? **This is the one that must reach zero.** A missed write is a
disappointment; telling the user their fact is saved when it is not is a lie they
cannot detect until the next session.

Costs real quota. A deliberate command, never CI.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.database.engine import session_scope

#: Phrasings of the same intent. Deliberately varied, because the observed
#: failure was phrasing-sensitive: "Remember for later sessions: …" failed where
#: "Remember this for future sessions: …" succeeded.
TRIAL_GOALS: list[tuple[str, str]] = [
    ("Remember for later sessions: my supervisor is Dr {tag}.", "supervisor"),
    ("Remember this for future sessions: my favourite editor is {tag}.", "editor"),
    ("Store this fact about me: my home city is {tag}.", "city"),
    ("My preferred database is {tag}. Please save that for next time.", "database"),
    ("Note down that my project deadline is {tag}.", "deadline"),
    ("For future reference, my team is called {tag}.", "team"),
]


@dataclass
class TrialResult:
    goal: str
    stored: bool
    claimed: bool
    tools_called: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def false_claim(self) -> bool:
        """Claimed a memory that was never written. The failure that matters."""
        return self.claimed and not self.stored


@dataclass
class TrialReport:
    results: list[TrialResult] = field(default_factory=list)

    @property
    def usable(self) -> list[TrialResult]:
        """Trials that produced evidence. A rate limit is not a data point —
        the same distinction V1.0's evaluation harness had to learn."""
        return [r for r in self.results if r.error is None]

    @property
    def store_rate(self) -> float:
        return sum(1 for r in self.usable if r.stored) / len(self.usable) if self.usable else 0.0

    @property
    def false_claims(self) -> int:
        return sum(1 for r in self.usable if r.false_claim)

    @property
    def false_claim_rate(self) -> float:
        return self.false_claims / len(self.usable) if self.usable else 0.0

    def summary(self) -> str:
        errored = len(self.results) - len(self.usable)
        lines = [
            f"trials         {len(self.usable)} usable"
            + (f" ({errored} errored, excluded)" if errored else ""),
            "",
            f"store rate     {self.store_rate:.0%}  "
            f"({sum(1 for r in self.usable if r.stored)}/{len(self.usable)})"
            "   — best effort",
            f"FALSE CLAIMS   {self.false_claims}  ({self.false_claim_rate:.0%})   — MUST be 0",
        ]
        misses = [r for r in self.usable if not r.stored]
        if misses:
            lines += ["", "not stored:"]
            for r in misses:
                flag = "  ← CLAIMED IT WAS" if r.claimed else ""
                lines.append(f"  {r.goal[:56]:58} tools={r.tools_called}{flag}")
        return "\n".join(lines)


async def run_trials(
    agent_factory: object,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    repeats: int = 2,
    pace_seconds: float = 6.0,
) -> TrialReport:
    """Run each phrasing `repeats` times and report the rates.

    Each trial uses a unique tag so its row is identifiable and removable — the
    harness must not leave test facts in the user's memory, and must not be
    confused by rows a previous run left behind.
    """
    from amos.memory.reconcile import claims_memory  # local: may not exist yet

    report = TrialReport()
    first = True

    for repeat in range(repeats):
        for template, subject_hint in TRIAL_GOALS:
            if not first:
                await asyncio.sleep(pace_seconds)
            first = False

            tag = f"trial{uuid.uuid4().hex[:8]}"
            goal = template.format(tag=tag)

            try:
                agent = agent_factory()  # type: ignore[operator]
                result = await agent.run(goal)
            except Exception as exc:  # noqa: BLE001
                report.results.append(
                    TrialResult(
                        goal=goal, stored=False, claimed=False, error=f"{type(exc).__name__}"
                    )
                )
                continue

            stored = await _row_exists(session_factory, tag)
            report.results.append(
                TrialResult(
                    goal=goal,
                    stored=stored,
                    claimed=claims_memory(result.response.answer),
                    tools_called=[o.name for o in result.tool_outcomes],
                )
            )
            _ = (repeat, subject_hint)

    await _cleanup(session_factory)
    return report


async def _row_exists(factory: async_sessionmaker[AsyncSession], tag: str) -> bool:
    """Ground truth: is the tagged fact actually in the table?

    Deliberately checks the database rather than trusting the tool outcome — the
    outcome says a call was made, the row says the write landed.
    """
    async with session_scope(factory) as session:
        result = await session.execute(
            sql_text("SELECT 1 FROM memories WHERE content ILIKE :pattern LIMIT 1"),
            {"pattern": f"%{tag}%"},
        )
        return result.first() is not None


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_scope(factory) as session:
        result = await session.execute(
            sql_text("DELETE FROM memories WHERE content ILIKE '%trial%' RETURNING id")
        )
        return len(list(result))
