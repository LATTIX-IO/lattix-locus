"""LOCUS-339: `lattix loop report` -- built from the loop home's ledger and history files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from locus_runtime.loop_runner.eval_gate import EvalGateResult, EvalHistory
from locus_runtime.loop_runner.perf_budget import PerfSettings, PerfStore
from locus_runtime.loop_runner.report import build_report, load_report, render_text
from locus_runtime.loop_runner.state import Ledger, append_run_history
from locus_tooling.cli import cli

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _run(outcome: str, day: str, **kw: object) -> dict[str, object]:
    return {"outcome": outcome, "finished_at": f"{day}T09:00:00Z", "run_id": f"r-{day}", **kw}


RUNS = [
    _run("done", "2026-10-01", pr_url="u1", usage={"cost_usd": 0.0, "tokens": 1200}),
    _run("done", "2026-10-02", pr_url="u2", usage={"cost_usd": 0.02, "tokens": 800}),
    _run("stopped", "2026-10-02", kind="quality_gate", gate_failures=["tests", "lint"]),
    _run("blocked", "2026-10-03", kind="quality_gate", gate_failures=["tests"]),
    _run("stopped", "2026-10-03", kind="user"),  # kill switch: not an attempt
    _run("done", "2026-06-01", pr_url="old"),  # outside the window
]
EVALS = [
    {"at": "2026-10-01T09:00:00Z", "status": "fail", "resolve_rate": 0.2},
    {"at": "2026-10-02T09:00:00Z", "status": "skipped", "resolve_rate": None},
    {"at": "2026-10-03T09:00:00Z", "status": "pass", "resolve_rate": 0.6},
]
PERF = [
    {"at": "2026-10-02T09:00:00Z", "status": "pass", "metrics": {"health_ms": 3.0}},
    {"at": "2026-10-03T09:00:00Z", "status": "fail", "metrics": {"health_ms": 9.0}},
]


def test_build_report_summarises_throughput_success_cost_gates_and_trends() -> None:
    report = build_report(
        RUNS, EVALS, PERF, perf_baseline={"health_ms": 3.0}, now=NOW, days=30, open_prs=[{}]
    )
    assert report["throughput"]["runs"] == 5
    assert report["throughput"]["prs_opened"] == 2
    assert report["throughput"]["open_loop_prs"] == 1
    assert report["throughput"]["runs_per_day"] == {
        "2026-10-01": 1,
        "2026-10-02": 2,
        "2026-10-03": 2,
    }
    assert report["outcomes"] == {"blocked": 1, "done": 2, "stopped": 2}
    assert report["success_rate"] == 0.5  # 2 done / 4 attempts (user stop excluded)
    assert report["cost"]["usd"] == pytest.approx(0.02) and report["cost"]["tokens"] == 2000
    assert report["gate_failures_by_check"] == {"tests": 2, "lint": 1}
    ev = report["eval"]
    assert ev["latest_resolve_rate"] == 0.6 and ev["change"] == pytest.approx(0.4)
    assert ev["statuses"] == {"fail": 1, "pass": 1, "skipped": 1}
    assert [p["resolve_rate"] for p in ev["trend"]] == [0.2, 0.6]  # skipped is not a point
    health = report["perf"]["metrics"]["health_ms"]
    assert health["baseline_ms"] == 3.0 and health["latest_ms"] == 9.0
    assert report["perf"]["statuses"] == {"fail": 1, "pass": 1}


def test_empty_home_report() -> None:
    report = build_report([], [], [], now=NOW)
    assert report["success_rate"] is None and report["eval"]["latest_resolve_rate"] is None
    assert "success rate: n/a" in render_text(report)


def test_report_from_loop_home_files_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "loop"
    monkeypatch.setenv("LOCUS_LOOP_HOME", str(home))
    for record in RUNS[:4]:
        append_run_history(home, dict(record))
    with (home / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    EvalHistory(home).append(
        EvalGateResult("fail", resolve_rate=0.25), run_id="r", issue="L-1", now=NOW
    )
    PerfStore(home).evaluate({"health_ms": 2.0}, run_id="r", settings=PerfSettings(), now=NOW)
    ledger = Ledger.load(home)
    ledger.add_open_pr({"number": 3})
    ledger.save()

    report = load_report(home, now=NOW)
    assert report["throughput"]["runs"] == 4 and report["throughput"]["open_loop_prs"] == 1
    assert report["eval"]["latest_resolve_rate"] == 0.25
    assert report["perf"]["metrics"]["health_ms"]["baseline_ms"] == 2.0

    runner = CliRunner()
    as_json = runner.invoke(cli, ["loop", "report", "--json", "--days", "3650"])
    assert as_json.exit_code == 0, as_json.output
    parsed = json.loads(as_json.output[as_json.output.index("{") :])
    assert parsed["throughput"]["runs"] == 4 and parsed["home"] == str(home.resolve())
    text = runner.invoke(cli, ["loop", "report", "--days", "3650"])
    assert text.exit_code == 0 and "Locus loop report" in text.output
    assert runner.invoke(cli, ["loop", "report", "--days", "0"]).exit_code != 0
