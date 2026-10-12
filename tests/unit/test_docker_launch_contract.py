"""LOCUS-433 increment 13: Docker CLI contract guards against conflicting flags.

The planner is deliberately tampered between trusted policy construction and
broker dispatch; every deviation must fail before Gateway action admission,
receipt creation, container launch, or any other subprocess invocation.
"""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture


@pytest.mark.parametrize("extra", [
    ("--user=0:0",),
    ("--cap-add=SYS_ADMIN",),
    ("--privileged",),
    ("--network=host",),
    ("--network", "host"),
    ("--pid=host",),
    ("--ipc=host",),
    ("--security-opt=seccomp=unconfined",),
    ("--security-opt=no-new-privileges=false",),
    ("--entrypoint=/bin/sh",),
    ("--mount", "type=bind,src=/etc,dst=/hostetc"),
    ("-v", "/etc:/etc:rw"),
    ("--volumes-from=other-container",),
    ("--device=/dev/kvm",),
    ("--tmpfs", "/etc:rw"),
    ("-e", "AWS_SECRET_ACCESS_KEY=compromised"),
    ("--cgroupns=host",),
])
def test_unexpected_docker_option_cannot_weaken_policy(
    tmp_path, monkeypatch, extra,
):
    invocations = docker(monkeypatch)
    original = broker_module.SandboxManager.plan

    def compromised_plan(self, spec, policy):
        plan = original(self, spec, policy)
        # Insert forged options where Docker will parse them, before the
        # immutable sandbox image argument and approved fixed command.
        image_index = len(plan.command) - len(spec.command) - 1
        plan.command[image_index:image_index] = list(extra)
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", compromised_plan)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker sandbox isolation"):
        app.execute(lease, cmd(tmp_path))
    assert not invocations
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("replace", [
    ("--user=1000:1000", "--user=0:0"),
    ("--ipc=private", "--ipc=host"),
    ("--network=none", "--network=host"),
    ("--cap-drop=ALL", "--cap-drop=NET_ADMIN"),
    ("--read-only", "--privileged"),
    ("--security-opt=no-new-privileges", "--security-opt=no-new-privileges=false"),
])
def test_replaced_required_docker_flag_denied_before_receipt(
    tmp_path, monkeypatch, replace,
):
    invocations = docker(monkeypatch)
    original = broker_module.SandboxManager.plan

    def compromised_plan(self, spec, policy):
        plan = original(self, spec, policy)
        old, new = replace
        plan.command[plan.command.index(old)] = new
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", compromised_plan)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker sandbox isolation"):
        app.execute(lease, cmd(tmp_path))
    assert not invocations
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_duplicate_mount_injection_denied_without_dispatch(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    original = broker_module.SandboxManager.plan

    def compromised_plan(self, spec, policy):
        plan = original(self, spec, policy)
        mount = f"{tmp_path}:{tmp_path}:rw"
        image_index = len(plan.command) - len(spec.command) - 1
        plan.command[image_index:image_index] = ["-v", mount]
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", compromised_plan)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker sandbox isolation"):
        app.execute(lease, cmd(tmp_path))
    assert not invocations
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_unmodified_docker_plan_still_enforces_one_dispatch_and_replay(
    tmp_path, monkeypatch,
):
    from locus_runtime.action_receipts import ActionReplayDenied

    invocations = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path))
    assert result.receipt.state == "succeeded"
    assert result.exit_code == 0
    assert len(invocations) == 1
    args, kwargs = invocations[0]
    assert args[:3] == ["docker", "run", "--rm"]
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert "--network=none" in args
    assert "--cap-drop=ALL" in args
    assert "--read-only" in args
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path))
    assert len(invocations) == 1
    assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
