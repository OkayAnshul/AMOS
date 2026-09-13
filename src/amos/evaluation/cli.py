"""Run the evaluation suite: `python -m amos.evaluation.cli`

Costs real API calls — several per goal. Run it deliberately, not on every commit.

Flags:
  --no-judge          skip the LLM-judged groundedness metric
  --update-baseline   overwrite engineering/eval-baseline.json with this run

The baseline is compared but **never written automatically** (ADR-011). A gate
that updates itself on failure is not a gate, so accepting a new number is an
explicit act that shows up in a diff.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.api.dependencies import build_agent
from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory, session_scope
from amos.evaluation.baseline import DEFAULT_PATH, Baseline, Comparison, compare
from amos.evaluation.cases import GOLDEN_GOALS
from amos.evaluation.harness import EvaluationHarness
from amos.evaluation.judge import GroundednessJudge
from amos.llm.gemini import GeminiProvider
from amos.observability import configure_logging


async def _corpus_size(factory: async_sessionmaker[AsyncSession] | None) -> int:
    """Chunks currently indexed. Part of what makes a baseline comparable."""
    if factory is None:
        return 0
    try:
        async with session_scope(factory) as session:
            result = await session.execute(sql_text("SELECT count(*) FROM chunks"))
            return int(result.scalar_one())
    except Exception:  # noqa: BLE001 - an unmeasurable corpus is not a failed run
        return 0


async def main(judge_enabled: bool = True, update_baseline: bool = False) -> int:
    settings = get_settings()
    configure_logging("WARNING")

    engine = create_engine(settings) if settings.database_url else None
    factory = create_session_factory(engine) if engine else None

    try:
        agent = build_agent(settings, session_factory=factory)
        judge = (
            GroundednessJudge(GeminiProvider(settings.require_api_key(), settings.llm_model))
            if judge_enabled
            else None
        )
        suite = await EvaluationHarness(agent, judge, pace_seconds=20.0).run(GOLDEN_GOALS)
    finally:
        if engine is not None:
            await engine.dispose()

    print(suite.summary())

    # --- regression gate against the stored baseline ---
    current = Baseline.from_suite(
        suite, model=settings.llm_model, corpus_chunks=await _corpus_size(factory)
    )
    stored = Baseline.load()
    comparison = Comparison()
    print("\nagainst the stored baseline:")
    if stored is None:
        print("  none stored yet — run with --update-baseline to record this one")
    else:
        comparison = compare(current, stored)
        print(f"  baseline {stored.measured_at}, {stored.model}, {stored.corpus_chunks} chunks")
        print(comparison.summary())

    if update_baseline:
        current.save()
        print(f"\nbaseline updated: {DEFAULT_PATH}")

    if suite.passed < suite.total:
        print("\nfailures:")
        for score in suite.scores:
            if not score.passed:
                print(f"  {score.goal[:70]}")
                for failure in score.failures:
                    print(f"      {failure}")

    if suite.unmeasurable:
        print(f"\n{suite.unmeasurable} case(s) could not be measured:")
        for score in suite.scores:
            if not score.measured:
                print(f"  {score.goal[:70]}")
                print(f"      {(score.unmeasurable or '')[:120]}")

    # Non-zero exit when a deterministic check fails, so this can gate CI.
    # Judged metrics deliberately do NOT gate: a flaky judge must not block a
    # release, and a threshold on a subjective score invites tuning the threshold.
    # Unmeasurable cases do not fail the gate: a rate limit is not a regression.
    #
    # A regression against the stored baseline also fails, even when every case
    # still passes — a rate falling from 100% to 83% is the thing this exists to
    # catch, and "all cases passed" would hide it.
    failed = suite.passed != len(suite.measured)
    return 1 if failed or comparison.regressed else 0


if __name__ == "__main__":
    sys.exit(
        asyncio.run(
            main(
                judge_enabled="--no-judge" not in sys.argv,
                update_baseline="--update-baseline" in sys.argv,
            )
        )
    )
