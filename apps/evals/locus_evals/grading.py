"""Grading — strictly by test execution, never by patch plausibility."""

from __future__ import annotations

from dataclasses import dataclass

from locus_runtime.harness.executor import Executor
from locus_runtime.harness.swe_agent import SweAgentResult, SweTask


@dataclass
class GradeResult:
    instance_id: str
    resolved: bool
    detail: str


def grade_synthetic(
    task: SweTask, result: SweAgentResult, root: str, *, executor: Executor | None = None
) -> GradeResult:
    """Re-run the task's test command independently; exit 0 => resolved.

    The tests execute the agent's patch, so they run through the instance's
    gated, confined executor (``task.executor`` unless one is given) -- never
    on the bare host. ``root`` is kept for callers that log it.

    Submit-or-zero: a run that produced no patch is unresolved regardless of
    test state (it never committed to an answer).
    """
    del root
    if not result.has_patch:
        return GradeResult(task.instance_id, False, "no patch submitted")
    runner = executor if executor is not None else task.executor
    res = runner.run_shell(task.test_command, timeout=180)
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
        from locus_evals.swebench_grader import run_official_grade  # type: ignore
    except ImportError as exc:  # pragma: no cover - live-only path
        raise RuntimeError(
            "SWE-bench grading requires the 'swebench' extra and locus_evals.swebench_grader"
        ) from exc
    return run_official_grade(task, result)  # pragma: no cover
