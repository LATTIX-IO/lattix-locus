"""Grading — strictly by test execution, never by patch plausibility."""

from __future__ import annotations

from dataclasses import dataclass

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.swe_agent import SweAgentResult, SweTask


@dataclass
class GradeResult:
    instance_id: str
    resolved: bool
    detail: str


def grade_synthetic(task: SweTask, result: SweAgentResult, root: str) -> GradeResult:
    """Re-run the task's test command independently; exit 0 => resolved.

    Submit-or-zero: a run that produced no patch is unresolved regardless of
    test state (it never committed to an answer).
    """
    if not result.has_patch:
        return GradeResult(task.instance_id, False, "no patch submitted")
    executor = LocalDirectExecutor(root)
    res = executor.run_shell(task.test_command, timeout=180)
    resolved = res.exit_code == 0
    return GradeResult(
        task.instance_id,
        resolved,
        "tests passed" if resolved else f"tests failed (exit {res.exit_code})",
    )


def grade_swebench(task: SweTask, result: SweAgentResult) -> GradeResult:
    """Grade a SWE-bench prediction via the official harness.

    Writes the patch as a prediction and defers to ``swebench.harness`` for the
    FAIL_TO_PASS / PASS_TO_PASS verdict. Requires the ``swebench`` extra and a
    reachable Docker host; not exercised in plumbing mode.
    """
    if not result.has_patch:
        return GradeResult(task.instance_id, False, "no patch submitted")
    try:
        from frontier_evals.swebench_grader import run_official_grade  # type: ignore
    except ImportError as exc:  # pragma: no cover - live-only path
        raise RuntimeError(
            "SWE-bench grading requires the 'swebench' extra and frontier_evals.swebench_grader"
        ) from exc
    return run_official_grade(task, result)  # pragma: no cover
