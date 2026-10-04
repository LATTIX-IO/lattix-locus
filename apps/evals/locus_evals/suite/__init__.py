"""The private Locus RSI task suite and its evaluator (LOCUS-351).

* ``tasks/dev/`` and ``tasks/heldout/`` -- declarative tasks (:mod:`.model`).
* :mod:`.store` -- the read-only, hash-verified copy the evaluator runs from.
* :mod:`.graders` -- deterministic graders (hidden tests, file and artifact
  checks, injection outcome, budget adherence).
* :mod:`.runner` -- the evaluator: candidate instance per sample, grading,
  telemetry scores, the scorecard JSON. Uses Inspect AI when installed
  (:mod:`.inspect_adapter`, optional ``evals`` extra), else a built-in loop
  that produces the same scorecard.

This directory is a D-22 protected path: the self-improvement loop cannot
change its own exam. See ``docs/development/rsi-scorecard.md``.
"""

from __future__ import annotations

from pathlib import Path

#: Bump when the task set or the grading semantics change (baselines are per suite).
SUITE_VERSION = "2026.10.1"
SUITE_DIR = Path(__file__).resolve().parent
TASKS_DIR = SUITE_DIR / "tasks"
