"""LOCUS-339: the performance budget gate -- baseline comparison (pure), the runner-side
baseline/history store, and the measurement entry point's output contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from locus_runtime.loop_runner import perf_budget as pb
from locus_runtime.loop_runner.perf_budget import (
    RESULT_PREFIX,
    PerfSettings,
    PerfStore,
    compare_to_baseline,
    make_perf_evaluator,
    median_ms,
    parse_perf_output,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SETTINGS = PerfSettings(tolerance=0.5, min_delta_ms=2.0)


def _out(**metrics: float | None) -> str:
    return "noise\n" + RESULT_PREFIX + json.dumps({"metrics": metrics})


def test_first_measurement_records_the_baseline() -> None:
    verdict = compare_to_baseline({"health_ms": 4.0, "gateway_decision_ms": 0.1}, {}, SETTINGS)
    assert verdict.status == "pass"
    assert verdict.recorded == ("health_ms", "gateway_decision_ms")
    assert verdict.details["policy_decision_ms"] == {"status": "not measured"}


def test_regression_needs_both_relative_and_absolute_margin() -> None:
    base = {"health_ms": 4.0, "gateway_decision_ms": 0.02}
    # +100 % but only +0.02 ms: below the noise floor, not a regression.
    tiny = compare_to_baseline({"gateway_decision_ms": 0.04}, base, SETTINGS)
    assert tiny.status == "pass"
    # within +50 %: fine
    assert compare_to_baseline({"health_ms": 5.9}, base, SETTINGS).status == "pass"
    # +50 % and > 2 ms: regression
    slow = compare_to_baseline({"health_ms": 6.5}, base, SETTINGS)
    assert slow.status == "fail" and "health_ms" in slow.regressions[0]
    assert slow.details["health_ms"]["status"] == "regressed"


def test_absolute_budget_fails_even_without_a_baseline() -> None:
    verdict = compare_to_baseline({"health_ms": 150.0}, {}, SETTINGS)
    assert verdict.status == "fail" and "budget" in verdict.regressions[0]
    assert verdict.recorded == ()  # an over-budget first run never becomes the baseline


@pytest.mark.parametrize("bad", [None, -1.0, float("nan"), float("inf"), True, "3"])
def test_invalid_measurements_are_not_measured(bad: object) -> None:
    verdict = compare_to_baseline({"health_ms": bad}, {"health_ms": 1.0}, SETTINGS)
    assert verdict.status == "pass" and verdict.details["health_ms"]["status"] == "not measured"


def test_parse_perf_output_takes_the_last_result_line_and_rejects_garbage() -> None:
    text = _out(health_ms=1.0) + "\n" + _out(health_ms=2.0)
    assert parse_perf_output(text) == {
        "health_ms": 2.0,
        "gateway_decision_ms": None,
        "policy_decision_ms": None,
    }
    assert parse_perf_output("nothing here") is None
    assert parse_perf_output(RESULT_PREFIX + "{not json") is None
    assert parse_perf_output(RESULT_PREFIX + '{"metrics": [1]}') is None


def test_store_records_then_compares_and_appends_history(tmp_path: Path) -> None:
    store = PerfStore(tmp_path)
    first = store.evaluate({"health_ms": 4.0}, run_id="r1", settings=SETTINGS, now=NOW)
    assert first.recorded == ("health_ms",)
    assert store.baseline() == {"health_ms": 4.0}
    second = store.evaluate({"health_ms": 9.0}, run_id="r2", settings=SETTINGS, now=NOW)
    assert second.status == "fail"
    assert store.baseline() == {"health_ms": 4.0}  # a regression never moves the baseline
    third = store.evaluate(
        {"health_ms": 4.5, "policy_decision_ms": 1.0}, run_id="r3", settings=SETTINGS, now=NOW
    )
    assert third.status == "pass" and third.recorded == ("policy_decision_ms",)
    assert store.baseline() == {"health_ms": 4.0, "policy_decision_ms": 1.0}
    history = store.history()
    assert [h["run_id"] for h in history] == ["r1", "r2", "r3"]
    assert history[1]["status"] == "fail" and history[1]["regressions"]


def test_corrupt_baseline_is_treated_as_absent(tmp_path: Path) -> None:
    (tmp_path / "perf-baseline.json").write_text("{oops", encoding="utf-8")
    assert PerfStore(tmp_path).baseline() == {}


def test_evaluator_fails_when_the_suite_printed_no_result(tmp_path: Path) -> None:
    evaluate = make_perf_evaluator(PerfStore(tmp_path), run_id="r", settings=SETTINGS)
    assert evaluate("Traceback ...")[0] == "fail"
    status, detail, evidence = evaluate(_out(health_ms=3.0))
    assert status == "pass" and "baseline recorded" in detail and "metrics" in evidence


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_LOOP_PERF_TOLERANCE", "0.25")
    monkeypatch.setenv("LOCUS_LOOP_PERF_MIN_DELTA_MS", "nope")
    monkeypatch.setenv("LOCUS_LOOP_PERF_BUDGET_HEALTH_MS", "40")
    settings = PerfSettings.from_env()
    assert settings.tolerance == 0.25 and settings.min_delta_ms == 2.0
    assert settings.budgets_ms["health_ms"] == 40.0


def test_median_is_robust_to_an_outlier() -> None:
    ticks = iter([0.0, 0.001, 0.0, 0.001, 0.0, 5.0, 0.0, 0.001] + [0.0] * 20)
    calls: list[int] = []
    real = pb.time.perf_counter
    pb.time.perf_counter = lambda: next(ticks)  # type: ignore[assignment]
    try:
        value = median_ms(lambda: calls.append(1), iterations=4, warmup=1)
    finally:
        pb.time.perf_counter = real  # type: ignore[assignment]
    assert value == pytest.approx(1.0) and len(calls) == 5


def test_main_prints_one_parseable_result_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        pb,
        "measure",
        lambda iterations: {
            "health_ms": 1.5,
            "gateway_decision_ms": 0.1,
            "policy_decision_ms": None,
        },
    )
    assert pb.main(["--iterations", "1"]) == 0
    parsed = parse_perf_output(capsys.readouterr().out)
    assert parsed == {"health_ms": 1.5, "gateway_decision_ms": 0.1, "policy_decision_ms": None}


def test_gateway_probe_measures_an_allowed_decision() -> None:
    value = pb._measure_gateway(5)
    assert value is not None and value >= 0
