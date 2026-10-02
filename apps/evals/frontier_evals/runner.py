"""Eval orchestration: instance x seed -> agent -> grade -> stats -> report."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from frontier_runtime.harness.llm import ChatClient
from frontier_runtime.harness.loop import LoopBudgets
from frontier_runtime.harness.model_profiles import resolve_profile
from frontier_runtime.harness.swe_agent import SweAgent, SweTask

from frontier_evals.config import EvalConfig
from frontier_evals.datasets import materialize_synthetic, synthetic_instances
from frontier_evals.grading import grade_synthetic
from frontier_evals.model_client import build_live_client, build_reference_solver
from frontier_evals.report import write_report
from frontier_evals.stats import SeedSummary, summarize


@dataclass
class InstanceResult:
    instance_id: str
    seed: int
    outcome: str
    resolved: bool
    steps: int
    elapsed_seconds: float
    telemetry: dict[str, Any]
    detail: str
    patch_bytes: int


@dataclass
class EvalRun:
    config: dict[str, Any]
    results: list[InstanceResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# A factory turns a SweTask into the ChatClient used to solve it. This lets
# plumbing mode build a per-task reference solver while live mode reuses one
# model client.
ClientFactory = Callable[[SweTask], ChatClient]


def _client_factory(config: EvalConfig) -> ClientFactory:
    if config.mode == "live":
        client = build_live_client(config)
        return lambda task: client
    # plumbing: reference solver derived from each task's known fix recipe
    return lambda task: build_reference_solver(task.metadata.get("fix", []))


def run_eval(
    config: EvalConfig,
    *,
    client_factory: ClientFactory | None = None,
    output_dir: Path | None = None,
) -> EvalRun:
    if config.dataset == "swe-bench":
        return run_live_swebench(config, output_dir=output_dir)
    if config.dataset != "synthetic-mini":
        raise NotImplementedError(
            f"unknown dataset {config.dataset!r}; use 'synthetic-mini' or 'swe-bench'."
        )
    instances = synthetic_instances(config.instance_ids)
    config.enforce_remote_guardrail(len(instances))
    factory = client_factory or _client_factory(config)
    out_dir = Path(output_dir or config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    budgets = LoopBudgets(max_steps=config.max_steps, max_seconds=config.max_seconds)
    spec = _load_agent_spec(config)  # shipped SDET agent, if requested
    run = EvalRun(config=_config_public(config))
    seed_summaries: list[SeedSummary] = []
    per_instance_pass: dict[str, int] = {inst.instance_id: 0 for inst in instances}

    for seed in config.seeds:
        resolved_count = 0
        for inst in instances:
            # ignore_cleanup_errors: on Windows, git can briefly hold a handle in
            # the workspace, making rmtree on context exit fail spuriously.
            with tempfile.TemporaryDirectory(
                prefix=f"eval-{inst.instance_id}-", ignore_cleanup_errors=True
            ) as tmp:
                root = Path(tmp)
                task = materialize_synthetic(inst, root, seed=seed)
                client = factory(task)
                profile = _resolve_profile_for(config, spec, client)
                traj_dir = out_dir / "instances" / inst.instance_id / f"seed-{seed}"
                traj_dir.mkdir(parents=True, exist_ok=True)
                agent = SweAgent(
                    client=client,
                    profile=profile,
                    budgets=budgets,
                    trajectory_dir=traj_dir,
                    system_prompt_override=spec.system_prompt if spec else None,
                )
                result = agent.solve(task)
                grade = grade_synthetic(task, result, str(root))
                if grade.resolved:
                    resolved_count += 1
                    per_instance_pass[inst.instance_id] += 1
                ir = InstanceResult(
                    instance_id=inst.instance_id,
                    seed=seed,
                    outcome=result.outcome.value,
                    resolved=grade.resolved,
                    steps=result.steps,
                    elapsed_seconds=round(result.elapsed_seconds, 3),
                    telemetry=result.telemetry,
                    detail=grade.detail,
                    patch_bytes=len(result.patch.encode("utf-8")),
                )
                run.results.append(ir)
                (traj_dir / "result.json").write_text(
                    json.dumps(asdict(ir), indent=2), encoding="utf-8"
                )
        seed_summaries.append(SeedSummary(seed=seed, resolved=resolved_count, total=len(instances)))

    summary = summarize(seed_summaries, per_instance_pass)
    run.summary = {
        **asdict(summary),
        "threshold": config.threshold,
        "meets_threshold": summary.meets_threshold(config.threshold),
        "per_instance_pass": per_instance_pass,
    }
    write_report(run, out_dir)
    return run


def run_live_swebench(
    config: EvalConfig, *, output_dir: Path | None = None
) -> EvalRun:  # pragma: no cover - live only
    """Run real SWE-bench instances in Docker against the model under test.

    Untested in CI (needs a GPU-served model + Docker on a remote runner). Wired
    so the gpt-oss-20b DeepSWE run is a single command from a runner box.
    """
    from frontier_evals.datasets import swebench_tasks
    from frontier_evals.docker_env import instance_container
    from frontier_evals.grading import grade_swebench

    if not config.instance_ids:
        raise RuntimeError("swe-bench mode requires explicit --instance-ids / FRONTIER_EVALS ids")
    config.enforce_remote_guardrail(len(config.instance_ids))
    client = build_live_client(config)
    spec = _load_agent_spec(config)
    out_dir = Path(output_dir or config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    budgets = LoopBudgets(max_steps=config.max_steps, max_seconds=config.max_seconds)

    run = EvalRun(config=_config_public(config))
    seed_summaries: list[SeedSummary] = []
    per_instance_pass: dict[str, int] = {iid: 0 for iid in config.instance_ids}

    for seed in config.seeds:
        resolved_count = 0
        for iid in config.instance_ids:
            with instance_container(iid, docker_host=config.docker_host) as container_id:
                task = swebench_tasks(
                    [iid],
                    docker_host=config.docker_host,
                    container_resolver=lambda _iid: container_id,
                    seed=seed,
                )[0]
                profile = _resolve_profile_for(config, spec, client)
                traj_dir = out_dir / "instances" / iid / f"seed-{seed}"
                traj_dir.mkdir(parents=True, exist_ok=True)
                agent = SweAgent(
                    client=client,
                    profile=profile,
                    budgets=budgets,
                    trajectory_dir=traj_dir,
                    system_prompt_override=spec.system_prompt if spec else None,
                )
                result = agent.solve(task)
                grade = grade_swebench(task, result)
                if grade.resolved:
                    resolved_count += 1
                    per_instance_pass[iid] += 1
                ir = InstanceResult(
                    instance_id=iid,
                    seed=seed,
                    outcome=result.outcome.value,
                    resolved=grade.resolved,
                    steps=result.steps,
                    elapsed_seconds=round(result.elapsed_seconds, 3),
                    telemetry=result.telemetry,
                    detail=grade.detail,
                    patch_bytes=len(result.patch.encode("utf-8")),
                )
                run.results.append(ir)
                (traj_dir / "result.json").write_text(
                    json.dumps(asdict(ir), indent=2), encoding="utf-8"
                )
        seed_summaries.append(
            SeedSummary(seed=seed, resolved=resolved_count, total=len(config.instance_ids))
        )

    summary = summarize(seed_summaries, per_instance_pass)
    run.summary = {
        **asdict(summary),
        "threshold": config.threshold,
        "meets_threshold": summary.meets_threshold(config.threshold),
        "per_instance_pass": per_instance_pass,
    }
    write_report(run, out_dir)
    return run


def _load_agent_spec(config: EvalConfig):
    if not config.agent_id:
        return None
    from frontier_runtime.harness.agent_library import load_agent_spec

    return load_agent_spec(config.agent_id)


def _resolve_profile_for(config: EvalConfig, spec, client):
    """Profile precedence: explicit --profile > shipped agent spec > auto from model."""
    if config.profile_id:
        return resolve_profile(
            getattr(client, "provider", "reference"),
            getattr(client, "model", ""),
            profile_id=config.profile_id,
        )
    if spec is not None:
        return spec.profile()
    return resolve_profile(getattr(client, "provider", "reference"), getattr(client, "model", ""))


def _config_public(config: EvalConfig) -> dict[str, Any]:
    public = asdict(config)
    public.pop("api_key", None)
    return public
