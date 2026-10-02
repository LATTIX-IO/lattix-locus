"""Track C: Windows AppContainer/Job-Object isolation — pure helpers, strategy
wiring, detection. Cross-platform unit tests; a Job-Object smoke runs on Windows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from frontier_runtime import sandbox as sb  # noqa: E402
from frontier_runtime import win_sandbox as ws  # noqa: E402


# --- pure helpers ------------------------------------------------------------
def test_parse_memory_limit():
    assert ws.parse_memory_limit("512m") == 512 * 1024**2
    assert ws.parse_memory_limit("2g") == 2 * 1024**3
    assert ws.parse_memory_limit("1048576") == 1048576
    assert ws.parse_memory_limit("") == 0
    assert ws.parse_memory_limit("garbage") == 0


def test_compute_job_limits_flags():
    limits = ws.compute_job_limits(memory="256m", pids=8)
    assert limits.memory_bytes == 256 * 1024**2
    assert limits.active_process_limit == 8
    flags = limits.limit_flags()
    assert flags & 0x2000  # KILL_ON_JOB_CLOSE
    assert flags & 0x0008  # ACTIVE_PROCESS
    assert flags & 0x0100  # PROCESS_MEMORY


def test_capability_sids_network_gated():
    assert ws.capability_sids(allow_network=False) == []
    caps = ws.capability_sids(allow_network=True)
    assert "internetClient" in caps


def test_acl_grant_commands():
    cmds = ws.acl_grant_commands("S-1-15-2-1-2-3", write_paths=["C:\\work"], read_paths=["C:\\ro"])
    # write path → modify (M); read path → read+execute (RX); both target the container SID.
    write_cmd = next(c for c in cmds if "C:\\work" in c)
    read_cmd = next(c for c in cmds if "C:\\ro" in c)
    assert write_cmd[0] == "icacls" and "/grant" in write_cmd
    assert any("*S-1-15-2-1-2-3:(OI)(CI)M" == a for a in write_cmd)
    assert any("*S-1-15-2-1-2-3:(OI)(CI)RX" == a for a in read_cmd)


def test_build_wsb_config():
    xml = ws.build_wsb_config(
        command=["cmd", "/c", "echo hi"],
        read_paths=["C:\\ro"],
        write_paths=["C:\\work"],
        allow_network=False,
    )
    assert "<Networking>Disable</Networking>" in xml
    assert "C:\\work" in xml and "<ReadOnly>false</ReadOnly>" in xml
    assert "C:\\ro" in xml and "<ReadOnly>true</ReadOnly>" in xml
    assert "echo hi" in xml and "<LogonCommand>" in xml
    # Network-on flips the toggle.
    assert "<Networking>Default</Networking>" in ws.build_wsb_config(
        command=[], read_paths=[], write_paths=[], allow_network=True
    )


# --- fail-closed tier selection (pure; cross-platform) -----------------------
def test_select_tier_defaults_to_appcontainer_with_fallback():
    tier, allow_fallback = ws.select_confinement_tier(forced=None, require_appcontainer=False)
    assert tier == "appcontainer"
    assert allow_fallback is True


def test_select_tier_require_appcontainer_disables_fallback():
    # The strong tier is demanded → no silent drop to the resource-only Job tier.
    tier, allow_fallback = ws.select_confinement_tier(forced=None, require_appcontainer=True)
    assert tier == "appcontainer"
    assert allow_fallback is False


def test_select_tier_explicit_job_ignores_require():
    # Operator deliberately chose the baseline tier; require flag does not apply.
    tier, allow_fallback = ws.select_confinement_tier(forced="job", require_appcontainer=True)
    assert tier == "job"
    assert allow_fallback is True


def test_select_tier_unknown_value_defaults_to_appcontainer():
    tier, _ = ws.select_confinement_tier(forced="bogus", require_appcontainer=False)
    assert tier == "appcontainer"


def test_run_confined_fails_closed_when_appcontainer_required(monkeypatch):
    # Hostile-code posture: if AppContainer can't be established and it's required,
    # run_confined must raise — never silently degrade to the Job-Object tier.
    monkeypatch.setattr(ws, "_is_windows", lambda: True)
    monkeypatch.setenv("FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER", "1")
    monkeypatch.delenv("FRONTIER_WIN_SANDBOX_TIER", raising=False)

    def _boom(*_a, **_k):
        raise OSError("appcontainer unavailable")

    monkeypatch.setattr(ws, "_run_in_appcontainer", _boom)
    with pytest.raises(RuntimeError, match="required"):
        ws.run_confined(["cmd", "/c", "echo hi"])


def test_run_confined_falls_back_when_not_required(monkeypatch):
    # Default posture (flag unset): AppContainer failure degrades to the Job tier.
    monkeypatch.setattr(ws, "_is_windows", lambda: True)
    monkeypatch.delenv("FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER", raising=False)
    monkeypatch.delenv("FRONTIER_WIN_SANDBOX_TIER", raising=False)

    def _boom(*_a, **_k):
        raise OSError("appcontainer unavailable")

    monkeypatch.setattr(ws, "_run_in_appcontainer", _boom)
    monkeypatch.setattr(ws, "_run_with_job_object", lambda *_a, **_k: 0)
    result = ws.run_confined(["cmd", "/c", "echo hi"])
    assert result.tier == "job-object"
    assert result.exit_code == 0


def test_parse_args_strips_double_dash():
    parsed = ws._parse_args(["run", "--memory", "256m", "--pids", "8", "--", "echo", "hi"])
    assert parsed.memory == "256m" and parsed.pids == 8
    assert parsed.command == ["echo", "hi"]


# --- strategy build_command + plan dispatch ---------------------------------
def test_appcontainer_strategy_build_command():
    mgr = sb.SandboxManager(force_strategy=sb.IsolationStrategy.WINDOWS_APPCONTAINER)
    policy = sb.SandboxPolicy(
        platform=sb.HostPlatform.WINDOWS,
        allow_network=False,
        allowed_write_paths=[str(_REPO_ROOT)],
        allowed_executables=["python"],
        memory_limit="256m",
        pid_limit=16,
    )
    spec = sb.ExecutionSpec(tool_id="coding", command=["python", "-c", "print(1)"], cwd="")
    plan = mgr.plan(spec, policy)
    assert plan.backend == "windows-appcontainer"
    assert "frontier_runtime.win_sandbox" in plan.command
    assert "run" in plan.command and "--memory" in plan.command and "256m" in plan.command
    assert "--allow-network" not in plan.command
    assert "--" in plan.command and plan.command[-3:] == ["python", "-c", "print(1)"]


def test_appcontainer_strategy_passes_network_flag():
    mgr = sb.SandboxManager(force_strategy=sb.IsolationStrategy.WINDOWS_APPCONTAINER)
    policy = sb.SandboxPolicy(
        platform=sb.HostPlatform.WINDOWS,
        allow_network=True,
        allowed_hosts=["pypi.org"],
        allowed_executables=["pip"],
    )
    spec = sb.ExecutionSpec(
        tool_id="coding", command=["pip", "install", "x"], requested_hosts=["pypi.org"]
    )
    plan = mgr.plan(spec, policy)
    assert "--allow-network" in plan.command


# --- detection ---------------------------------------------------------------
def test_detect_windows_native_picks_appcontainer(monkeypatch):
    monkeypatch.setenv("FRONTIER_RUNTIME_PROFILE", "local-native")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(sb, "detect_host_platform", lambda *a, **k: sb.HostPlatform.WINDOWS)
    assert sb.SandboxManager()._detect() == sb.IsolationStrategy.WINDOWS_APPCONTAINER


def test_detect_windows_opt_in_force(monkeypatch):
    monkeypatch.setenv("FRONTIER_RUNTIME_PROFILE", "local-lightweight")
    monkeypatch.setenv("FRONTIER_FORCE_WINDOWS_APPCONTAINER", "1")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(sb, "detect_host_platform", lambda *a, **k: sb.HostPlatform.WINDOWS)
    assert sb.SandboxManager()._detect() == sb.IsolationStrategy.WINDOWS_APPCONTAINER


def test_native_isolation_parity_across_os(monkeypatch):
    """Native installs get OS-deep isolation on all three platforms:
    Linux→bwrap, macOS→seatbelt, Windows→AppContainer."""
    monkeypatch.setenv("FRONTIER_RUNTIME_PROFILE", "local-native")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)

    # Linux → bwrap (present)
    monkeypatch.setattr(sb, "detect_host_platform", lambda *a, **k: sb.HostPlatform.LINUX)
    monkeypatch.setattr(sb.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert sb.SandboxManager()._detect() == sb.IsolationStrategy.KERNEL_BWRAP

    # macOS → seatbelt (sandbox-exec present)
    monkeypatch.setattr(sb, "detect_host_platform", lambda *a, **k: sb.HostPlatform.MACOS)
    import types as _types

    monkeypatch.setattr(sb, "Path", lambda p: _types.SimpleNamespace(is_file=lambda: True))
    assert sb.SandboxManager()._detect() == sb.IsolationStrategy.KERNEL_SEATBELT

    # Windows → AppContainer
    monkeypatch.setattr(sb, "detect_host_platform", lambda *a, **k: sb.HostPlatform.WINDOWS)
    assert sb.SandboxManager()._detect() == sb.IsolationStrategy.WINDOWS_APPCONTAINER


def test_run_confined_rejected_off_windows(monkeypatch):
    monkeypatch.setattr(ws, "_is_windows", lambda: False)
    with pytest.raises(RuntimeError):
        ws.run_confined(["echo", "hi"])


# --- live Job-Object smoke (Windows only) -----------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="Job Object confinement is Windows-only")
def test_job_object_runs_trivial_command(monkeypatch):
    monkeypatch.setenv("FRONTIER_WIN_SANDBOX_TIER", "job")  # force the baseline tier
    result = ws.run_confined(["cmd", "/c", "echo frontier-ok"], memory="256m", pids=16)
    assert result.exit_code == 0
    assert result.tier == "job-object"


@pytest.mark.skipif(sys.platform != "win32", reason="AppContainer is Windows-only")
def test_appcontainer_default_runs_or_falls_back(monkeypatch, tmp_path):
    # Default tier attempts AppContainer; if the environment blocks it, it must
    # fall back to the Job-Object tier — either way the command runs cleanly.
    monkeypatch.delenv("FRONTIER_WIN_SANDBOX_TIER", raising=False)
    result = ws.run_confined(
        ["cmd", "/c", "echo frontier-ok"],
        memory="256m",
        pids=16,
        write_paths=[str(tmp_path)],
        cwd=str(tmp_path),
    )
    assert result.exit_code == 0
    assert result.tier in {"appcontainer-job", "job-object"}
