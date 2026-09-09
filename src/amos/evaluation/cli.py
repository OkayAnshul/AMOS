"""Run the evaluation suite: `python -m amos.evaluation.cli`

Costs real API calls — several per goal. Run it deliberately, not on every commit.
"""

from __future__ import annotations

import asyncio
import sys

from amos.api.dependencies import build_agent
from amos.config import get_settings
from amos.database.engine import create_engine, create_session_factory
from amos.evaluation.cases import GOLDEN_GOALS
from amos.evaluation.harness import EvaluationHarness
from amos.evaluation.judge import GroundednessJudge
from amos.llm.gemini import GeminiProvider
from amos.observability import configure_logging


async def main(judge_enabled: bool = True) -> int:
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
    return 0 if suite.passed == len(suite.measured) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--no-judge" not in sys.argv)))
