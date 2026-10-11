"""LOCUS-433 command broker TDD, negative policy and sandbox regression."""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied, PodActionReceipts
from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ReservationRequest, ResourceBudget,
)
from locus_runtime.pod_sandbox_broker import BrokerCommand, PodSandboxBroker
from locus_runtime.sandbox import (
    ConfinementSelection, HostPlatform, IsolationStrategy,
)
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from tests.gateway_support import FakeEngine


def fixture(tmp_path, *, engine=None):
    ledger = PodReservationLedger(tmp_path / "pods.db")
    gateway = Gateway(engine or FakeEngine(), lambda _: None)
    parent = gateway.open_session(
        run_id="run-safe", principal="alice", engine="supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"run_command"}),
            read_roots=(str(tmp_path),),
            write_roots=(str(tmp_path),),
            allowed_executables=("echo",),
            max_risk_class=RiskClass.R2,
            max_actions=12, max_tool_calls=12,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=1200, duration_used_seconds=0,
                max_duration_seconds=120, cost_used_usd=0, max_cost_usd=2,
            ),
        ),
    )
    plan = compile_task_plan(
        {"contract_version": "1.0", "nodes": [{
            "node_id": "analysis", "depends_on": [],
            "capabilities": ["run_command"], "risk_ceiling": "R2",
            "budget": {
                "actions": 5, "tokens": 500, "wall_seconds": 60,
                "spend_microunits": 1_000_000,
            },
            "deliverables": ["analysis"], "acceptance_criteria": ["bounded command"],
        }]},
        intent=TaskIntent("execute a declared test CLI tool", "professional", "alice",
                          ("command is constrained",)),
        parent_authority=TaskAuthority(
            frozenset({"run_command"}), "R2",
            TaskBudget(12, 1200, 120, 2_000_000),
        ),
    )
    ledger.register_parent(plan, parent, max_tool_calls=12)
    reservation = ledger.reserve(
        plan, parent, ReservationRequest(
            "analysis", "pod-attempt", "v1",
            ResourceBudget(5, 4, 400, 45, 700_000), 70,
        ),
    )
    lease = ledger.admit(plan, parent, reservation.reservation_id, engine="worker")
    receipts = PodActionReceipts(ledger, supervisor=parent)
    return PodSandboxBroker(receipts), receipts, lease, parent


def cmd(tmp_path, *, tool="run_command", args=("echo", "success"),
        cwd=None, key="safe-one", timeout=15):
    return BrokerCommand(tool, args, str(cwd or tmp_path), key, timeout)


def docker(monkeypatch, *, status=0):
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.HARDENED_DOCKER, HostPlatform.LINUX, "verified docker",
        ),
    )
    monkeypatch.setattr(broker_module, "detect_host_platform", lambda: HostPlatform.LINUX)
    invocations = []
    def fake_run(argv, **kwargs):
        invocations.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, status)
    monkeypatch.setattr(broker_module.subprocess, "run", fake_run)
    return invocations


def test_allowed_sandboxed_command_is_gateway_receipted_and_network_disabled(tmp_path, monkeypatch):
    executions = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-inherit-me")
    result = app.execute(lease, cmd(tmp_path))
    assert result.exit_code == 0
    assert result.receipt.state == "succeeded"
    assert len(executions) == 1
    argv, opts = executions[0]
    assert argv[0:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert opts["shell"] is False
    assert opts["timeout"] == 15
    assert opts["stdout"] is subprocess.DEVNULL
    assert opts["stderr"] is subprocess.DEVNULL
    assert "AWS_SECRET_ACCESS_KEY" not in opts["env"]
    assert all("do-not-inherit-me" not in str(value) for value in opts["env"].values())
    assert [x[0] for x in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]


def test_result_is_status_only_with_no_command_output_in_audit(tmp_path, monkeypatch):
    executions = docker(monkeypatch, status=7)
    app, receipts, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path))
    assert result.exit_code == 7
    assert result.receipt.state == "succeeded"  # execution completed, tool failed
    assert "success" not in str(receipts.audit(result.receipt.action_id))
    assert executions[0][1]["stderr"] is subprocess.DEVNULL


def test_exactly_one_sandbox_spawn_after_duplicate_idempotency_key(tmp_path, monkeypatch):
    executions = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    app.execute(lease, cmd(tmp_path))
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path))
    assert len(executions) == 1
    assert receipts.reservations.audit_events("run-safe")


def test_unknown_tool_and_executable_fail_before_gateway_receipt(tmp_path, monkeypatch):
    executions = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="tool"):
        app.execute(lease, cmd(tmp_path, tool="browser_click"))
    with pytest.raises(AdmissionDenied, match="executable"):
        app.execute(lease, cmd(tmp_path, args=("curl", "https://example.net")))
    assert not executions
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("args", [
    ("bash", "-c", "cat ~/.ssh/id_rsa"),
    ("sh", "-c", "echo hi"),
    ("cmd.exe", "/c", "whoami"),
    ("powershell.exe", "-Command", "whoami"),
    ("C:\\Windows\\System32\\cmd.exe", "/C", "whoami"),
    ("python3", "-c", "import os"),
])
def test_shell_interpreter_entrypoints_are_rejected(args, tmp_path):
    with pytest.raises(AdmissionDenied, match="shell"):
        cmd(tmp_path, args=args)


@pytest.mark.parametrize("args", [
    (), ("echo", "\x00hidden"), ("echo", "\nattack"), ("echo", "x" * 2049),
    tuple("x" for _ in range(33)),
])
def test_bad_argument_envelope_rejected_before_any_execution(args, tmp_path):
    with pytest.raises(ValueError):
        cmd(tmp_path, args=args)


def test_no_confinement_never_falls_back_to_raw_subprocess(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(None, HostPlatform.LINUX, "unavailable"),
    )
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="no proven confining"):
        app.execute(lease, cmd(tmp_path))
    assert not invocations
    assert receipts.reservations.audit_events("run-safe")


def test_bwrap_all_host_read_binding_is_refused_without_hardened_docker(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.KERNEL_BWRAP, HostPlatform.LINUX, "bubblewrap",
        ),
    )
    monkeypatch.setattr(broker_module.shutil, "which", lambda _: None)
    app, _, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="bwrap default"):
        app.execute(lease, cmd(tmp_path))
    assert not invocations


def test_symlinked_workdir_outside_allowed_scope_is_denied(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    app, _, lease, _ = fixture(tmp_path)
    forbidden = tmp_path.parent / "other-tenant"
    forbidden.mkdir(exist_ok=True)
    symlink = tmp_path / "escape"
    symlink.symlink_to(forbidden, target_is_directory=True)
    with pytest.raises(AdmissionDenied, match="read scope"):
        app.execute(lease, cmd(tmp_path, cwd=symlink))
    assert not invocations


def test_gateway_denial_never_launches_sandbox_process(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    app, receipts, lease, _ = fixture(
        tmp_path, engine=FakeEngine(allow={"agent_policy": False}),
    )
    with pytest.raises(AdmissionDenied):
        app.execute(lease, cmd(tmp_path))
    assert not invocations
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_timeout_is_uncertain_and_never_repeated(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    def timeout(argv, **kwargs):
        invocations.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(broker_module.subprocess, "run", timeout)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        app.execute(lease, cmd(tmp_path))
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path))
    assert len(invocations) == 1
    with receipts.reservations._connect() as db:
        status = db.execute("SELECT state FROM pod_action_receipt").fetchone()[0]
    assert status == "uncertain"


def test_revoked_parent_prevents_process_dispatch(tmp_path, monkeypatch):
    invocations = docker(monkeypatch)
    app, receipts, lease, parent = fixture(tmp_path)
    receipts.reservations.revoke_parent(parent)
    with pytest.raises(AdmissionDenied):
        app.execute(lease, cmd(tmp_path))
    assert not invocations



def test_remote_docker_host_never_inherited_by_broker(tmp_path, monkeypatch):
    launched = docker(monkeypatch)
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote-host.internal:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "cloud-admin")
    app, _, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path))
    assert result.exit_code == 0
    assert launched
    assert "DOCKER_HOST" not in launched[0][1]["env"]
    assert "DOCKER_CONTEXT" not in launched[0][1]["env"]
