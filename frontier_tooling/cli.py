from __future__ import annotations

import os
import platform
from collections.abc import Mapping

import click

from . import installer
from .common import (
    DEFAULT_ARCHIVE_URL,
    configured_local_api_headers,
    configured_local_api_url,
    detect_sandbox_backend,
    discover_agent_records,
    ensure_compose_env_file,
    existing_compose_prefix,
    print_json,
    python_executable,
    remove_installer_artifacts,
    remove_installer_env_files,
    repo_root,
    request_json,
    resolve_opa_command,
    run_command,
)

ROOT = repo_root()


def _request_local_api(
    path: str, *, method: str = "GET", payload: Mapping[str, object] | None = None
) -> object:
    return request_json(
        configured_local_api_url(path),
        method=method,
        payload=payload,
        extra_headers=configured_local_api_headers(),
    )


def _full_compose(*extra: str) -> list[str]:
    env_path = ensure_compose_env_file()
    return ["docker", "compose", "--env-file", str(env_path), *extra]


def _local_compose(*extra: str) -> list[str]:
    env_path = ensure_compose_env_file(local_profile=True)
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_path),
        "-f",
        "docker-compose.local.yml",
        *extra,
    ]


@click.group()
def cli() -> None:
    """Lattix xFrontier repo tooling."""


@cli.command()
def bootstrap() -> None:
    bootstrap_env = os.environ.copy()
    bootstrap_env["FRONTIER_APP_HOME"] = str(ROOT)
    managed_runtime = installer._bootstrap_managed_venv(ROOT, bootstrap_env)
    python_bin = managed_runtime["python_bin"]
    run_command([python_bin, "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT)
    run_command(_full_compose("pull"), cwd=ROOT)
    click.echo("Run 'lattix up' to start the secure platform stack.")


@cli.command("up")
def up_command() -> None:
    run_command(_full_compose("up", "-d"), cwd=ROOT)


@cli.command("local-up")
def local_up_command() -> None:
    run_command(_local_compose("up", "-d"), cwd=ROOT)
    click.echo(
        "Lightweight local stack running. Frontend: http://localhost:3000 ; API health: http://localhost:8000/healthz"
    )


@cli.command("down")
def down_command() -> None:
    run_command(_full_compose("down", "-v"), cwd=ROOT)


@cli.command("update")
def update_command() -> None:
    installer.update()


@cli.command("remove")
def remove_command() -> None:
    torn_down: list[str] = []
    failed_teardowns: list[str] = []
    for local, label in ((False, "secure"), (True, "lightweight")):
        prefix = existing_compose_prefix(local=local)
        if prefix is None:
            continue
        completed = run_command(prefix + ["down", "-v", "--remove-orphans"], cwd=ROOT, check=False)
        if completed.returncode == 0:
            torn_down.append(label)
        else:
            failed_teardowns.append(label)
    removed_env_files = remove_installer_env_files()
    removed_artifacts = remove_installer_artifacts()
    removed = not failed_teardowns
    notes = [
        "Source checkout and .env were left in place.",
        "Editable installs, virtual environments, and PATH entries are left in place.",
        "Run 'lattix bootstrap' or the public bootstrap script again to reinstall.",
    ]
    if failed_teardowns:
        notes.insert(0, "Some Docker compose environments could not be torn down cleanly.")
    print_json(
        {
            "removed": removed,
            "torn_down": torn_down,
            "failed_teardowns": failed_teardowns,
            "deleted_env_files": [str(path) for path in removed_env_files],
            "deleted_artifacts": [str(path) for path in removed_artifacts],
            "notes": notes,
        }
    )


@cli.command("local-down")
def local_down_command() -> None:
    run_command(_local_compose("down", "-v"), cwd=ROOT)


# --------------------------------------------------------------------------- #
# Native (Dockerless) install — managed sidecars, no docker compose.
# --------------------------------------------------------------------------- #
def _native_plan(*, world_models: bool, redis: bool):
    from .native_launcher import NativeConfig, build_native_plan

    config = NativeConfig(
        enable_world_models=world_models,
        enable_redis=redis,
        projects_root=str(os.getenv("FRONTIER_PROJECTS_ROOT") or "").strip(),
    )
    return build_native_plan(config)


@cli.command("native-up")
@click.option(
    "--world-models/--no-world-models", default=True, help="Run the Neo4j world-graph sidecar."
)
@click.option(
    "--redis/--no-redis", default=True, help="Run the Redis short-term cache (WAL fallback if off)."
)
def native_up_command(world_models: bool, redis: bool) -> None:
    """Start xFrontier natively (no Docker): managed Postgres+pgvector, Neo4j
    world models, NATS, Ollama, and the app — then print the resolved status."""
    from .native_launcher import NativeLauncherError, NativeSupervisor

    try:
        plan = _native_plan(world_models=world_models, redis=redis)
    except NativeLauncherError as exc:
        raise SystemExit(f"native-up blocked: {exc}")
    for warning in plan.warnings:
        click.echo(f"warning: {warning}")
    supervisor = NativeSupervisor(plan, log=lambda m: click.echo(m))
    status = supervisor.start_all()
    print_json({"profile": "local-native", "services": status, "warnings": plan.warnings})


@cli.command("native-status")
@click.option("--world-models/--no-world-models", default=True)
@click.option("--redis/--no-redis", default=True)
def native_status_command(world_models: bool, redis: bool) -> None:
    """Show the planned native service set + derived backend env (no launch)."""
    from .native_launcher import NativeLauncherError

    try:
        plan = _native_plan(world_models=world_models, redis=redis)
    except NativeLauncherError as exc:
        raise SystemExit(str(exc))
    safe_env = {
        k: ("***" if any(frag in k for frag in ("SECRET", "PASSWORD", "TOKEN", "DSN")) else v)
        for k, v in plan.env.items()
    }
    print_json({"services": plan.service_names(), "env": safe_env, "warnings": plan.warnings})


@cli.command("native-fetch")
@click.argument("names", nargs=-1)
def native_fetch_command(names: tuple[str, ...]) -> None:
    """Download the native sidecar binaries for THIS OS/arch into the app-home
    bin dir. Auto-fetches single static binaries (nats-server, caddy, ollama on
    Linux); prints official-install guidance for Postgres+pgvector and Neo4j."""
    from .common import default_app_home
    from .native_binaries import DEFAULT_TARGETS, current_platform, provision

    bin_dir = default_app_home() / "bin"
    targets = list(names) if names else list(DEFAULT_TARGETS)
    os_name, arch = current_platform()
    report = provision(targets, bin_dir)
    print_json(
        {
            "platform": {"os": os_name, "arch": arch},
            "bin_dir": str(bin_dir),
            "installed": report.installed,
            "skipped": report.skipped,
            "manual": report.manual,
            "failed": report.failed,
            "warnings": report.warnings,
        }
    )


@cli.command("native-serve")
def native_serve_command() -> None:
    """Run the native supervisor in the FOREGROUND until interrupted (Ctrl+C).

    This is the dev equivalent of the desktop backend sidecar: it starts every
    sidecar + the backend + the frontend and blocks, tearing them down on exit.
    """
    from .desktop import run_desktop_supervisor

    run_desktop_supervisor(log=lambda m: click.echo(m))


@cli.command("native-down")
def native_down_command() -> None:
    """Stop natively-launched sidecars (best-effort; supervisor is per-process)."""
    click.echo(
        "Native services run under the foreground supervisor started by 'native-up'/'native-serve'. "
        "Stop that process (Ctrl+C) to terminate the managed sidecars."
    )


@cli.command("stack-up")
def stack_up_command() -> None:
    run_command(_full_compose("up", "-d"), cwd=ROOT)


@cli.command("stack-down")
def stack_down_command() -> None:
    run_command(_full_compose("down", "-v"), cwd=ROOT)


@cli.command()
def ps() -> None:
    run_command(_full_compose("ps"), cwd=ROOT)


@cli.command()
def logs() -> None:
    run_command(_full_compose("logs", "--tail=200"), cwd=ROOT)


@cli.command()
def health() -> None:
    print_json(_request_local_api("/healthz"))


@cli.command()
def smoke() -> None:
    print_json(_request_local_api("/healthz"))


@cli.command()
def test() -> None:
    run_command([python_executable(), "-m", "pytest", "tests", "-v"], cwd=ROOT / "apps" / "backend")


@cli.command()
def lint() -> None:
    run_command([python_executable(), "-m", "ruff", "check", ".", "--fix"], cwd=ROOT)
    run_command([python_executable(), "-m", "ruff", "format", "."], cwd=ROOT)


@cli.command()
def typecheck() -> None:
    run_command([python_executable(), "-m", "mypy", "frontier_tooling"], cwd=ROOT)


@cli.group()
def policy() -> None:
    """OPA policy helpers."""


@policy.command("test")
def policy_test() -> None:
    run_command([python_executable(), "scripts/run_opa.py", "test", "policies/", "-v"], cwd=ROOT)


@policy.command("lint")
def policy_lint() -> None:
    run_command([resolve_opa_command(), "check", "policies/"], cwd=ROOT)


@cli.command("install-opa")
def install_opa() -> None:
    if os.name != "nt":
        click.echo(
            "Automatic OPA installation is currently implemented in the PowerShell helper on Windows. Install 'opa' on PATH or place it under .tools/opa/."
        )
        return
    opa_dir = ROOT / ".tools" / "opa"
    opa_dir.mkdir(parents=True, exist_ok=True)
    opa_path = opa_dir / "opa.exe"
    run_command(
        [
            python_executable(),
            "-c",
            (
                "from urllib.request import urlopen; from pathlib import Path; "
                f"data=urlopen('https://openpolicyagent.org/downloads/v0.68.0/opa_windows_amd64.exe').read(); "
                f"Path(r'{opa_path}').write_bytes(data)"
            ),
        ],
        cwd=ROOT,
    )
    click.echo(str(opa_path))


@cli.group()
def agent() -> None:
    """Agent asset helpers."""


@agent.command("list")
def agent_list() -> None:
    print_json(discover_agent_records())


@agent.command("scaffold")
@click.option("--name", required=True)
def agent_scaffold(name: str) -> None:
    run_command(
        [python_executable(), "apps/workers/scripts/scaffold_agent_service.py", name], cwd=ROOT
    )


@cli.group()
def workflow() -> None:
    """Workflow helpers."""


@workflow.command("list")
def workflow_list() -> None:
    print_json(_request_local_api("/workflow-definitions"))


@workflow.command("run")
@click.argument("workflow_name")
@click.option("--task", required=True)
def workflow_run(workflow_name: str, task: str) -> None:
    payload = {"workflow_definition_id": workflow_name, "task": task, "input": {"task": task}}
    print_json(_request_local_api("/workflow-runs", method="POST", payload=payload))


@cli.group()
def sandbox() -> None:
    """Sandbox helpers."""


@sandbox.command("backend")
def sandbox_backend() -> None:
    print_json({"backend": detect_sandbox_backend(), "platform": platform.system()})


@cli.group()
def install() -> None:
    """Installer helpers."""


@install.command("run")
def install_run() -> None:
    installer.main()


@install.command("bootstrap-url")
def install_bootstrap_url() -> None:
    click.echo(installer.bootstrap_url() or DEFAULT_ARCHIVE_URL)


@cli.command()
@click.argument("domain", required=False)
def demo(domain: str | None) -> None:
    print_json({"domain": domain or "default", "agents": discover_agent_records()[:5]})


if __name__ == "__main__":
    cli()
