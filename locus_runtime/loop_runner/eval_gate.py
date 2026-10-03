"""The loop's eval gate: a synthetic DeepSWE plumbing run on the model chain (LOCUS-339).

After the verifier suite passes, the runner can run the ``synthetic-mini``
DeepSWE tasks from ``apps/evals`` with the configured model chain (hosted NIM,
then local Ollama, D-21) driving the SWE agent. The resolve rate is appended to
``eval-history.jsonl`` under ``LOCUS_LOOP_HOME`` and reported in the PR body.

What it measures: the model chain plus the *runner's* installed harness, not
the PR's code. Running the PR's harness would mean executing agent-authored
code on the host with model egress; the per-PR code is covered by the verifier
suite inside the jail instead.

Modes (``LOCUS_LOOP_EVAL_GATE``): ``off``; ``advisory`` (default for
``lattix loop``: run, record, report; never blocks a PR or a merge);
``required`` (the D-22 auto-merge additionally holds unless the eval passed).

Fail honest: when no model is reachable, or the eval harness is unavailable,
the result is ``skipped`` with the reason. A skipped or errored eval is never
recorded or reported as a pass.
"""

from __future__ import annotations

import importlib
import math
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from locus_runtime.gateway import redact_text
from locus_runtime.harness.llm import ChatClient
from locus_runtime.loop_runner.perf_budget import append_jsonl, read_jsonl

EvalStatus = Literal["pass", "fail", "skipped", "error"]
EvalMode = Literal["off", "advisory", "required"]
EVAL_MODES: tuple[str, ...] = ("off", "advisory", "required")
DEFAULT_THRESHOLD = 0.30


def parse_eval_mode(value: str | None, default: EvalMode = "advisory") -> EvalMode:
    raw = str(value or "").strip().lower()
    aliases: dict[str, EvalMode] = {
        "0": "off",
        "false": "off",
        "no": "off",
        "1": "advisory",
        "true": "advisory",
        "on": "advisory",
        "yes": "advisory",
    }
    if raw in EVAL_MODES:
        return raw  # type: ignore[return-value]
    return aliases.get(raw, default)


@dataclass(frozen=True)
class EvalGateResult:
    status: EvalStatus
    reason: str = ""
    resolve_rate: float | None = None
    threshold: float = DEFAULT_THRESHOLD
    n_instances: int = 0
    model: str = ""

    def __post_init__(self) -> None:
        # Never a pass without a measured rate that meets the threshold.
        if self.status == "pass" and (
            self.resolve_rate is None or self.resolve_rate < self.threshold
        ):
            object.__setattr__(self, "status", "fail")

    @classmethod
    def skipped(cls, reason: str, *, threshold: float = DEFAULT_THRESHOLD) -> EvalGateResult:
        return cls("skipped", redact_text(reason, limit=300), threshold=threshold)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def markdown(self) -> list[str]:
        if self.status in {"skipped", "error"}:
            return [f"- {self.status}: {_clean(self.reason)}"]
        rate = f"{(self.resolve_rate or 0.0) * 100:.1f}%"
        return [
            f"- **{self.status}**: resolve rate {rate} on {self.n_instances} synthetic "
            f"DeepSWE task(s) (threshold {self.threshold * 100:.0f}%)",
            f"- Model chain: `{_clean(self.model)}`",
        ]


def _clean(text: Any) -> str:
    return redact_text(str(text or ""), limit=300).replace("<!--", "&lt;!--").replace("`", "'")


def eval_merge_hold_reason(mode: str, status: str | None) -> str:
    """Why the D-22 merge must hold for the eval gate ('' = no hold). Pure."""
    if parse_eval_mode(mode, "off") != "required":
        return ""
    if status == "pass":
        return ""
    return f"eval gate is required but did not pass (status: {status or 'not run'})"


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
class EvalHistory:
    """``eval-history.jsonl`` under the loop home (one line per eval gate run)."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "eval-history.jsonl"

    def append(
        self, result: EvalGateResult, *, run_id: str, issue: str, now: datetime | None = None
    ) -> None:
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        append_jsonl(self.path, {"at": stamp, "run_id": run_id, "issue": issue, **result.to_dict()})

    def load(self, limit: int = 2000) -> list[dict[str, Any]]:
        return read_jsonl(self.path, limit)


# --------------------------------------------------------------------------- #
# Runner seam + default implementation (apps/evals)
# --------------------------------------------------------------------------- #
@dataclass
class EvalRequest:
    client_factory: Callable[[], ChatClient]
    output_dir: Path
    repo_path: Path
    threshold: float = DEFAULT_THRESHOLD
    max_steps: int = 20
    max_seconds: float = 900.0
    instance_ids: list[str] = field(default_factory=list)
    run_kwargs: Mapping[str, Any] = field(default_factory=dict)  # tests: gateway doubles


EvalRunner = Callable[[EvalRequest], EvalGateResult]


def _import_run_eval(repo_path: Path) -> tuple[Any, Any]:
    """``(EvalConfig, run_eval)`` from apps/evals (installed, or the runner's checkout)."""
    try:
        config_mod = importlib.import_module("locus_evals.config")
        runner_mod = importlib.import_module("locus_evals.runner")
    except ImportError:
        source = (Path(repo_path) / "apps" / "evals").resolve()
        if not (source / "locus_evals").is_dir():
            raise
        if str(source) not in sys.path:
            sys.path.append(str(source))  # the runner's own checkout, never the run's worktree
        config_mod = importlib.import_module("locus_evals.config")
        runner_mod = importlib.import_module("locus_evals.runner")
    return config_mod.EvalConfig, runner_mod.run_eval


def default_eval_runner(request: EvalRequest) -> EvalGateResult:
    """Preflight the model chain, then run synthetic-mini with it driving the agent."""
    threshold = request.threshold
    try:
        client = request.client_factory()
        client.complete(
            [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=8
        )
    except Exception as exc:  # noqa: BLE001 - unreachable model chain: skipped, never a pass
        return EvalGateResult.skipped(
            f"no model in the chain is reachable ({type(exc).__name__})", threshold=threshold
        )
    model = f"{getattr(client, 'provider', '')}/{getattr(client, 'model', '')}"
    try:
        eval_config_cls, run_eval = _import_run_eval(request.repo_path)
    except ImportError:
        return EvalGateResult.skipped(
            "the apps/evals harness is not installed", threshold=threshold
        )
    config = eval_config_cls(
        mode="plumbing",
        dataset="synthetic-mini",
        instance_ids=list(request.instance_ids),
        seeds=[0],
        max_steps=request.max_steps,
        max_seconds=request.max_seconds,
        threshold=threshold,
        output_dir=str(request.output_dir),
    )
    try:
        run = run_eval(
            config,
            client_factory=lambda _task: client,
            output_dir=request.output_dir,
            **dict(request.run_kwargs),
        )
    except Exception as exc:  # noqa: BLE001 - an eval that cannot run is not a result
        return EvalGateResult(
            "error", f"the eval run failed ({type(exc).__name__})", threshold=threshold, model=model
        )
    summary: Mapping[str, Any] = getattr(run, "summary", None) or {}
    raw_rate = summary.get("resolve_rate_mean")
    try:
        rate = float(raw_rate) if raw_rate is not None else math.nan
        n_instances = int(summary.get("n_instances") or 0)
    except (TypeError, ValueError):
        return EvalGateResult("error", "the eval produced no resolve rate", threshold=threshold)
    if not math.isfinite(rate):
        return EvalGateResult("error", "the eval produced no resolve rate", threshold=threshold)
    if n_instances <= 0:
        return EvalGateResult("error", "the eval ran no instances", threshold=threshold)
    return EvalGateResult(
        "pass" if rate >= threshold else "fail",
        resolve_rate=round(rate, 4),
        threshold=threshold,
        n_instances=n_instances,
        model=model,
    )
