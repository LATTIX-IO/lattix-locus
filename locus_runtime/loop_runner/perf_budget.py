"""Performance budget suite for the loop's pre-PR gate (LOCUS-339).

Two halves with different trust:

* **Measurement** (``python -m locus_runtime.loop_runner.perf_budget``) runs
  *inside the run's workspace and jail*, against the changed code. It measures,
  in-process, the median latency over N iterations (after warm-up) of:

  - ``health_ms``          -- ``GET /health`` on the backend app (FastAPI TestClient)
  - ``gateway_decision_ms`` -- one ``Gateway`` authorization (an in-process allow engine,
    so this is the gateway's own overhead: capability checks, audit, redaction)
  - ``policy_decision_ms``  -- one ``agent_policy`` decision on the configured policy
    engine (OPA). Reported as ``null`` when no engine can start here.

  It prints one ``LOCUS_PERF_RESULT {json}`` line. Medians, warm-up and an
  in-process loop keep it deterministic enough for a gate.

* **Comparison** (:func:`compare_to_baseline`, :class:`PerfStore`) runs in the
  *runner*: the baseline and the history live under ``LOCUS_LOOP_HOME``, which
  the agent cannot write. The first measurement of a metric records its
  baseline. A later median fails the gate when it exceeds the baseline by more
  than ``tolerance`` (fraction) **and** by more than ``min_delta_ms`` (absolute
  noise floor), or when it exceeds the metric's absolute budget.

This module is under ``/locus_runtime/loop_runner/``, a D-22 protected path:
a PR that edits the measurement is never auto-merged.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

RESULT_PREFIX = "LOCUS_PERF_RESULT "
METRICS: tuple[str, ...] = ("health_ms", "gateway_decision_ms", "policy_decision_ms")
#: Absolute ceilings (ms, median). Generous: they catch pathological regressions;
#: the baseline comparison catches relative ones.
DEFAULT_BUDGETS_MS: dict[str, float] = {
    "health_ms": 100.0,
    "gateway_decision_ms": 50.0,
    "policy_decision_ms": 250.0,
}
_MAX_HISTORY_READ = 2000


# --------------------------------------------------------------------------- #
# Settings and comparison (pure)
# --------------------------------------------------------------------------- #
def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(str(os.getenv(name) or "").strip() or default)
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= minimum else default


@dataclass(frozen=True)
class PerfSettings:
    tolerance: float = 0.5  # +50 % over baseline fails ...
    min_delta_ms: float = 2.0  # ... only when it is also more than 2 ms slower
    budgets_ms: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_BUDGETS_MS))

    @classmethod
    def from_env(cls) -> PerfSettings:
        budgets = dict(DEFAULT_BUDGETS_MS)
        for metric in METRICS:
            env = f"LOCUS_LOOP_PERF_BUDGET_{metric.upper()}"
            budgets[metric] = _env_float(env, budgets[metric], minimum=0.001)
        return cls(
            tolerance=_env_float("LOCUS_LOOP_PERF_TOLERANCE", 0.5),
            min_delta_ms=_env_float("LOCUS_LOOP_PERF_MIN_DELTA_MS", 2.0),
            budgets_ms=budgets,
        )


@dataclass(frozen=True)
class PerfVerdict:
    status: Literal["pass", "fail"]
    regressions: tuple[str, ...]
    recorded: tuple[str, ...]  # metrics whose baseline this measurement records
    details: dict[str, dict[str, Any]]

    def summary(self) -> str:
        if self.regressions:
            return "regression: " + "; ".join(self.regressions)
        if self.recorded:
            return "baseline recorded for " + ", ".join(self.recorded)
        return "within budget and baseline tolerance"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def compare_to_baseline(
    measured: Mapping[str, Any],
    baseline: Mapping[str, Any],
    settings: PerfSettings | None = None,
) -> PerfVerdict:
    """Pass/fail of ``measured`` medians against the stored ``baseline`` (pure)."""
    cfg = settings or PerfSettings()
    regressions: list[str] = []
    recorded: list[str] = []
    details: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        value = _number(measured.get(metric))
        if value is None:
            details[metric] = {"status": "not measured"}
            continue
        budget = float(cfg.budgets_ms.get(metric, math.inf))
        base = _number(baseline.get(metric))
        entry: dict[str, Any] = {"median_ms": round(value, 3), "budget_ms": budget}
        if value > budget:
            regressions.append(f"{metric} {value:.2f} ms exceeds the {budget:g} ms budget")
            entry["status"] = "over budget"
        elif base is None:
            recorded.append(metric)
            entry["status"] = "baseline recorded"
        else:
            limit = max(base * (1.0 + cfg.tolerance), base + cfg.min_delta_ms)
            entry.update(baseline_ms=round(base, 3), limit_ms=round(limit, 3))
            if value > limit:
                regressions.append(
                    f"{metric} {value:.2f} ms vs baseline {base:.2f} ms (limit {limit:.2f} ms)"
                )
                entry["status"] = "regressed"
            else:
                entry["status"] = "ok"
        details[metric] = entry
    return PerfVerdict(
        status="fail" if regressions else "pass",
        regressions=tuple(regressions),
        recorded=tuple(m for m in recorded if not regressions),
        details=details,
    )


def parse_perf_output(text: str) -> dict[str, float | None] | None:
    """The measurement from the suite's stdout (last result line), or None."""
    for line in reversed(str(text or "").splitlines()):
        line = line.strip()
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            data = json.loads(line[len(RESULT_PREFIX) :])
        except ValueError:
            return None
        metrics = data.get("metrics") if isinstance(data, dict) else None
        if not isinstance(metrics, dict):
            return None
        return {m: _number(metrics.get(m)) for m in METRICS}
    return None


# --------------------------------------------------------------------------- #
# Baseline + history store (runner side, under LOCUS_LOOP_HOME)
# --------------------------------------------------------------------------- #
class PerfStore:
    """``perf-baseline.json`` and ``perf-history.jsonl`` under the loop home."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.baseline_path = self.home / "perf-baseline.json"
        self.history_path = self.home / "perf-history.jsonl"

    def baseline(self) -> dict[str, float]:
        try:
            data = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        metrics = data.get("metrics") if isinstance(data, dict) else None
        if not isinstance(metrics, dict):
            return {}
        return {m: v for m in METRICS if (v := _number(metrics.get(m))) is not None}

    def history(self, limit: int = _MAX_HISTORY_READ) -> list[dict[str, Any]]:
        return read_jsonl(self.history_path, limit)

    def evaluate(
        self,
        measured: Mapping[str, Any],
        *,
        run_id: str,
        settings: PerfSettings | None = None,
        now: datetime | None = None,
    ) -> PerfVerdict:
        """Compare against the baseline, record new baselines, append to the history."""
        verdict = compare_to_baseline(measured, self.baseline(), settings)
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if verdict.recorded:
            merged = {**self.baseline()}
            for metric in verdict.recorded:
                value = _number(measured.get(metric))
                if value is not None:
                    merged[metric] = round(value, 4)
            from locus_runtime.loop_runner.state import write_json_atomic

            write_json_atomic(
                self.baseline_path, {"metrics": merged, "recorded_at": stamp, "run_id": run_id}
            )
        append_jsonl(
            self.history_path,
            {
                "at": stamp,
                "run_id": run_id,
                "status": verdict.status,
                "metrics": {m: _number(measured.get(m)) for m in METRICS},
                "regressions": list(verdict.regressions),
            },
        )
        return verdict


def make_perf_evaluator(
    store: PerfStore, *, run_id: str, settings: PerfSettings | None = None
) -> Callable[[str], tuple[Literal["pass", "fail"], str, dict[str, Any]]]:
    """The gate's perf evaluator: parse the suite's stdout, compare, record."""

    def evaluate(stdout: str) -> tuple[Literal["pass", "fail"], str, dict[str, Any]]:
        measured = parse_perf_output(stdout)
        if measured is None:
            return "fail", "the performance suite printed no result", {}
        verdict = store.evaluate(measured, run_id=run_id, settings=settings)
        return verdict.status, verdict.summary(), {"metrics": verdict.details}

    return evaluate


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(record), sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path, limit: int = _MAX_HISTORY_READ) -> list[dict[str, Any]]:
    """The last ``limit`` JSON object lines of ``path`` (bad lines are skipped)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Measurement (runs in the workspace, against the changed code)
# --------------------------------------------------------------------------- #
def median_ms(fn: Callable[[], Any], *, iterations: int, warmup: int = 3) -> float:
    """Median wall time of ``fn`` in milliseconds over ``iterations`` calls."""
    for _ in range(max(0, warmup)):
        fn()
    samples: list[float] = []
    for _ in range(max(1, iterations)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return float(statistics.median(samples))


def _measure_health(iterations: int) -> float | None:
    backend = Path.cwd() / "apps" / "backend"
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from fastapi.testclient import TestClient

        import app.main as backend_main  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - no backend in this repository: not measured
        return None
    client = TestClient(backend_main.app)

    def call() -> None:
        if client.get("/health").status_code != 200:
            raise RuntimeError("health endpoint did not return 200")

    return median_ms(call, iterations=iterations)


class _AllowEngine:
    """In-process allow-all engine: isolates the gateway's own decision overhead."""

    name = "perf-allow"

    def decide(self, policy: str, input: dict[str, Any]) -> Any:  # noqa: A002 - interface
        from locus_runtime.policy_engine import Decision

        return Decision(allow=True, reasons=[], policy_version="perf", backend=self.name)

    def close(self) -> None:
        return None


def _measure_gateway(iterations: int) -> float | None:
    from locus_runtime.gateway import Capabilities, Gateway

    gateway = Gateway(_AllowEngine(), lambda _record: None)
    root = str(Path.cwd())
    session = gateway.open_session(
        run_id="perf-budget",
        principal="perf-budget",
        engine="perf",
        capabilities=Capabilities(
            allowed_tools=frozenset({"read_file"}), read_roots=(root,), write_roots=(root,)
        ),
    )
    target = str(Path(root) / "README.md")
    try:
        return median_ms(
            lambda: session.authorize(kind="file_read", tool="read_file", target=target),
            iterations=iterations,
        )
    finally:
        session.close()


def _measure_policy(iterations: int) -> float | None:
    try:
        from locus_runtime.policy_engine import build_policy_engine

        engine: Any = build_policy_engine()
        start = getattr(engine, "start", None)
        if callable(start):
            start()
    except Exception:  # noqa: BLE001 - no engine here: not measured (never a fake number)
        return None
    payload = {
        "tool": "read_file",
        "action": "read_file",
        "actor": "perf-budget",
        "allowed_tools": ["read_file"],
    }
    try:
        return median_ms(lambda: engine.decide("agent_policy", payload), iterations=iterations)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            engine.close()
        except Exception:  # noqa: BLE001 - cleanup
            pass


def measure(iterations: int = 30) -> dict[str, float | None]:
    results: dict[str, float | None] = {}
    for metric, probe in (
        ("health_ms", _measure_health),
        ("gateway_decision_ms", _measure_gateway),
        ("policy_decision_ms", _measure_policy),
    ):
        try:
            value = probe(iterations)
        except Exception:  # noqa: BLE001 - a crashing probe is "not measured"
            value = None
        results[metric] = None if value is None else round(value, 4)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Locus loop performance budget suite")
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args(argv)
    iterations = min(max(5, int(args.iterations)), 500)
    metrics = measure(iterations)
    print(
        RESULT_PREFIX + json.dumps({"iterations": iterations, "metrics": metrics}, sort_keys=True)
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
