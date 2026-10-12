"""LOCUS-433 increment 22: protect sensitive subpaths in writable Docker bind roots.

A writable workspace must not make .git, .ssh, .locus, or credential stores
writable in the container. The broker's independent argv contract must
require exactly the protective readonly binds the sandbox planner emits.
"""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture


def _mounted(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "-v"]


@pytest.mark.parametrize("sensitive", [
    ".git", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".locus",
    ".config/gcloud",
])
def test_writable_workspace_reprotects_existing_sensitive_subtree(
    tmp_path, monkeypatch, sensitive,
):
    calls = docker(monkeypatch)
    root = tmp_path
    path = root / sensitive
    path.mkdir(parents=True)
    app, receipts, lease, _ = fixture(tmp_path)
    out = app.execute(lease, cmd(tmp_path, key="readonly-overlay"))
    assert out.receipt.state == "succeeded"
    assert len(calls) == 1
    argv, opts = calls[0]
    mounts = _mounted(argv)
    rw = f"{root}:{root}:rw"
    ro = f"{path}:{path}:ro"
    assert rw in mounts
    assert ro in mounts
    assert mounts.index(ro) > mounts.index(rw)
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert opts["shell"] is False
    assert opts["stdout"] is subprocess.DEVNULL
    assert opts["stderr"] is subprocess.DEVNULL
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path, key="readonly-overlay"))
    assert len(calls) == 1


def test_planner_cannot_remove_required_sensitive_readonly_overlay(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    sensitive = tmp_path / ".git"
    sensitive.mkdir()
    original = broker_module.SandboxManager.plan

    def stripped(self, spec, policy):
        plan = original(self, spec, policy)
        target = f"{sensitive}:{sensitive}:ro"
        index = plan.command.index(target)
        assert plan.command[index - 1] == "-v"
        del plan.command[index - 1:index + 1]
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", stripped)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker sandbox isolation contract denied"):
        app.execute(lease, cmd(tmp_path, key="forged-overlays"))
    assert calls == []
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("sensitive", [".git", ".aws", ".config/gcloud"])
def test_protected_control_directory_itself_cannot_be_write_root(
    tmp_path, monkeypatch, sensitive,
):
    calls = docker(monkeypatch)
    writable = tmp_path / sensitive
    writable.mkdir(parents=True)
    app, receipts, lease, _ = fixture(writable)
    with pytest.raises(AdmissionDenied, match="Docker protected workspace path denied"):
        app.execute(lease, cmd(writable, key="protected-root"))
    assert calls == []
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("sensitive", [".git", ".ssh", ".config"])
def test_symlinked_sensitive_subpath_denied_before_gateway_or_receipt(
    tmp_path, monkeypatch, sensitive,
):
    calls = docker(monkeypatch)
    external = tmp_path.parent / f"external-{sensitive.lstrip('.')}"
    external.mkdir(exist_ok=True)
    (tmp_path / sensitive).symlink_to(external, target_is_directory=True)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker protected workspace path denied"):
        app.execute(lease, cmd(tmp_path, key="sensitive-symlink"))
    assert not calls
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_workspace_without_sensitive_subpaths_remains_compatible(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path, key="no-extra-overlays"))
    assert result.receipt.state == "succeeded"
    assert len(calls) == 1
    argv = calls[0][0]
    assert _mounted(argv) == [f"{tmp_path}:{tmp_path}:rw"]
    assert "--network=none" in argv
    assert len(receipts.audit(result.receipt.action_id)) == 4



@pytest.mark.parametrize("sensitive", [
    ".GIT", ".SSH", ".AWS", ".CONFIG", ".config/GCLOUD",
])
def test_case_variant_protected_directory_is_never_a_writable_root(
    tmp_path, monkeypatch, sensitive,
):
    """Deny even on Linux CI: such aliases are sensitive on casefolding hosts."""
    calls = docker(monkeypatch)
    root = tmp_path / sensitive
    root.mkdir(parents=True)
    app, receipts, lease, _ = fixture(root)
    with pytest.raises(AdmissionDenied, match="Docker protected workspace path denied"):
        app.execute(lease, cmd(root, key="casefold-sensitive-root"))
    assert not calls
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("sensitive", [
    ".GIT", ".SSH", ".CONFIG", ".config/GCLOUD",
])
def test_nested_case_variant_sensitive_path_denied_before_side_effect(
    tmp_path, monkeypatch, sensitive,
):
    """Read-only mount at .git would not protect an existing .GIT spelling."""
    calls = docker(monkeypatch)
    (tmp_path / sensitive).mkdir(parents=True)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker protected workspace path denied"):
        app.execute(lease, cmd(tmp_path, key="nested-casefold-sensitive"))
    assert not calls
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_explicit_readonly_sensitive_child_does_not_create_duplicate_mount(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    sensitive = tmp_path / ".git"
    sensitive.mkdir()
    original = broker_module.SandboxManager.plan

    def extra_readonly(self, spec, policy):
        policy.allowed_read_paths.append(str(sensitive))
        return original(self, spec, policy)

    # Keep the explicit read grant part of the original admission contract:
    # fixture's broker is not called until the policy source is patched.
    from locus_runtime import pod_sandbox_broker as broker
    app, receipts, lease, _ = fixture(tmp_path)
    original_expected = broker._expected_docker_launch_contract

    def expected_with_read(spec, policy, *, image):
        policy.allowed_read_paths.append(str(sensitive))
        return original_expected(spec, policy, image=image)

    # Simpler deterministic unit contract: compare independent planner/broker
    # argv for the same explicit grants without altering the receipt pipeline.
    from locus_runtime.sandbox import ExecutionSpec, SandboxManager, SandboxPolicy
    policy = SandboxPolicy(
        allowed_read_paths=[str(tmp_path), str(sensitive)],
        allowed_write_paths=[str(tmp_path)],
    )
    spec = ExecutionSpec(command=["true"], cwd=str(tmp_path))
    argv = SandboxManager().build_command(spec, policy)
    mounts = _mounted(argv)
    readonly = f"{sensitive}:{sensitive}:ro"
    assert mounts.count(readonly) == 1
    assert mounts.index(readonly) > mounts.index(f"{tmp_path}:{tmp_path}:rw")
    assert not calls
