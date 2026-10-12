"""LOCUS-433: deny-first administrator tool registry and single-sink regression."""
from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied, PodActionReceipts
from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ReservationRequest, ResourceBudget,
)
from locus_runtime.pod_tool_broker import BrokerTool, SandboxedPodToolBroker
from locus_runtime.sandbox import (
    ConfinementSelection, ExecutionPlan, HostPlatform, IsolationStrategy,
)
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from tests.gateway_support import FakeEngine


def setup(tmp_path: Path, monkeypatch, *, engine=None, exec_path=None):
    exe = exec_path or tmp_path / "approved_tool"
    if not exe.exists():
        exe.write_text("fixed-test-tool\n", encoding="utf-8")
    ledger = PodReservationLedger(tmp_path / "pods.db")
    gateway = Gateway(engine or FakeEngine(), lambda _: None)
    parent = gateway.open_session(
        run_id="catalog-supervisor", principal="alice", engine="supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"fixed_tool"}),
            read_roots=(str(tmp_path),), write_roots=(str(tmp_path),),
            allowed_executables=(str(exe),),
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
            "node_id": "analysis", "depends_on": [], "capabilities": ["fixed_tool"],
            "risk_ceiling": "R2",
            "budget": {
                "actions": 5, "tokens": 500, "wall_seconds": 60,
                "spend_microunits": 1_000_000,
            },
            "deliverables": ["analysis"], "acceptance_criteria": ["bounded tool"],
        }]},
        intent=TaskIntent("run an administrator-registered tool", "professional",
                          "alice", ("fixed tool only",)),
        parent_authority=TaskAuthority(
            frozenset({"fixed_tool"}), "R2",
            TaskBudget(12, 1200, 120, 2_000_000),
        ),
    )
    ledger.register_parent(plan, parent, max_tool_calls=12)
    reservation = ledger.reserve(
        plan, parent, ReservationRequest(
            "analysis", "attempt-fixed-tool", "v1",
            ResourceBudget(5, 4, 400, 45, 700_000), 70,
        ),
    )
    lease = ledger.admit(plan, parent, reservation.reservation_id, engine="worker")
    receipts = PodActionReceipts(ledger, supervisor=parent)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.HARDENED_DOCKER, HostPlatform.LINUX, "trusted-docker",
        ),
    )
    monkeypatch.setattr(broker_module, "detect_host_platform", lambda: HostPlatform.LINUX)
    invocations: list[tuple[list[str], dict]] = []

    def fake_subprocess(argv, **kwargs):
        invocations.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(broker_module.subprocess, "run", fake_subprocess)
    tool = BrokerTool("fixed_tool", (str(exe), "--fixed"), str(tmp_path), 10)
    configured = {"fixed_tool": tool}
    broker = SandboxedPodToolBroker(receipts, configured)
    return broker, receipts, lease, parent, configured, invocations


def test_authorized_tool_uses_single_broker_and_fenced_receipt(tmp_path, monkeypatch):
    app, receipts, lease, _, _, invokes = setup(tmp_path, monkeypatch)
    result = app.execute(lease, tool_name="fixed_tool", action_idempotency_key="fixed-1")
    assert result.receipt.state == "succeeded"
    assert result.exit_code == 0
    assert result.backend == IsolationStrategy.HARDENED_DOCKER.value
    assert len(invokes) == 1
    argv, options = invokes[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]


def test_registry_is_copied_readonly_and_descriptors_are_frozen(tmp_path, monkeypatch):
    app, _, lease, _, mapping, invokes = setup(tmp_path, monkeypatch)
    descriptor = mapping["fixed_tool"]
    mapping.clear()
    with pytest.raises(TypeError):
        app._tools["fixed_tool"] = descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor.name = "tampered"
    app.execute(lease, tool_name="fixed_tool", action_idempotency_key="catalog-immutable")
    assert len(invokes) == 1


def test_no_unknown_tool_or_dynamic_command_is_accepted(tmp_path, monkeypatch):
    app, receipts, lease, _, _, invokes = setup(tmp_path, monkeypatch)
    with pytest.raises(AdmissionDenied, match="not registered"):
        app.execute(lease, tool_name="unregistered", action_idempotency_key="unknown")
    with pytest.raises(AdmissionDenied, match="trusted admitted pod"):
        app.execute(object(), tool_name="fixed_tool", action_idempotency_key="forged")
    with pytest.raises(AdmissionDenied, match="string"):
        app.execute(lease, tool_name=123, action_idempotency_key="nonstring")
    assert not invokes
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("descriptor", [
    ("", ("/bin/true",), "/tmp", 10),
    ("bad name", ("/bin/true",), "/tmp", 10),
    ("fixed_tool", ("relative",), "/tmp", 10),
    ("fixed_tool", ("/bin/true",), "relative", 10),
    ("fixed_tool", (), "/tmp", 10),
    ("fixed_tool", ("/bin/sh", "-c", "cat /etc/shadow"), "/tmp", 10),
    ("fixed_tool", ("/bin/true", "bad\x00value"), "/tmp", 10),
    ("fixed_tool", ("/bin/true", 5), "/tmp", 10),
    ("fixed_tool", ("/bin/true",), "/tmp", 300),
])
def test_invalid_registered_descriptors_are_rejected(descriptor):
    with pytest.raises(ValueError):
        BrokerTool(*descriptor)


def test_unapproved_child_capability_blocks_dispatch(tmp_path, monkeypatch):
    app, receipts, lease, _, _, invokes = setup(tmp_path, monkeypatch)
    other = tmp_path / "other_tool"
    other.write_text("other\n", encoding="utf-8")
    unapproved = SandboxedPodToolBroker(receipts, {"fixed_tool": BrokerTool(
        "fixed_tool", (str(other), "--fixed"), str(tmp_path), 10,
    )})
    with pytest.raises(AdmissionDenied, match="executable outside"):
        unapproved.execute(lease, tool_name="fixed_tool", action_idempotency_key="unapproved")
    assert not invokes
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_gateway_denial_never_invokes_runner(tmp_path, monkeypatch):
    app, receipts, lease, _, _, invokes = setup(
        tmp_path, monkeypatch, engine=FakeEngine(allow={"agent_policy": False}),
    )
    with pytest.raises(AdmissionDenied):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="policy-deny")
    assert not invokes
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_one_effect_per_idempotency_key(tmp_path, monkeypatch):
    app, _, lease, _, _, invokes = setup(tmp_path, monkeypatch)
    app.execute(lease, tool_name="fixed_tool", action_idempotency_key="once")
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="once")
    assert len(invokes) == 1


def test_timeout_stays_uncertain_when_docker_cleanup_fails(tmp_path, monkeypatch):
    app, receipts, lease, _, _, invokes = setup(tmp_path, monkeypatch)
    def runner(argv, **kwargs):
        invokes.append((argv, kwargs))
        if argv[:2] == ["docker", "kill"]:
            raise OSError("daemon disappeared")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(broker_module.subprocess, "run", runner)
    with pytest.raises(subprocess.TimeoutExpired):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="timeout")
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="timeout")
    assert len(invokes) == 2
    assert invokes[1][0][0:2] == ["docker", "kill"]
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT state FROM pod_action_receipt").fetchone()[0] == "uncertain"


def test_symlinked_registered_executable_fails_closed(tmp_path, monkeypatch):
    executable = tmp_path / "actual_tool"
    executable.write_text("actual\n", encoding="utf-8")
    alias = tmp_path / "tool_alias"
    alias.symlink_to(executable)
    app, receipts, lease, _, _, invokes = setup(
        tmp_path, monkeypatch, exec_path=alias,
    )
    with pytest.raises(AdmissionDenied, match="linked"):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="symlink")
    assert not invokes
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_unconfined_planner_cannot_bypass_existing_broker(tmp_path, monkeypatch):
    app, receipts, lease, _, _, invokes = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        broker_module.SandboxManager, "plan",
        lambda self, spec, policy: ExecutionPlan(
            backend="hardened-docker", command=spec.command,
            strategy=IsolationStrategy.HARDENED_DOCKER,
        ),
    )
    with pytest.raises(AdmissionDenied, match="direct host execution"):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="raw-host")
    assert not invokes
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_revoked_parent_cannot_dispatch_registered_tool(tmp_path, monkeypatch):
    app, receipts, lease, parent, _, invokes = setup(tmp_path, monkeypatch)
    receipts.reservations.revoke_parent(parent)
    with pytest.raises(AdmissionDenied):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="revoked")
    assert not invokes


def test_catalog_and_dispatch_sink_are_immutable(tmp_path, monkeypatch):
    app, receipts, lease, _, mapping, launches = setup(tmp_path, monkeypatch)
    original = app.catalog_digest
    other = BrokerTool("fixed_tool", (str(tmp_path / "other"),), str(tmp_path))
    mapping["fixed_tool"] = other
    assert app.catalog_digest == original
    assert app._tools["fixed_tool"] != other
    with pytest.raises(FrozenInstanceError):
        app._tools = mapping
    with pytest.raises(FrozenInstanceError):
        app._executor = None
    with pytest.raises(FrozenInstanceError):
        app._catalog_digest = "0" * 64
    with pytest.raises((AttributeError, TypeError)):
        app.rogue_state = "not allowed"
    app.execute(lease, tool_name="fixed_tool", action_idempotency_key="frozen-identity")
    assert len(launches) == 1
    assert app.catalog_digest == original


def test_catalog_digest_is_stable_but_changes_for_descriptor_revision(tmp_path, monkeypatch):
    app, receipts, _, _, mapping, _ = setup(tmp_path, monkeypatch)
    first = app.catalog_digest
    assert len(first) == 64
    assert all(character in "0123456789abcdef" for character in first)
    same = SandboxedPodToolBroker(receipts, dict(reversed(list(mapping.items()))))
    assert same.catalog_digest == first
    descriptor = mapping["fixed_tool"]
    changed = SandboxedPodToolBroker(receipts, {
        "fixed_tool": BrokerTool(
            "fixed_tool", descriptor.argv + ("--different",),
            descriptor.cwd, descriptor.max_seconds,
        ),
    })
    assert changed.catalog_digest != first
    changed_timeout = SandboxedPodToolBroker(receipts, {
        "fixed_tool": BrokerTool(
            "fixed_tool", descriptor.argv,
            descriptor.cwd, descriptor.max_seconds + 1,
        ),
    })
    assert changed_timeout.catalog_digest not in {first, changed.catalog_digest}


def test_catalog_repr_never_exposes_registered_argv(tmp_path, monkeypatch):
    app, _, _, _, mapping, _ = setup(tmp_path, monkeypatch)
    descriptor = mapping["fixed_tool"]
    assert descriptor.argv[0] not in repr(app)
    assert descriptor.cwd not in repr(app)
