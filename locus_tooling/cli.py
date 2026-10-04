from __future__ import annotations

import os
import platform
import re
from collections.abc import Mapping
from pathlib import Path

import click

from . import installer
from .provenance import cli as provenance_cli
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
    """Lattix Locus repo tooling."""


@cli.command()
def bootstrap() -> None:
    bootstrap_env = os.environ.copy()
    bootstrap_env["LOCUS_APP_HOME"] = str(ROOT)
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
        projects_root=str(os.getenv("LOCUS_PROJECTS_ROOT") or "").strip(),
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
    """Start Locus natively (no Docker): managed Postgres+pgvector, Neo4j
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


@cli.command("native-fetch-toolchain")
def native_fetch_toolchain_command() -> None:
    """Fetch the Windows agent toolchain (BusyBox sh + embeddable CPython) into
    <app_home>/toolchain, verify the pinned sha256 values (fail closed) and grant
    the Locus AppContainer read+execute on that directory only. Idempotent."""
    from locus_runtime.win_toolchain import toolchain_app_home

    from .native_binaries import provision_toolchain

    report = provision_toolchain(toolchain_app_home())
    print_json(
        {
            "root": report.root,
            "installed": report.installed,
            "present": report.present,
            "failed": report.failed,
            "granted": report.granted,
        }
    )
    if not report.ok:
        raise SystemExit(1)


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
    run_command([python_executable(), "-m", "mypy", "locus_tooling"], cwd=ROOT)


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
    """Fetch the pinned OPA release into .tools/opa/ (sha256-verified; fails closed)."""
    from .native_binaries import current_platform
    from .opa_release import OpaReleaseError, asset_for, fetch

    os_name, arch = current_platform()
    opa_path = ROOT / ".tools" / "opa" / ("opa.exe" if os_name == "windows" else "opa")
    try:
        fetch(asset_for(os_name, arch), opa_path)
    except OpaReleaseError as exc:
        raise click.ClickException(str(exc)) from exc
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


@cli.group()
def secrets() -> None:
    """Store secrets in the OS keychain (Windows DPAPI fallback); never echoed."""


_SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,63}")


@secrets.command("set")
@click.argument("name")
def secrets_set(name: str) -> None:
    """Prompt (hidden input) for NAME's value and store it, e.g. LINEAR_API_KEY, NVIDIA_API_KEY."""
    from .native_secrets import SecretStorageUnavailable, set_secret

    if not _SECRET_NAME.fullmatch(name):
        raise click.BadParameter("use an upper-case name such as LINEAR_API_KEY", param_hint="NAME")
    value = click.prompt(f"{name}", hide_input=True, confirmation_prompt=True).strip()
    if not value:
        raise click.ClickException("an empty value was not stored")
    try:
        mode = set_secret(name, value)
    except SecretStorageUnavailable as exc:
        raise click.ClickException(str(exc)) from exc
    print_json({"name": name, "stored": True, "storage": mode})


@cli.group()
def loop() -> None:
    """Self-improvement loop: Linear intake -> verified run -> PR (LOCUS-338)."""


def _loop_runner():  # noqa: ANN202 - lazy import keeps the CLI start fast
    from locus_runtime.loop_runner import build_runner

    return build_runner(str(ROOT))


@loop.command("run")
@click.option("--once", is_flag=True, required=True, help="Run one tick and exit.")
def loop_run(once: bool) -> None:  # noqa: ARG001 - --once is the only mode
    """Pick, run and deliver at most one eligible issue."""
    result = _loop_runner().run_once()
    print_json(result.to_dict())
    if result.status in {"error", "refused"}:
        raise SystemExit(1)


@loop.command("serve")
@click.option("--poll-interval", type=float, default=None, help="Seconds between ticks.")
def loop_serve(poll_interval: float | None) -> None:
    """Tick until the kill switch is set (Ctrl+C stops; a crashed run resumes next start)."""
    try:
        result = _loop_runner().serve(poll_interval)
    except KeyboardInterrupt:
        click.echo("loop stopped")
        return
    print_json(result.to_dict())


@loop.command("status")
def loop_status_command() -> None:
    """Enabled/disabled, runs today, active run, last outcome, open loop PRs, host warnings."""
    from locus_runtime.loop_runner import loop_status

    status = loop_status()
    print_json(status)
    for warning in status.get("warnings") or []:
        click.echo(f"warning: {warning}", err=True)


@loop.command("report")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
@click.option("--days", type=click.IntRange(1, 3650), default=30, help="Window in days.")
def loop_report_command(as_json: bool, days: int) -> None:
    """Throughput, success rate, cost, gate failures, eval and perf trends (LOCUS-339)."""
    from locus_runtime.loop_runner.report import load_report, render_text

    report = load_report(days=days)
    if as_json:
        print_json(report)
    else:
        click.echo(render_text(report))


@loop.command("disable")
def loop_disable() -> None:
    """Set the file kill switch (the loop stops before its next step)."""
    from locus_runtime.loop_runner.state import KILL_FILE, default_loop_home

    home = default_loop_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / KILL_FILE).write_text("disabled via `lattix loop disable`\n", encoding="utf-8")
    print_json({"enabled": False, "kill_switch": str(home / KILL_FILE)})


@loop.command("enable")
def loop_enable() -> None:
    """Remove the file kill switch (LOCUS_LOOP_DISABLED still wins if set)."""
    from locus_runtime.loop_runner import loop_status
    from locus_runtime.loop_runner.state import KILL_FILE, default_loop_home

    (default_loop_home() / KILL_FILE).unlink(missing_ok=True)
    print_json(loop_status())


@loop.command("autostart")
@click.option("--repo", "repo", default="", help="Checkout the loop runs on (needs WORKFLOW.md).")
@click.option("--off", "off", is_flag=True, help="Stop starting the loop with the desktop app.")
def loop_autostart(repo: str, off: bool) -> None:
    """Start `lattix loop serve` with the desktop app, also after updates (LOCUS-349).

    The kill switch still wins: with `lattix loop disable` set, the app does not start it.
    """
    from locus_runtime.loop_runner.state import default_loop_home
    from locus_tooling.desktop_update import read_loop_autostart, write_loop_autostart

    home = default_loop_home()
    if off:
        flag = write_loop_autostart(home, enabled=False)
    elif repo:
        try:
            flag = write_loop_autostart(home, enabled=True, repo_path=repo)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    else:
        flag = read_loop_autostart(home)
    print_json(flag.model_dump())


@cli.group("version")
def version_group() -> None:
    """Release versions (D-31): MAJOR.MINOR from VERSION, PATCH = build counter."""


@version_group.command("next")
@click.option(
    "--tags-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Release tag names, one per line (or `git ls-remote --tags` output).",
)
def version_next(tags_file: str) -> None:
    """Print the next build version: VERSION + (1 + the highest tagged PATCH, or 0)."""
    from . import versioning

    try:
        click.echo(versioning.next_from_files(ROOT / versioning.VERSION_FILE, Path(tags_file)))
    except versioning.VersionError as exc:
        raise click.ClickException(str(exc)) from exc


@version_group.command("sync")
def version_sync() -> None:
    """Set the pinned manifests (tauri.conf.json, Cargo.toml, package*.json, pyproject.toml)
    to <VERSION>.0 after a VERSION edit."""
    from . import versioning

    try:
        base = versioning.read_version_file(
            (ROOT / versioning.VERSION_FILE).read_text(encoding="utf-8")
        )
        changed = versioning.sync_manifests(ROOT, base)
    except (versioning.VersionError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    for path in changed:
        click.echo(f"updated {path}")


@version_group.command("check")
@click.option("--base-ref", default=None, help="Git ref the change is compared with (PR base).")
@click.option(
    "--pr-body-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="PR body holding the `Release-Impact: patch|minor|major` declaration.",
)
def version_check(base_ref: str | None, pr_body_file: str | None) -> None:
    """Validate VERSION and, against --base-ref, the change's release impact."""
    from . import versioning

    body = Path(pr_body_file).read_text(encoding="utf-8") if pr_body_file else None
    try:
        result = versioning.run_check(ROOT, base_ref=base_ref, pr_body=body)
    except versioning.VersionError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(result.render())
    if not result.ok:
        raise SystemExit(1)


@cli.command()
@click.argument("domain", required=False)
def demo(domain: str | None) -> None:
    print_json({"domain": domain or "default", "agents": discover_agent_records()[:5]})


# D-29 provenance inspection and dependency gate (LOCUS-358).
cli.add_command(provenance_cli.provenance)


if __name__ == "__main__":
    cli()
