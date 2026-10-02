"""frontier-evals CLI.

    frontier-evals smoke                 # synthetic plumbing eval (reference solver)
    frontier-evals run --mode live \\
        --api-base-url http://runner:8000/v1 --model gpt-oss-20b \\
        --dataset swe-bench --seeds 0,1,2,3,4

The live SWE-bench path runs the same SweAgent against Docker instances on a
remote runner; the localhost guardrail blocks accidental local fleets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from frontier_evals.config import EvalConfig
from frontier_evals.runner import run_eval


@click.group()
def cli() -> None:
    """Lattix xFrontier evaluation harness."""


@cli.command()
@click.option("--output-dir", default="eval-results/smoke", help="Where to write results.")
@click.option("--seeds", default="0", help="Comma-separated seeds.")
def smoke(output_dir: str, seeds: str) -> None:
    """Run the synthetic plumbing eval with the reference solver (no GPU/Docker)."""
    config = EvalConfig(
        mode="plumbing",
        dataset="synthetic-mini",
        seeds=[int(s) for s in seeds.split(",") if s.strip()],
        output_dir=output_dir,
    )
    run = run_eval(config, output_dir=Path(output_dir))
    _print_summary(run)
    # Reference solver must resolve everything — a failing smoke means the
    # harness/grading pipeline is broken.
    if not run.summary.get("meets_threshold"):
        click.echo("smoke eval below threshold — harness pipeline regression", err=True)
        sys.exit(1)


@cli.command("collaborate")
@click.option("--repo", required=True, help="Path to the local git repo the team works in.")
@click.option(
    "--spec", required=True, help="Spec text, @file, or linear:FRONT-123 (needs Linear wiring)."
)
@click.option(
    "--test-command", default="", help="Command the team runs to verify (e.g. 'pytest -q')."
)
@click.option("--api-base-url", default="http://localhost:11434/v1")
@click.option("--model", default="gpt-oss:20b")
@click.option("--provider", default="ollama")
@click.option(
    "--seats",
    default="backend,frontend,sdet,security,devops,performance",
    help="Comma-separated discussion seats (tech-lead always facilitates).",
)
@click.option("--base-ref", default="", help="Branch/sha to base the work on (default: HEAD).")
@click.option("--branch", default="", help="Working branch (default: frontier/<task>).")
@click.option(
    "--isolation",
    default="worktree",
    type=click.Choice(["worktree", "in-place"]),
    help="worktree = isolated checkout per task (recommended); in-place = edit the repo directly.",
)
@click.option(
    "--allow-outside",
    default="ask",
    type=click.Choice(["ask", "deny", "allow"]),
    help="What the team may do outside the bound repo.",
)
@click.option(
    "--grant", "grants", multiple=True, help="Extra path(s) the team is permitted to touch."
)
@click.option("--task-id", default="", help="Task/chat id (names the worktree + branch).")
@click.option("--discussion-rounds", default=2, type=int)
@click.option("--build-rounds", default=2, type=int)
@click.option("--max-steps", default=40, type=int)
@click.option("--trajectory-dir", default="")
def collaborate(
    repo,
    spec,
    test_command,
    api_base_url,
    model,
    provider,
    seats,
    base_ref,
    branch,
    isolation,
    allow_outside,
    grants,
    task_id,
    discussion_rounds,
    build_rounds,
    max_steps,
    trajectory_dir,
):
    """Give a spec to a cross-functional team, bound to a specific repo.

    The team is confined to the bound repo (isolated git worktree by default);
    work outside it requires permission (--allow-outside). They reason, debate to
    a consensus design, build + test it, and hand back a completed feature. The
    full team discussion (each engineer's chain-of-thought) is printed.
    """
    from pathlib import Path as _Path

    from frontier_runtime.harness.collaboration import build_collaborative_team
    from frontier_runtime.harness.llm import OpenAIChatClient
    from frontier_runtime.harness.loop import LoopBudgets
    from frontier_runtime.harness.workspace_binding import WorkspaceBinding, WorkspaceManager

    if spec.startswith("@"):
        spec_text = _Path(spec[1:]).read_text(encoding="utf-8")
    elif spec.startswith("linear:"):
        raise click.ClickException("linear: specs need the Linear connector wired (see roadmap).")
    else:
        spec_text = spec

    run_id = task_id or _Path(repo).name
    binding = WorkspaceBinding(
        repo_path=repo,
        base_ref=base_ref,
        branch=branch,
        isolation=isolation,
        allow_outside=allow_outside,
        extra_paths=list(grants),
        test_command=test_command,
    )
    manager = WorkspaceManager()
    task, prov = manager.build_task(binding, run_id, spec_text)
    click.echo(
        f"  · workspace: {prov.root} (branch {prov.branch}, outside={allow_outside})", err=True
    )

    client = OpenAIChatClient(
        model=model, base_url=api_base_url, api_key="local", provider=provider
    )
    participants = tuple(s.strip() for s in seats.split(",") if s.strip())
    team = build_collaborative_team(
        client_for=lambda role: client,
        budgets=LoopBudgets(max_steps=max_steps),
        participants=participants,
        max_discussion_rounds=discussion_rounds,
        max_build_rounds=build_rounds,
        out_of_bounds=allow_outside,
        on_escalation=lambda req: click.echo(f"  · PERMISSION REQUESTED: {req}", err=True),
        trajectory_dir=_Path(trajectory_dir) if trajectory_dir else None,
        on_event=lambda kind, data: click.echo(f"  · {kind}: {data}", err=True),
    )
    try:
        result = team.run(task, spec_text)
    finally:
        if isolation == "worktree":
            click.echo(f"  · worktree kept at {prov.root} (branch {prov.branch})", err=True)
    click.echo(result.chat())
    sys.exit(0 if result.approved else 2)


@cli.command("develop")
@click.option("--repo", required=True, help="Path to the local git repo to work in.")
@click.option(
    "--spec", default="", help="Spec text, @file, or linear:FRONT-123 (needs Linear wiring)."
)
@click.option(
    "--test-command", default="", help="Command the team runs to verify (e.g. 'pytest -q')."
)
@click.option("--api-base-url", default="http://localhost:11434/v1")
@click.option("--model", default="gpt-oss:20b")
@click.option("--provider", default="ollama")
@click.option("--max-rounds", default=3, type=int)
@click.option("--max-steps", default=40, type=int)
@click.option(
    "--open-pr/--no-open-pr", default=False, help="Open a GitHub PR on approve (needs gh)."
)
@click.option("--target-branch", default="main")
@click.option("--trajectory-dir", default="")
def develop(
    repo,
    spec,
    test_command,
    api_base_url,
    model,
    provider,
    max_rounds,
    max_steps,
    open_pr,
    target_branch,
    trajectory_dir,
):
    """Cross-functional dev workflow: plan -> execute -> test -> secure -> deploy-prep.

    Brings the whole agent team together as a chat to take a spec to
    production-ready, then prints the team conversation + result.
    """
    from pathlib import Path as _Path

    from frontier_runtime.harness.development import build_development_workflow
    from frontier_runtime.harness.executor import LocalDirectExecutor
    from frontier_runtime.harness.integrations import DeliveryPolicy, GhCliGitHub, GitHubDelivery
    from frontier_runtime.harness.llm import OpenAIChatClient
    from frontier_runtime.harness.loop import LoopBudgets
    from frontier_runtime.harness.swe_agent import SweTask

    if spec.startswith("@"):
        spec_text = _Path(spec[1:]).read_text(encoding="utf-8")
    elif spec.startswith("linear:"):
        raise click.ClickException("linear: specs need the Linear connector wired (see roadmap).")
    else:
        spec_text = spec or "(no spec provided)"

    client = OpenAIChatClient(
        model=model, base_url=api_base_url, api_key="local", provider=provider
    )
    executor = LocalDirectExecutor(repo)

    delivery = None
    policy = DeliveryPolicy(auto_open_pr=open_pr, target_branch=target_branch)
    if open_pr:

        def gh_runner(args):
            return executor.run(["gh", *args], timeout=120)

        delivery = GitHubDelivery(github=GhCliGitHub(executor=executor, gh_runner=gh_runner))

    workflow = build_development_workflow(
        client_for=lambda role: client,
        budgets=LoopBudgets(max_steps=max_steps),
        max_rounds=max_rounds,
        delivery=delivery,
        policy=policy,
        trajectory_dir=_Path(trajectory_dir) if trajectory_dir else None,
        on_event=lambda kind, data: click.echo(f"  · {kind}: {data}", err=True),
    )
    task = SweTask(
        instance_id=_Path(repo).name,
        problem_statement=spec_text,
        executor=executor,
        test_command=test_command,
        base_ref=target_branch,
    )
    result = workflow.run(task, spec_text)

    click.echo(result.chat())
    sys.exit(0 if result.approved else 2)


@cli.command("team")
@click.option("--repo", required=True, help="Path to a local git repo to work in.")
@click.option("--spec", required=True, help="Spec text, or @path/to/spec.md to read from a file.")
@click.option(
    "--test-command", default="", help="Command the agents run to verify (e.g. 'pytest -q')."
)
@click.option("--api-base-url", default="http://localhost:11434/v1")
@click.option("--model", default="gpt-oss:20b")
@click.option("--provider", default="ollama")
@click.option("--max-rounds", default=3, type=int)
@click.option("--max-steps", default=40, type=int)
@click.option("--trajectory-dir", default="")
def team(
    repo, spec, test_command, api_base_url, model, provider, max_rounds, max_steps, trajectory_dir
):
    """Run the multi-agent dev team (architect→implement→review→moderate→fix) on a local repo."""
    import json as _json
    from pathlib import Path as _Path

    from frontier_runtime.harness.executor import LocalDirectExecutor
    from frontier_runtime.harness.llm import OpenAIChatClient
    from frontier_runtime.harness.loop import LoopBudgets
    from frontier_runtime.harness.swe_agent import SweTask
    from frontier_runtime.harness.team import build_team_from_shipped

    spec_text = _Path(spec[1:]).read_text(encoding="utf-8") if spec.startswith("@") else spec
    client = OpenAIChatClient(
        model=model, base_url=api_base_url, api_key="local", provider=provider
    )
    team_flow = build_team_from_shipped(
        client_for=lambda role: client,
        budgets=LoopBudgets(max_steps=max_steps),
        max_rounds=max_rounds,
        trajectory_dir=_Path(trajectory_dir) if trajectory_dir else None,
        on_event=lambda kind, data: click.echo(f"[{kind}] {data}", err=True),
    )
    task = SweTask(
        instance_id=_Path(repo).name,
        problem_statement=spec_text,
        executor=LocalDirectExecutor(repo),
        test_command=test_command,
    )
    result = team_flow.run(task, spec=spec_text)
    click.echo(
        _json.dumps(
            {
                "approved": result.approved,
                "rounds": result.round_count,
                "plan": result.plan[:500],
                "final_patch_bytes": len(result.final_patch.encode("utf-8")),
                "verdicts": [r.verdict.decision for r in result.rounds],
            },
            indent=2,
        )
    )
    if result.final_patch:
        click.echo("\n--- final patch ---\n" + result.final_patch)
    sys.exit(0 if result.approved else 2)


@cli.command("list-instances")
@click.option("--dataset", default="princeton-nlp/SWE-bench_Verified")
@click.option("--limit", default=20, type=int)
@click.option("--split", default="test")
def list_instances(dataset: str, limit: int, split: str) -> None:
    """List SWE-bench instance ids (run on the runner; needs the swebench extra)."""
    from frontier_evals.datasets import swebench_instance_ids

    for iid in swebench_instance_ids(limit, split=split, dataset=dataset):
        click.echo(iid)


@cli.command()
@click.option("--mode", default="plumbing", type=click.Choice(["plumbing", "live"]))
@click.option("--dataset", default="synthetic-mini")
@click.option("--model", default="")
@click.option("--api-base-url", default="")
@click.option("--provider", default="vllm")
@click.option("--profile", default="", help="Force a model capability profile id.")
@click.option(
    "--agent",
    "agent_id",
    default="",
    help="Shipped agent id (examples/agents/<id>) to drive the run, e.g. sdet-swe-agent.",
)
@click.option("--docker-host", default="")
@click.option("--seeds", default="0")
@click.option("--instance-ids", default="", help="Comma-separated subset of instance ids.")
@click.option("--max-steps", default=40, type=int)
@click.option("--threshold", default=0.30, type=float)
@click.option("--allow-local", is_flag=True, help="Override the remote-runner guardrail.")
@click.option("--output-dir", default="eval-results/run")
def run(
    mode: str,
    dataset: str,
    model: str,
    api_base_url: str,
    provider: str,
    profile: str,
    agent_id: str,
    docker_host: str,
    seeds: str,
    instance_ids: str,
    max_steps: int,
    threshold: float,
    allow_local: bool,
    output_dir: str,
) -> None:
    """Run an evaluation (plumbing or live)."""
    config = EvalConfig(
        mode=mode,
        dataset=dataset,
        model=model,
        api_base_url=api_base_url,
        provider=provider,
        profile_id=profile,
        agent_id=agent_id,
        docker_host=docker_host,
        seeds=[int(s) for s in seeds.split(",") if s.strip()],
        instance_ids=[i.strip() for i in instance_ids.split(",") if i.strip()],
        max_steps=max_steps,
        threshold=threshold,
        allow_local=allow_local,
        output_dir=output_dir,
    )
    run_obj = run_eval(config, output_dir=Path(output_dir))
    _print_summary(run_obj)
    sys.exit(0 if run_obj.summary.get("meets_threshold") else 2)


def _print_summary(run_obj) -> None:
    s = run_obj.summary
    click.echo(json.dumps(s, indent=2))
    click.echo(
        f"\nResolve rate: {s['resolve_rate_mean'] * 100:.1f}% "
        f"± {s['resolve_rate_sem'] * 100:.1f}%  "
        f"(threshold {s['threshold'] * 100:.0f}% "
        f"{'MET' if s['meets_threshold'] else 'NOT MET'})"
    )
