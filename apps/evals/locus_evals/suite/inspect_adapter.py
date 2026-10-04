"""Inspect AI as the eval harness for the Locus suite (LOCUS-351; optional ``evals`` extra).

Inspect AI (MIT, UK AI Security Institute) provides the task / solver / scorer
structure, epochs and the eval logs. The model is not Inspect's: the solver
runs a **Locus runtime** (``create_runtime``, so verified-loop and deep-agents
both work) in the candidate instance through the gateway, and Inspect is given
its built-in ``mockllm`` model, which is never called. Each sample's solver
delegates to the same :func:`locus_evals.suite.runner.evaluate_sample` the
built-in engine uses, so both engines produce the same scorecard.

Scorers (deterministic, from the graded record):

* ``task_success`` -- every grader check passed;
* ``injection_resistance`` -- the attack did not succeed (injection tasks only);
* ``budget_adherence`` -- an honest budget stop (budget tasks only).

Inspect writes its logs to ``<output>/inspect-logs``. Samples run one at a time
(``max_samples=1``): model trials are strictly sequential on this machine.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from pathlib import Path

from locus_runtime.rsi.scorecard import SampleRecord

from locus_evals.suite.model import SuiteTask


def inspect_available() -> bool:
    return importlib.util.find_spec("inspect_ai") is not None


def run_with_inspect(
    tasks: Sequence[SuiteTask],
    trials: int,
    evaluate: Callable[[SuiteTask, int], SampleRecord],
    *,
    log_dir: Path,
) -> list[SampleRecord]:
    """Run ``tasks`` x ``trials`` through Inspect AI; returns the graded records."""
    from locus_evals.suite.inspect_task import run

    return run(tasks, trials, evaluate, log_dir=log_dir)
