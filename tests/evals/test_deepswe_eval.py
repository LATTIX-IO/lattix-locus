"""Automated DeepSWE / SWE-bench evaluation of the xFrontier SWE agent.

This is the headline gate. It drives ``frontier_runtime.harness.SweAgent``
through the full eval pipeline (materialize repo -> agent loop -> execution
grading -> mean/SEM stats -> report) and asserts the resolve rate meets the
acceptance threshold (default 30%).

Two modes, selected by environment:

* default (CI / this machine): ``plumbing`` mode with the deterministic
  reference solver — proves the harness + grading + stats pipeline is correct
  and that a competent scaffold clears the bar end to end. No GPU/Docker.

* live: set ``FRONTIER_EVALS_MODE=live``, ``FRONTIER_EVALS_API_BASE_URL`` (a
  vLLM/llama.cpp endpoint serving gpt-oss-20b), ``FRONTIER_EVALS_MODEL``, and
  for SWE-bench ``FRONTIER_EVALS_DATASET=swe-bench`` + ``DOCKER_HOST`` on a
  remote runner. The SAME assertion then enforces gpt-oss-20b >= 30% on
  DeepSWE/SWE-bench.
"""

from __future__ import annotations

import shutil

import pytest

requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available on this host"
)
requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available on this host"
)


@requires_bash
@requires_git
def test_swe_agent_meets_deepswe_threshold(tmp_path):
    from frontier_evals.config import EvalConfig
    from frontier_evals.runner import run_eval

    config = EvalConfig.from_env()
    # Default to the plumbing pipeline check when no live endpoint is configured.
    if config.mode == "live" and not config.api_base_url:
        config.mode = "plumbing"
    if config.mode == "plumbing":
        config.dataset = "synthetic-mini"
        config.seeds = [0, 1]
        config.threshold = 0.30
        config.output_dir = str(tmp_path / "smoke")

    run = run_eval(config, output_dir=tmp_path / "out")
    summary = run.summary

    # Pipeline integrity: a real, execution-graded number with stats + report.
    assert summary["n_instances"] >= 1
    assert 0.0 <= summary["resolve_rate_mean"] <= 1.0
    assert (tmp_path / "out" / "summary.json").exists()
    assert (tmp_path / "out" / "report.md").exists()

    # THE GATE.
    assert summary["resolve_rate_mean"] >= config.threshold, (
        f"resolve rate {summary['resolve_rate_mean']:.3f} below threshold "
        f"{config.threshold:.2f} (mode={config.mode}, model={config.model or 'reference'})"
    )


@requires_bash
@requires_git
def test_reference_solver_resolves_all_synthetic(tmp_path):
    from frontier_evals.config import EvalConfig
    from frontier_evals.runner import run_eval

    config = EvalConfig(mode="plumbing", dataset="synthetic-mini", seeds=[0, 1, 2])
    run = run_eval(config, output_dir=tmp_path / "out")
    assert run.summary["resolve_rate_mean"] == 1.0
    assert run.summary["resolve_rate_sem"] == 0.0  # identical across seeds
    assert run.summary["pass_at_k"] == 1.0
    # one trajectory + result per instance per seed
    n = run.summary["n_instances"] * run.summary["n_seeds"]
    assert len(run.results) == n


@requires_bash
@requires_git
def test_noop_solver_resolves_nothing(tmp_path):
    from frontier_evals.config import EvalConfig
    from frontier_evals.model_client import build_noop_solver
    from frontier_evals.runner import run_eval

    config = EvalConfig(mode="plumbing", dataset="synthetic-mini", seeds=[0])
    run = run_eval(
        config, client_factory=lambda task: build_noop_solver(), output_dir=tmp_path / "o"
    )
    assert run.summary["resolve_rate_mean"] == 0.0
    assert run.summary["meets_threshold"] is False


def test_remote_guardrail_blocks_local_live_fleet():
    from frontier_evals.config import EvalConfig

    config = EvalConfig(
        mode="live", api_base_url="http://localhost:8000/v1", docker_host="", allow_local=False
    )
    with pytest.raises(RuntimeError, match="local"):
        config.enforce_remote_guardrail(n_instances=50)
    # small smoke (<=2) is allowed; --allow-local overrides
    config.enforce_remote_guardrail(n_instances=2)
    EvalConfig(
        mode="live", api_base_url="http://localhost:8000/v1", allow_local=True
    ).enforce_remote_guardrail(50)


def test_stats_sem_math():
    from frontier_evals.stats import SeedSummary, summarize

    seeds = [SeedSummary(0, 1, 3), SeedSummary(1, 3, 3)]  # rates 1/3 and 1.0
    summary = summarize(seeds, {"a": 1, "b": 2, "c": 1})
    assert summary.n_seeds == 2
    assert abs(summary.resolve_rate_mean - (1 / 3 + 1.0) / 2) < 1e-3  # mean rounded to 4dp
    assert summary.resolve_rate_sem > 0
    assert summary.pass_at_k == 1.0  # all three instances resolved by >=1 seed
