"""``python -m locus_evals.suite``: run the RSI suite, seal the store, compare scorecards.

    # one trial of both splits against a checkout, default runtime, local Ollama
    python -m locus_evals.suite run --candidate . --trials 1 --output-dir out/
    python -m locus_evals.suite run --candidate . --splits heldout --record-variant
    python -m locus_evals.suite compare baseline.json candidate.json
    python -m locus_evals.suite record docs/development/scorecard-baseline-2026-10.json --branch main
    python -m locus_evals.suite install          # seal the store, print its digest
    python -m locus_evals.suite list

Needs ``LOCUS_OPA_BIN`` (or an installed OPA) and a reachable keyless model
endpoint (``OLLAMA_BASE_URL``). The held-out split is private (LOCUS-382): sync it
first with ``lattix evals sync`` (or point ``LOCUS_EVAL_HELDOUT_DIR`` at a local
private folder); without it the held-out split is ``skipped: not synced`` and the
scorecard is never promoted. See ``docs/development/rsi-scorecard.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from locus_runtime.rsi.scorecard import Scorecard, compare
from locus_tooling.evals_sync import resolve_heldout

from locus_evals.suite import SUITE_VERSION, TASKS_DIR
from locus_evals.suite.loader import load_task, load_tasks, task_files
from locus_evals.suite.store import install


def _csv(text: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in text.split(",") if p.strip())


def _load(path: str) -> Scorecard:
    return Scorecard.model_validate_json(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m locus_evals.suite")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the suite against a candidate checkout")
    run.add_argument("--candidate", default=".", help="candidate checkout (default: .)")
    run.add_argument("--python", default="", help="candidate interpreter (default: its .venv)")
    run.add_argument("--splits", default="dev,heldout")
    run.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--model", default="gpt-oss:20b-ctx32k")
    run.add_argument(
        "--runtime", default="", help="agent runtime (default: the candidate's default)"
    )
    run.add_argument("--engine", choices=("auto", "inspect", "builtin"), default="auto")
    run.add_argument("--output-dir", default="rsi-scorecard")
    run.add_argument(
        "--store-root", default="", help="suite store (default: <app_home>/evals/suite-store)"
    )
    run.add_argument("--work-dir", default="")
    run.add_argument("--max-steps", type=int, default=30)
    run.add_argument("--max-seconds", type=float, default=600.0)
    run.add_argument("--branch", default="")
    run.add_argument("--baseline", default="", help="scorecard JSON to compare against")
    run.add_argument(
        "--record-variant", action="store_true", help="archive under LOCUS_LOOP_HOME/variants"
    )

    cmp_ = sub.add_parser("compare", help="apply the promotion rule to two scorecards")
    cmp_.add_argument("baseline")
    cmp_.add_argument("candidate")

    rec = sub.add_parser("record", help="archive a scorecard JSON as a variant (e.g. a baseline)")
    rec.add_argument("scorecard")
    rec.add_argument("--branch", default="", help="override the scorecard's branch (e.g. main)")

    sub.add_parser("install", help="copy + seal the suite store and print its digest")
    sub.add_parser("list", help="list the tasks per split")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "list":
        for task in load_tasks(TASKS_DIR, ("dev",)):
            print(f"{task.split:8} {task.id:32} {task.category:22} {task.kind}")
        heldout = resolve_heldout()
        if heldout.path is None:
            print(f"heldout  {heldout.describe()}")
        else:
            for path in task_files(heldout.path, "."):
                task = load_task(path, "heldout")
                print(f"{task.split:8} {task.id:32} {task.category:22} {task.kind}")
        return 0
    if args.command == "install":
        sealed = install(TASKS_DIR)
        print(
            json.dumps(
                {
                    "root": str(sealed.root),
                    "digest": sealed.digest,
                    "version": SUITE_VERSION,
                    "split_digests": dict(sealed.split_digests),
                    "heldout": sealed.heldout.describe(),
                },
                indent=1,
            )
        )
        return 0
    if args.command == "record":
        from locus_runtime.loop_runner.state import default_loop_home
        from locus_runtime.rsi.variants import VariantArchive

        card = _load(args.scorecard)
        if args.branch:
            card = card.model_copy(update={"branch": args.branch})
        path = VariantArchive(default_loop_home()).record(card, source="import")
        print(json.dumps({"variant": str(path), "branch": card.branch, "status": card.status}))
        return 0
    if args.command == "compare":
        verdict = compare(_load(args.baseline), _load(args.candidate))
        print(json.dumps(verdict.model_dump(mode="json"), indent=1))
        return 0 if verdict.promote else 3

    from locus_evals.suite.runner import SuiteRunConfig, default_python_for, run_suite

    checkout = Path(args.candidate).resolve()
    cfg = SuiteRunConfig(
        candidate_checkout=checkout,
        output_dir=Path(args.output_dir).resolve(),
        candidate_python=args.python or default_python_for(checkout),
        splits=_csv(args.splits),
        trials=max(1, args.trials),
        task_ids=_csv(args.tasks),
        model=args.model,
        runtime=args.runtime,
        engine=args.engine,
        branch=args.branch,
        store_root=Path(args.store_root) if args.store_root else None,
        work_dir=Path(args.work_dir) if args.work_dir else None,
        max_steps=args.max_steps,
        max_seconds=args.max_seconds,
    )
    from locus_runtime import telemetry

    if not telemetry.posture().configured:
        from locus_runtime.telemetry import TelemetrySettings

        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        telemetry.configure(
            TelemetrySettings(
                local_enabled=True, db_path=str(cfg.output_dir / "evaluator-telemetry.db")
            )
        )
    result = run_suite(cfg)
    summary = {
        "scorecard": str(result.scorecard_path),
        "status": result.scorecard.status,
        "engine": result.scorecard.engine,
        "splits": {k: v.model_dump() for k, v in result.scorecard.splits.items()},
    }
    comparison = None
    if args.baseline:
        comparison = compare(_load(args.baseline), result.scorecard)
        summary["comparison"] = comparison.model_dump(mode="json")
    if args.record_variant and result.scorecard.git_sha:
        from locus_runtime.loop_runner.state import default_loop_home
        from locus_runtime.rsi.variants import VariantArchive

        summary["variant"] = str(
            VariantArchive(default_loop_home()).record(
                result.scorecard, comparison=comparison, source="cli"
            )
        )
    telemetry.force_flush()
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
