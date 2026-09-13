"""The regression gate (V1.2, ADR-011).

`make eval` printed a number and discarded it, so "did that change make things
worse?" was unanswerable unless somebody remembered the previous figure.
"""

from __future__ import annotations

from pathlib import Path

from amos.evaluation.baseline import (
    GATED_METRICS,
    Baseline,
    compare,
)


def baseline(**overrides: object) -> Baseline:
    defaults: dict[str, object] = {
        "measured_at": "2026-09-13",
        "model": "gemini-3.5-flash",
        "corpus_chunks": 300,
        "cases": 6,
        "pass_rate": 1.0,
        "rates": {name: 1.0 for name in GATED_METRICS},
        "mean_groundedness": 1.0,
        "total_tokens": 21252,
    }
    defaults.update(overrides)
    return Baseline(**defaults)  # type: ignore[arg-type]


def test_an_unchanged_run_reports_no_change() -> None:
    assert compare(baseline(), baseline()).regressed is False
    assert "no change" in compare(baseline(), baseline()).summary()


def test_a_dropped_rate_is_a_regression() -> None:
    worse = baseline(rates={**{n: 1.0 for n in GATED_METRICS}, "tools_correct": 0.5})

    result = compare(worse, baseline())

    assert result.regressed
    assert any("tools_correct" in entry for entry in result.regressions)
    assert "100% -> 50%" in result.summary()


def test_an_improvement_is_reported_and_does_not_fail() -> None:
    was = baseline(rates={**{n: 1.0 for n in GATED_METRICS}, "answer_correct": 0.5})

    result = compare(baseline(), was)

    assert result.regressed is False
    assert any("answer_correct" in entry for entry in result.improvements)


def test_a_different_model_is_incomparable_rather_than_a_regression() -> None:
    """A baseline is per model. Comparing across them would report a model change
    as a quality change, which is a different and much more confusing claim.
    """
    result = compare(baseline(model="gemini-3.5-flash-lite"), baseline())

    assert result.incomparable is not None
    assert result.regressed is False
    assert "not comparable" in result.summary()


def test_a_different_corpus_is_incomparable() -> None:
    """Retrieval metrics measured against 300 chunks say nothing about 900."""
    result = compare(baseline(corpus_chunks=906), baseline())

    assert result.incomparable is not None
    assert result.regressed is False


def test_the_judged_score_never_gates() -> None:
    """It comes from a model in the same family as the one being judged, and a
    threshold on a subjective number invites tuning the threshold.
    """
    assert "mean_groundedness" not in GATED_METRICS

    collapsed = baseline(mean_groundedness=0.1)
    assert compare(collapsed, baseline()).regressed is False


def test_a_round_trip_through_the_file_preserves_the_numbers(tmp_path: Path) -> None:
    path = tmp_path / "eval-baseline.json"
    original = baseline()

    original.save(path)
    loaded = Baseline.load(path)

    assert loaded is not None
    assert loaded.rates == original.rates
    assert loaded.model == original.model
    assert loaded.corpus_chunks == original.corpus_chunks


def test_no_baseline_yet_is_not_an_error(tmp_path: Path) -> None:
    """The first run has nothing to compare against, and that is not a failure."""
    assert Baseline.load(tmp_path / "missing.json") is None


def test_an_unknown_field_in_the_file_is_ignored(tmp_path: Path) -> None:
    """The file is committed and hand-editable. A stale key from an older version
    must not crash the gate — which would turn a formatting mistake into an
    unrunnable evaluation.
    """
    path = tmp_path / "eval-baseline.json"
    path.write_text(
        '{"measured_at": "2026-01-01", "model": "m", "corpus_chunks": 1, "cases": 1,'
        ' "pass_rate": 1.0, "rates": {}, "retired_metric": 0.5}'
    )

    loaded = Baseline.load(path)

    assert loaded is not None
    assert loaded.model == "m"
