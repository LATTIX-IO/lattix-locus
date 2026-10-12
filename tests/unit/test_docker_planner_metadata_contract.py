"""LOCUS-433 increment 21: no mutable Docker planner metadata authority.

Hardened Docker already has a complete verified argv; unlike Windows
AppContainer it has no authorized host-side launcher cwd or network route.
"""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture


@pytest.mark.parametrize("attack", [
    "absolute-host-cwd",
    "relative-host-cwd",
    "empty-host-cwd",
    "unexpected-metadata",
    "unexpected-network",
    "empty-network",
])
def test_docker_planner_metadata_denied_before_receipt_or_process(
    tmp_path, monkeypatch, attack,
):
    calls = docker(monkeypatch)
    original = broker_module.SandboxManager.plan

    def compromised_plan(self, spec, policy):
        plan = original(self, spec, policy)
        if attack == "absolute-host-cwd":
            plan.metadata["launcher_cwd"] = "/etc"
        elif attack == "relative-host-cwd":
            plan.metadata["launcher_cwd"] = "./"
        elif attack == "empty-host-cwd":
            plan.metadata["launcher_cwd"] = ""
        elif attack == "unexpected-metadata":
            plan.metadata["docker_host"] = "tcp://attacker.example:2375"
        elif attack == "unexpected-network":
            plan.network_name = "host"
        elif attack == "empty-network":
            plan.network_name = ""
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", compromised_plan)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker sandbox planner metadata denied"):
        app.execute(lease, cmd(tmp_path, key="metadata-denied"))
    assert calls == []
    with receipts.reservations._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM pod_action_receipt",
        ).fetchone()[0] == 0


def test_valid_docker_planner_defaults_execute_once_with_no_host_cwd(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path, key="metadata-valid"))
    assert result.receipt.state == "succeeded"
    assert result.backend == "hardened-docker"
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert options["cwd"] is None
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path, key="metadata-valid"))
    assert len(calls) == 1


def test_existing_native_windows_launcher_metadata_unchanged(
    tmp_path, monkeypatch,
):
    from locus_runtime.sandbox import (
        ConfinementSelection, HostPlatform, IsolationStrategy,
    )

    calls = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.WINDOWS_APPCONTAINER, HostPlatform.WINDOWS,
            "approved native sandbox",
        ),
    )
    original = broker_module.SandboxManager.plan

    def native_planner(self, spec, policy):
        plan = original(self, spec, policy)
        # Existing native planner owns Windows launcher directory selection.
        # Validating that value still uses the preexisting native path.
        assert plan.metadata.get("launcher_cwd") is not None
        raise RuntimeError("native strategy reached planner")

    monkeypatch.setattr(broker_module.SandboxManager, "plan", native_planner)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(RuntimeError, match="native strategy reached planner"):
        app.execute(lease, cmd(tmp_path, key="native-compat"))
    assert not calls
