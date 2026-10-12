"""LOCUS-433: immutable, validated launch identity across durable receipt handoff."""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture


@pytest.mark.parametrize("attack", [
    "append-privileged",
    "replace-with-host",
    "replace-with-network",
    "clear-plan",
    "forge-backend",
    "replace-launcher-cwd",
])
def test_planner_retained_mutable_plan_cannot_change_fenced_dispatch(
    tmp_path, monkeypatch, attack,
):
    """Admitted bytes remain frozen while write-ahead fencing authorizes dispatch."""
    subprocess_calls = docker(monkeypatch)
    original_plan = broker_module.SandboxManager.plan
    retained = []

    def capture_plan(self, spec, policy):
        plan = original_plan(self, spec, policy)
        retained.append(plan)
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", capture_plan)
    app, receipts, lease, _ = fixture(tmp_path)
    original_execute_once = receipts.execute_once
    seen = []

    def corrupt_before_invoke(lease_arg, *, idempotency_key, authorization, invoke):
        assert len(retained) == 1
        plan = retained[0]
        seen.append(tuple(plan.command))
        if attack == "append-privileged":
            plan.command.insert(-3, "--privileged")
        elif attack == "replace-with-host":
            plan.command[:] = ["echo", "unauthorized-host-exec"]
        elif attack == "replace-with-network":
            plan.command[:] = ["docker", "run", "--rm", "--network=host", "busybox"]
        elif attack == "clear-plan":
            plan.command.clear()
        elif attack == "forge-backend":
            plan.backend = "direct-host"
        elif attack == "replace-launcher-cwd":
            plan.metadata["launcher_cwd"] = "/etc"
        return original_execute_once(
            lease_arg, idempotency_key=idempotency_key,
            authorization=authorization, invoke=invoke,
        )

    monkeypatch.setattr(receipts, "execute_once", corrupt_before_invoke)
    result = app.execute(lease, cmd(tmp_path))
    assert len(seen) == 1
    assert result.receipt.state == "succeeded"
    assert result.backend == "hardened-docker"
    assert result.exit_code == 0
    assert len(subprocess_calls) == 1
    argv, options = subprocess_calls[0]
    # The broker alone injects a one-time Docker container name. All other
    # CLI bytes must equal the previously validated immutable snapshot.
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[3] == "--name" and argv[4].startswith("locus-pod-")
    assert tuple(argv[:3] + argv[5:]) == seen[0]
    assert "--privileged" not in argv
    assert "--network=host" not in argv
    assert "unauthorized-host-exec" not in argv
    assert options["cwd"] != "/etc"
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path))
    assert len(subprocess_calls) == 1


@pytest.mark.parametrize("attack", [
    "extra-host-write",
    "extra-network",
    "host-exec",
])
def test_untrusted_launch_plan_must_pass_contract_before_receipt(
    tmp_path, monkeypatch, attack,
):
    calls = docker(monkeypatch)
    original_plan = broker_module.SandboxManager.plan

    def forged_plan(self, spec, policy):
        plan = original_plan(self, spec, policy)
        if attack == "extra-host-write":
            plan.command.insert(-3, "-v")
            plan.command.insert(-3, "/etc:/etc:rw")
        elif attack == "extra-network":
            plan.command.insert(-3, "--network=host")
        else:
            plan.command[:] = ["echo", "unauthorized-host-exec"]
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", forged_plan)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied):
        app.execute(lease, cmd(tmp_path))
    assert calls == []
    with receipts.reservations._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM pod_action_receipt",
        ).fetchone()[0] == 0
