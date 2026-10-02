"""Track 0: local-native profile + per-agent sandbox executor wiring.
Unit-level (no bwrap execution needed), runs anywhere."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from frontier_runtime.harness import workspace_binding as wb  # noqa: E402
from frontier_runtime.harness.executor import LocalDirectExecutor, LocalSandboxExecutor  # noqa: E402
from frontier_runtime import sandbox as sb  # noqa: E402


# --- executor selection ------------------------------------------------------
def test_default_uses_direct_executor(tmp_path, monkeypatch):
    monkeypatch.delenv("FRONTIER_SANDBOX_AGENTS", raising=False)
    monkeypatch.delenv("FRONTIER_RUNTIME_PROFILE", raising=False)
    ex = wb._make_executor(tmp_path, [])
    assert isinstance(ex, LocalDirectExecutor)  # current deploys unchanged


def test_flag_selects_sandbox_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTIER_SANDBOX_AGENTS", "1")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    assert isinstance(wb._make_executor(tmp_path, []), LocalSandboxExecutor)


def test_native_profile_selects_sandbox_executor(tmp_path, monkeypatch):
    monkeypatch.delenv("FRONTIER_SANDBOX_AGENTS", raising=False)
    monkeypatch.setenv("FRONTIER_RUNTIME_PROFILE", "local-native")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    assert wb._sandbox_executor_requested() is True
    assert isinstance(wb._make_executor(tmp_path, []), LocalSandboxExecutor)


def test_sandbox_executor_carries_extra_paths(tmp_path):
    ex = LocalSandboxExecutor(tmp_path, extra_paths=[str(tmp_path / "other")])
    assert any("other" in p for p in ex._extra_paths)


# --- detect: local-native is Dockerless -------------------------------------
def test_local_native_never_falls_back_to_docker(monkeypatch):
    monkeypatch.setenv("FRONTIER_RUNTIME_PROFILE", "local-native")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(sb, "detect_host_platform", lambda *a, **k: sb.HostPlatform.LINUX)
    # docker present, bwrap absent → must NOT pick hardened-docker under native
    monkeypatch.setattr(
        sb.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    mgr = sb.SandboxManager()
    assert mgr._detect() == sb.IsolationStrategy.RESTRICTED_PROCESS

    # bwrap present → kernel sandbox
    monkeypatch.setattr(sb.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert sb.SandboxManager()._detect() == sb.IsolationStrategy.KERNEL_BWRAP


# --- bwrap actually wraps the command (no execution) -------------------------
def test_bwrap_strategy_confines_command(monkeypatch):
    mgr = sb.SandboxManager()
    mgr._forced = sb.IsolationStrategy.KERNEL_BWRAP
    policy = sb.SandboxPolicy(
        platform=sb.HostPlatform.LINUX,
        allow_network=False,
        allowed_read_paths=["/work"],
        allowed_write_paths=["/work"],
        allowed_executables=["bash"],
        timeout_seconds=60,
    )
    spec = sb.ExecutionSpec(tool_id="coding", command=["bash", "-lc", "echo hi"], cwd="/work")
    plan = mgr.plan(spec, policy)
    assert plan.command[0] == "bwrap"
    assert "--unshare-net" in plan.command  # network off by default
    assert "--" in plan.command and plan.command[-3:] == ["bash", "-lc", "echo hi"]


# --- profile registered in the backend --------------------------------------
def test_local_native_profile_registered():
    main = pytest.importorskip("app.main")
    assert main._normalize_runtime_profile_name("native") == "local-native"
    assert "local-native" in main._RUNTIME_PROFILES
