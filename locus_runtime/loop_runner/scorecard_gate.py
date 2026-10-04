"""The loop's RSI scorecard gate (LOCUS-351): is the candidate *better*?

After the pre-PR verifier suite passes and the change is committed, the runner
evaluates the candidate commit with the RSI suite (``apps/evals/locus_evals/suite``,
loaded from the runner's own trusted checkout, never from the run's working
copy) in a :class:`~locus_runtime.rsi.candidate.CandidateInstance`, compares
the scorecard with the stored baseline (the latest complete scorecard of the
base branch in ``LOCUS_LOOP_HOME/variants/``) and records the variant.

Modes (``LOCUS_LOOP_SCORECARD``): ``off``; ``advisory`` (default for ``lattix
loop``: run, record, attach to the PR; never blocks); ``required`` (the D-22
auto-merge additionally holds unless the comparison says ``promote``).

Fail honest: no reachable keyless model endpoint, no OPA or no suite means
``skipped`` with the reason; a crash is ``error``. Neither is ever a promote.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from locus_runtime.gateway import redact_text
from locus_runtime.loop_runner.eval_gate import EvalMode, parse_eval_mode
from locus_runtime.loop_runner.perf_budget import append_jsonl, read_jsonl
from locus_runtime.rsi.scorecard import (
    Comparison,
    Scorecard,
    compare,
    comparison_markdown,
    scorecard_markdown,
)
from locus_runtime.rsi.variants import VariantArchive

ScorecardStatus = Literal["promote", "hold", "skipped", "error"]
DEFAULT_MODEL = "gpt-oss:20b-ctx32k"


class ScorecardUnavailable(RuntimeError):
    """The scorecard cannot run here (reported as ``skipped``, never a promote)."""


def parse_scorecard_mode(value: str | None, default: EvalMode = "advisory") -> EvalMode:
    """``off`` | ``advisory`` | ``required`` (same spelling rules as the eval gate)."""
    return parse_eval_mode(value, default)


def scorecard_merge_hold_reason(mode: str, status: str | None) -> str:
    """Why the D-22 merge must hold for the scorecard ('' = no hold). Pure."""
    if parse_scorecard_mode(mode, "off") != "required":
        return ""
    if status == "promote":
        return ""
    return f"RSI scorecard is required but did not promote (status: {status or 'not run'})"


def _clean(text: Any, limit: int = 300) -> str:
    return redact_text(str(text or ""), limit=limit).replace("<!--", "&lt;!--").replace("`", "'")


@dataclass(frozen=True)
class ScorecardGateResult:
    status: ScorecardStatus
    reason: str = ""
    scorecard: Scorecard | None = None
    comparison: Comparison | None = None
    scorecard_path: str = ""
    variant_path: str = ""

    @classmethod
    def skipped(cls, reason: str) -> ScorecardGateResult:
        return cls("skipped", _clean(reason))

    def summary(self) -> dict[str, Any]:
        """A small record for ``runs.jsonl`` / the ledger / the history file."""
        heldout = self.scorecard.split("heldout") if self.scorecard else None
        dev = self.scorecard.split("dev") if self.scorecard else None
        return {
            "status": self.status,
            "reason": self.reason,
            "git_sha": self.scorecard.git_sha if self.scorecard else "",
            "model": self.scorecard.model if self.scorecard else "",
            "heldout_pass_rate": heldout.pass_rate if heldout else None,
            "dev_pass_rate": dev.pass_rate if dev else None,
            "scorecard_status": self.scorecard.status if self.scorecard else None,
            "reasons": list(self.comparison.reasons[:8]) if self.comparison else [],
            "improvements": list(self.comparison.improvements) if self.comparison else [],
            "variant": Path(self.variant_path).name if self.variant_path else "",
        }

    def markdown(self) -> list[str]:
        if self.status in {"skipped", "error"}:
            return [f"- {self.status}: {_clean(self.reason)}"]
        lines: list[str] = []
        if self.scorecard is not None:
            lines += [_clean(line, 400) for line in scorecard_markdown(self.scorecard)]
        if self.comparison is not None:
            lines += ["", "Comparison with the base branch's latest scorecard:"]
            lines += [_clean(line, 400) for line in comparison_markdown(self.comparison)]
        if self.variant_path:
            lines.append(f"- Variant archive: `variants/{Path(self.variant_path).name}`")
        return lines


@dataclass
class ScorecardRequest:
    candidate_checkout: Path
    repo_path: Path
    output_dir: Path
    git_sha: str
    branch: str
    #: Failing quality-gate checks (``None`` = the gates were not consulted).
    gate_failures: list[str] | None = None
    trials: int = 1
    splits: tuple[str, ...] = ("dev", "heldout")
    model: str = DEFAULT_MODEL
    runtime: str = ""
    python: str = ""
    run_kwargs: Mapping[str, Any] = field(default_factory=dict)


ScorecardRunner = Callable[[ScorecardRequest], Scorecard]


def _import_suite_runner(repo_path: Path) -> Any:
    """``locus_evals.suite.runner`` from the installed package or the runner's own
    checkout (never the run's working copy: the exam comes from the trusted side)."""
    try:
        return importlib.import_module("locus_evals.suite.runner")
    except ImportError:
        source = (Path(repo_path) / "apps" / "evals").resolve()
        if not (source / "locus_evals" / "suite").is_dir():
            raise
        if str(source) not in sys.path:
            sys.path.append(str(source))
        return importlib.import_module("locus_evals.suite.runner")


def default_scorecard_runner(request: ScorecardRequest) -> Scorecard:
    try:
        suite = _import_suite_runner(request.repo_path)
    except ImportError as exc:
        raise ScorecardUnavailable("the apps/evals RSI suite is not installed") from exc
    config = suite.SuiteRunConfig(
        candidate_checkout=request.candidate_checkout,
        output_dir=request.output_dir,
        candidate_python=request.python or sys.executable,
        splits=tuple(request.splits),
        trials=max(1, int(request.trials)),
        model=request.model,
        runtime=request.runtime,
        git_sha=request.git_sha,
        branch=request.branch,
        gate_failures=None if request.gate_failures is None else list(request.gate_failures),
        **dict(request.run_kwargs),
    )
    try:
        run = suite.run_suite(config)
    except suite.SuiteUnavailable as exc:
        raise ScorecardUnavailable(str(exc)) from exc
    scorecard: Scorecard = run.scorecard
    return scorecard


def evaluate_candidate(
    request: ScorecardRequest,
    runner: ScorecardRunner,
    archive: VariantArchive,
    *,
    base_branch: str = "main",
    now: datetime | None = None,
) -> ScorecardGateResult:
    """Run the scorecard, compare with the base branch's baseline, archive the variant."""
    try:
        scorecard = runner(request)
    except ScorecardUnavailable as exc:
        return ScorecardGateResult.skipped(str(exc))
    except Exception as exc:  # noqa: BLE001 - a scorecard that cannot run is never a promote
        return ScorecardGateResult("error", f"the scorecard run failed ({type(exc).__name__})")
    baseline = archive.baseline(
        base_branch,
        heldout_digest=scorecard.split_digests.get("heldout", ""),
        model=scorecard.model,
    )
    comparison = compare(baseline, scorecard)
    variant = ""
    try:
        variant = str(archive.record(scorecard, comparison=comparison, now=now, source="loop"))
    except (OSError, ValueError):
        variant = ""
    return ScorecardGateResult(
        comparison.decision,
        "; ".join(comparison.reasons[:3]),
        scorecard=scorecard,
        comparison=comparison,
        scorecard_path=str(request.output_dir / "scorecard.json"),
        variant_path=variant,
    )


class ScorecardHistory:
    """``scorecard-history.jsonl`` under the loop home (one line per scorecard gate run)."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "scorecard-history.jsonl"

    def append(
        self,
        result: ScorecardGateResult,
        *,
        run_id: str,
        issue: str,
        now: datetime | None = None,
    ) -> None:
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        append_jsonl(self.path, {"at": stamp, "run_id": run_id, "issue": issue, **result.summary()})

    def load(self, limit: int = 2000) -> list[dict[str, Any]]:
        return read_jsonl(self.path, limit)
