"""Write summary.json + a human-readable markdown report."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from frontier_evals.runner import EvalRun


def write_report(run: "EvalRun", out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "config": run.config,
                "summary": run.summary,
                "results": [asdict(r) for r in run.results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_markdown(run), encoding="utf-8")


def _markdown(run: "EvalRun") -> str:
    s = run.summary
    lines = [
        "# Evaluation report",
        "",
        f"- Dataset: `{run.config.get('dataset')}`  ·  Mode: `{run.config.get('mode')}`",
        f"- Model: `{run.config.get('model') or 'reference-solver'}`",
        f"- Instances: {s.get('n_instances')}  ·  Seeds: {s.get('n_seeds')}",
        "",
        f"**Resolve rate: {s.get('resolve_rate_mean', 0) * 100:.1f}% "
        f"± {s.get('resolve_rate_sem', 0) * 100:.1f}%**  "
        f"(pass@{s.get('n_seeds')}: {s.get('pass_at_k', 0) * 100:.1f}%)",
        "",
        f"Threshold {s.get('threshold', 0) * 100:.0f}%: "
        f"{'MET ✅' if s.get('meets_threshold') else 'NOT MET ❌'}",
        "",
        "## Per-instance (resolved across seeds)",
        "",
        "| Instance | Resolved/seeds |",
        "| --- | --- |",
    ]
    pip = s.get("per_instance_pass", {})
    n_seeds = s.get("n_seeds", 1)
    for iid, count in sorted(pip.items()):
        lines.append(f"| `{iid}` | {count}/{n_seeds} |")
    return "\n".join(lines) + "\n"
