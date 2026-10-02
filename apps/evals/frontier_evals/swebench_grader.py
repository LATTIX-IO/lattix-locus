"""Official SWE-bench grading (live mode only).

Writes the agent's patch as a prediction and defers the resolved/unresolved
verdict entirely to ``swebench.harness.run_evaluation`` (test execution:
FAIL_TO_PASS must flip to passing, PASS_TO_PASS must stay passing). No LLM, no
heuristic ever decides resolution.

Requires the ``swebench`` extra and a reachable Docker host; not imported in
plumbing mode.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from frontier_runtime.harness.swe_agent import SweAgentResult, SweTask

from frontier_evals.grading import GradeResult


def write_predictions(
    results: list[tuple[SweTask, SweAgentResult]], path: Path, model: str
) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for task, result in results:
            fh.write(
                json.dumps(
                    {
                        "instance_id": task.instance_id,
                        "model_name_or_path": model,
                        "model_patch": result.patch,
                    }
                )
                + "\n"
            )
    return path


def run_official_grade(task: SweTask, result: SweAgentResult) -> GradeResult:  # pragma: no cover
    """Grade a single instance via the official harness."""
    from swebench.harness.run_evaluation import main as run_evaluation  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        preds = write_predictions([(task, result)], Path(tmp) / "preds.jsonl", "frontier-agent")
        run_id = f"frontier-{task.instance_id}"
        run_evaluation(
            dataset_name="princeton-nlp/SWE-bench_Verified",
            split="test",
            instance_ids=[task.instance_id],
            predictions_path=str(preds),
            max_workers=1,
            run_id=run_id,
            timeout=1800,
        )
        report_path = Path(f"frontier-agent.{run_id}.json")
        if not report_path.exists():
            return GradeResult(task.instance_id, False, "no evaluation report produced")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        resolved = task.instance_id in report.get("resolved_ids", [])
        return GradeResult(
            task.instance_id, resolved, "resolved" if resolved else "unresolved (tests)"
        )
