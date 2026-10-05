"""Sandboxed execution is the default; agent commands get a minimal environment.

Principal decision 2026-10-03 ("Accept real OS jails"), LOCUS-332:

* the harness picks the platform's confining tier (Windows → AppContainer with
  require_appcontainer, macOS → seatbelt, Linux → bubblewrap, else hardened
  Docker) and fails closed with an actionable reason when there is none;
* jail facts are derived from the strategy actually launched;
* executors never pass the full ``os.environ`` (LOCUS_*, keys, tokens) to agent
  commands -- only an allowlisted minimum plus explicit per-run variables.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import sandbox as sb
from locus_runtime import win_sandbox as ws
from locus_runtime.harness import executor as executor_module
from locus_runtime.harness import workspace_binding as wb
from locus_runtime.harness.executor import (
    GATEWAY_BLOCKED_EXIT_CODE,
    LocalDirectExecutor,
    LocalSandboxExecutor,
    default_executor,
)
from locus_runtime.win_toolchain import WindowsToolchain

P = sb.HostPlatform
S = sb.IsolationStrategy

SECRETS = {
    "LOCUS_API_BEARER_TOKEN": "locus-secret",
    "LOCUS_ANYTHING": "locus-setting",
    "OPENAI_API_KEY": "sk-test",
    "GITHUB_TOKEN": "ghp-test",
    "AWS_SECRET_ACCESS_KEY": "aws-test",
    "DB_PASSWORD": "pw-test",
}


def _which(*present: str):  # noqa: ANN202
    return lambda name: f"/usr/bin/{name}" if name in present else None


# --- default tier selection per OS ----------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "kwargs", "expected"),
    [
        (
            P.WINDOWS,
            {"appcontainer_available": True, "which": _which("docker")},
            S.WINDOWS_APPCONTAINER,
        ),
        (
            P.WINDOWS,
            {"appcontainer_available": False, "which": _which("docker")},
            S.HARDENED_DOCKER,
        ),
        (P.MACOS, {"seatbelt_available": True, "which": _which("docker")}, S.KERNEL_SEATBELT),
        (P.MACOS, {"seatbelt_available": False, "which": _which("docker")}, S.HARDENED_DOCKER),
        (P.LINUX, {"which": _which("bwrap", "docker")}, S.KERNEL_BWRAP),
        (P.LINUX, {"which": _which("docker")}, S.HARDENED_DOCKER),
    ],
)
def test_select_confining_strategy_per_platform(
    platform: sb.HostPlatform, kwargs: dict[str, Any], expected: sb.IsolationStrategy
) -> None:
    selection = sb.select_confining_strategy(platform=platform, profile="", **kwargs)
    assert selection.strategy == expected
    assert selection.strategy in sb.CONFINING_STRATEGIES


@pytest.mark.parametrize(
    ("platform", "kwargs", "hint"),
    [
        (P.LINUX, {"which": _which()}, "install bubblewrap"),
        (P.MACOS, {"seatbelt_available": False, "which": _which()}, "sandbox-exec"),
        (P.WINDOWS, {"appcontainer_available": False, "which": _which()}, "AppContainer"),
    ],
)
def test_no_confining_tier_returns_actionable_reason(
    platform: sb.HostPlatform, kwargs: dict[str, Any], hint: str
) -> None:
    selection = sb.select_confining_strategy(platform=platform, profile="", **kwargs)
    assert selection.strategy is None
    assert selection.reason.startswith("no confining sandbox available on this host")
    assert hint in selection.reason


def test_native_profile_never_selects_docker() -> None:
    selection = sb.select_confining_strategy(
        platform=P.LINUX, which=_which("docker"), profile="local-native"
    )
    assert selection.strategy is None
    assert "Docker" not in selection.reason


def _force_selection(monkeypatch: pytest.MonkeyPatch, strategy: sb.IsolationStrategy | None):
    selection = sb.ConfinementSelection(strategy, P.LINUX, "no confining sandbox: install x")
    monkeypatch.setattr(executor_module, "select_confining_strategy", lambda: selection)


@pytest.mark.parametrize(
    "strategy", [S.WINDOWS_APPCONTAINER, S.KERNEL_SEATBELT, S.KERNEL_BWRAP, S.HARDENED_DOCKER]
)
def test_default_executor_is_the_confining_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: sb.IsolationStrategy
) -> None:
    monkeypatch.delenv("LOCUS_SANDBOX_AGENTS", raising=False)
    _force_selection(monkeypatch, strategy)
    ex = default_executor(tmp_path)
    assert isinstance(ex, LocalSandboxExecutor)
    assert ex.strategy == strategy
    assert ex.jail_facts().strategy == strategy.value


def test_windows_default_facts_require_appcontainer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOCUS_SANDBOX_AGENTS", raising=False)
    _force_selection(monkeypatch, S.WINDOWS_APPCONTAINER)
    facts = default_executor(tmp_path).jail_facts()
    assert (facts.appcontainer, facts.job_object, facts.require_appcontainer) == (True, True, True)
    assert facts.allow_network is False


def test_no_tier_executor_fails_closed_even_if_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The autouse allow-all authorizer allows the exec; the sink still refuses."""
    monkeypatch.delenv("LOCUS_SANDBOX_AGENTS", raising=False)
    _force_selection(monkeypatch, None)
    spawned: list[Any] = []
    monkeypatch.setattr(executor_module.subprocess, "run", lambda *a, **k: spawned.append(a))
    ex = default_executor(tmp_path)
    assert isinstance(ex, LocalSandboxExecutor) and ex.strategy is None
    assert ex.jail_facts().strategy == "unavailable"
    result = ex.run(["bash", "-c", "true"])
    assert result.exit_code == GATEWAY_BLOCKED_EXIT_CODE
    assert "install x" in result.stderr
    assert spawned == []


def test_explicit_opt_out_selects_direct_executor(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("LOCUS_SANDBOX_AGENTS", "0")
    ex = default_executor(tmp_path)
    assert isinstance(ex, LocalDirectExecutor)
    assert ex.jail_facts().strategy == "local-direct"  # tool_jail denies this tier


def test_workspace_binding_uses_default_selection(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("LOCUS_SANDBOX_AGENTS", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    _force_selection(monkeypatch, S.KERNEL_BWRAP)
    assert wb._sandbox_executor_requested() is True
    assert isinstance(wb._make_executor(tmp_path, []), LocalSandboxExecutor)


# --- environment scrubbing ------------------------------------------------------------------


def test_minimal_agent_env_drops_secrets_and_keeps_basics() -> None:
    base = {"PATH": "/bin", "HOME": "/home/a", "SystemRoot": "C:\\Windows", **SECRETS}
    env = sb.minimal_agent_env({"FOO": "bar", "MY_TOKEN": "x", "LOCUS_RUN": "y"}, base=base)
    assert env == {"PATH": "/bin", "HOME": "/home/a", "SystemRoot": "C:\\Windows", "FOO": "bar"}


def test_local_direct_executor_spawns_with_minimal_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real spawn: the child sees PATH but no LOCUS_* / key / token variables."""
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    ex = LocalDirectExecutor(tmp_path, env={"RUN_LABEL": "eval-1", "RUN_API_KEY": "nope"})
    script = "import json, os; print(json.dumps(dict(os.environ)))"
    result = ex.run([sys.executable, "-c", script])
    assert result.exit_code == 0, result.stderr
    child = {key.upper(): value for key, value in json.loads(result.stdout).items()}
    assert "PATH" in child
    assert child["RUN_LABEL"] == "eval-1"
    for key in (*SECRETS, "RUN_API_KEY"):
        assert key not in child
    assert not any(key.startswith("LOCUS_") for key in child)


def _capture_spawn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append({"cmd": list(cmd), **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(executor_module.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("strategy", [S.KERNEL_BWRAP, S.WINDOWS_APPCONTAINER, S.HARDENED_DOCKER])
def test_sandbox_executor_spawns_with_minimal_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: sb.IsolationStrategy
) -> None:
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    calls = _capture_spawn(monkeypatch)
    ex = LocalSandboxExecutor(
        tmp_path,
        manager=sb.SandboxManager(force_strategy=strategy),
        env={"RUN_LABEL": "x", "SERVICE_TOKEN": "nope"},
        # Windows: bash resolves to the Locus toolchain's BusyBox (LOCUS-333).
        toolchain=WindowsToolchain(root=tmp_path / "toolchain"),
    )
    ex.run(["bash", "-c", "true"])
    assert len(calls) == 1
    env = calls[0]["env"]
    assert not any(key.upper() in SECRETS or key.upper().startswith("LOCUS_") for key in env)
    flat = " ".join(calls[0]["cmd"])
    assert "nope" not in flat and "SERVICE_TOKEN" not in env
    if strategy == S.HARDENED_DOCKER:
        assert "RUN_LABEL=x" in flat  # explicit vars reach the container via -e only
        assert "RUN_LABEL" not in env
    else:
        assert env["RUN_LABEL"] == "x"
    if strategy == S.WINDOWS_APPCONTAINER:
        assert "--require-appcontainer" in calls[0]["cmd"]
        assert calls[0]["cwd"] == sb.WIN_LAUNCHER_CWD


def test_docker_exec_cli_env_has_no_host_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    calls = _capture_spawn(monkeypatch)
    ex = executor_module.DockerContainerExecutor(
        "c1", docker_host="tcp://runner:2376", inspect_network_mode=lambda _cid: "none"
    )
    ex.run_shell("true")
    env = calls[0]["env"]
    assert env["DOCKER_HOST"] == "tcp://runner:2376"
    assert not any(key.upper() in SECRETS for key in env)


def test_docker_exec_network_fact_comes_from_the_container() -> None:
    seen: list[str] = []

    def inspect(cid: str) -> str:
        seen.append(cid)
        return "none"

    ex = executor_module.DockerContainerExecutor("c9", inspect_network_mode=inspect)
    assert ex.jail_facts().allow_network is False
    assert ex.jail_facts().strategy == "docker-exec"
    assert seen == ["c9"]  # inspected once, cached
    broken = executor_module.DockerContainerExecutor(
        "c9", inspect_network_mode=lambda _cid: (_ for _ in ()).throw(OSError("no docker"))
    )
    assert broken.jail_facts().allow_network is True  # unknown counts as networked


# --- Windows launcher: --require-appcontainer is unconditional --------------------------------


def test_launcher_require_flag_overrides_job_tier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCUS_WIN_SANDBOX_TIER", "job")
    monkeypatch.delenv("LOCUS_WIN_SANDBOX_REQUIRE_APPCONTAINER", raising=False)

    def _boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise OSError("appcontainer unavailable")

    job_runs: list[Any] = []
    monkeypatch.setattr(ws, "_run_in_appcontainer", _boom)
    monkeypatch.setattr(ws, "_run_with_job_object", lambda *a, **k: job_runs.append(a) or 0)
    with pytest.raises(RuntimeError, match="required"):
        ws.run_confined(["cmd", "/c", "echo hi"], require_appcontainer=True)
    assert job_runs == []


def test_launcher_parses_require_flag() -> None:
    parsed = ws._parse_args(["run", "--require-appcontainer", "--", "cmd", "/c", "echo"])
    assert parsed.require_appcontainer is True
    assert parsed.command == ["cmd", "/c", "echo"]
