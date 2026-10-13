"""Worker-driven LOCUS-413 integration with deterministic fake engine adapters.

These tests exercise real Gateway child sessions and durable PodReservationLedger
rather than simulating authorization in the supervisor.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from locus_runtime.pod_reservations import ResourceBudget
from locus_runtime.task_dispatch_driver import (
    EngineBinding, GatewayTaskDriver, WorkReport,
)
from locus_runtime.task_scheduler import DurableTaskScheduler
from tests.unit.test_task_scheduler import build


USED = ResourceBudget(1, 1, 25, 5, 0)


class Worker:
    def __init__(self, *, barrier=None, fail=False, invalid=False, excessive=False):
        self.calls = []
        self.barrier = barrier
        self.fail = fail
        self.invalid = invalid
        self.excessive = excessive

    def run(self, lease, *, node, intent):
        # The adapter sees a resource-limited PodLease, not an ambient parent
        # GatewaySession or any device/filesystem/CLI authority.
        permitted = lease.authorize(kind="tool_call", tool="list_issues")
        assert permitted.outcome == "allow"
        denied = lease.authorize(kind="tool_call", tool="send_email")
        assert denied.outcome == "deny"
        self.calls.append((node.node_id, lease.run_id, intent.area))
        if self.barrier is not None and node.node_id in ("research", "triage"):
            self.barrier.wait(timeout=10)
        if self.fail:
            raise RuntimeError("unsafe-path secret=never-echo-me")
        if self.invalid:
            return WorkReport("invalid", USED)
        digest = hashlib.sha256(node.node_id.encode()).hexdigest()
        return WorkReport(
            digest,
            ResourceBudget(4, 2, 25, 5, 0) if self.excessive else USED,
        )


class TrustedVerifier:
    def __init__(self, *, accept=True, fail=False):
        self.calls = []
        self.accept = accept
        self.fail = fail

    def verify(self, **data):
        self.calls.append(dict(data))
        if self.fail:
            raise RuntimeError("verifier secret should not appear in dispatch")
        return self.accept


def driver(scheduler, worker, verifier=None, *, parallel=2, caps=None):
    verifier = verifier or TrustedVerifier()
    registered = EngineBinding(
        "test/gateway-worker", caps if caps is not None else frozenset({"list_issues"}),
        worker,
    )
    return GatewayTaskDriver(
        scheduler, engines=(registered,), verifier=verifier,
        max_parallel=parallel,
    )


def test_mock_engine_wave_diamond_is_parallel_and_verified(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    worker = Worker(barrier=threading.Barrier(2))
    verifier = TrustedVerifier()
    subject = driver(scheduler, worker, verifier)
    summary = subject.run_until_idle(plan, parent)
    assert summary.status == "done"
    assert summary.waves == 3
    assert all(value == "verified" for value in summary.node_states.values())
    assert len(worker.calls) == 4
    assert len({child for _, child, _ in worker.calls}) == 4
    assert {name for name, *_ in worker.calls[:2]} == {"research", "triage"}
    assert [result.status for result in summary.outcomes] == ["verified"] * 4
    assert {r["node_id"] for r in verifier.calls} == {
        "research", "triage", "compose", "publish",
    }
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
    assert ledger.snapshot(parent.caller.run_id).used.actions == 4
    assert subject.run_wave(plan, parent).outcomes == ()  # never replay


def test_restart_dispatches_only_unverified_predecessor_and_never_reexecutes(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    first = Worker()
    subject = driver(scheduler, first)
    wave = subject.run_wave(plan, parent)
    assert [a.status for a in wave.outcomes] == ["verified", "verified"]
    restarted = DurableTaskScheduler(
        scheduler.ledger.__class__(tmp_path / "pods.db"), max_inflight=2,
    )
    restarted.bind(plan, parent, max_tool_calls=12)
    second = Worker()
    result = driver(restarted, second).run_until_idle(plan, parent)
    assert result.status == "done"
    assert [row[0] for row in second.calls] == ["compose", "publish"]
    assert [row[0] for row in first.calls] == ["research", "triage"]


def test_missing_engine_capability_blocks_without_gateway_child_or_budget_hold(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    worker = Worker()
    subject = driver(scheduler, worker, caps=frozenset({"read_file"}))
    result = subject.run_wave(plan, parent)
    assert [(a.node_id, a.status, a.reason_code) for a in result.outcomes] == [
        ("research", "blocked", "engine_capability_unavailable"),
        ("triage", "blocked", "engine_capability_unavailable"),
    ]
    assert worker.calls == []
    snap = ledger.snapshot(parent.caller.run_id)
    assert snap.used.actions == snap.held.actions == 0
    assert scheduler.ready(plan, parent) == ()
    assert scheduler.states(plan, parent)["research"] == "blocked"


def test_failure_does_not_release_lease_or_surface_worker_secret(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    subject = driver(scheduler, Worker(fail=True))
    result = subject.run_wave(plan, parent)
    assert all(r.status == "uncertain" for r in result.outcomes)
    assert all(r.reason_code == "worker_interrupted" for r in result.outcomes)
    assert "never-echo-me" not in repr(result)
    snap = ledger.snapshot(parent.caller.run_id)
    assert snap.held.actions > 0
    assert scheduler.ready(plan, parent) == ()
    assert scheduler.claim_next(plan, parent) is None


def test_invalid_worker_artifact_and_overbudget_result_never_verify(tmp_path):
    for index, kwargs in enumerate(({"invalid": True}, {"excessive": True})):
        directory = tmp_path / str(index)
        directory.mkdir()
        scheduler, ledger, plan, parent = build(directory)
        verifier = TrustedVerifier()
        result = driver(scheduler, Worker(**kwargs), verifier).run_wave(plan, parent)
        assert all(outcome.status == "uncertain" for outcome in result.outcomes)
        assert verifier.calls == []
        assert ledger.snapshot(parent.caller.run_id).held.actions > 0
        assert not any(
            state == "verified" for state in scheduler.states(plan, parent).values()
        )


@pytest.mark.parametrize("outcome", ["rejected", "unavailable"])
def test_independent_verifier_failure_blocks_child_without_replaying(tmp_path, outcome):
    scheduler, ledger, plan, parent = build(tmp_path)
    verifier = TrustedVerifier(
        accept=outcome != "rejected", fail=outcome == "unavailable",
    )
    subject = driver(scheduler, Worker(), verifier)
    wave = subject.run_wave(plan, parent)
    expected_status = "blocked" if outcome == "rejected" else "uncertain"
    expected_code = "artifact_not_verified" if outcome == "rejected" else "verifier_unavailable"
    assert all(item.status == expected_status for item in wave.outcomes)
    assert all(item.reason_code == expected_code for item in wave.outcomes)
    assert scheduler.ready(plan, parent) == ()
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
    assert all(value != "verified" for value in scheduler.states(plan, parent).values())
    assert subject.run_wave(plan, parent).outcomes == ()
    assert set(scheduler.states(plan, parent).values()) <= {
        "pending", "blocked" if outcome == "rejected" else "active",
    }


def test_interrupt_prior_to_admission_has_zero_effect_and_no_child(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    worker = Worker()
    subject = driver(scheduler, worker)
    result = subject.run_until_idle(plan, parent, should_stop=lambda: True)
    assert result.status == "stopped"
    assert result.waves == 0
    assert all(v == "pending" for v in result.node_states.values())
    assert worker.calls == []
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0


def test_supplied_engine_order_is_explicit_and_byo_provider_is_possible(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    first, second = Worker(), Worker()
    subject = GatewayTaskDriver(
        scheduler,
        engines=(
            EngineBinding("external/approved-harness", frozenset({"read_file"}), first),
            EngineBinding("local/deep-agents", frozenset({"list_issues"}), second),
        ),
        verifier=TrustedVerifier(),
        max_parallel=2,
    )
    assert subject.run_wave(plan, parent).outcomes[0].engine_id == "local/deep-agents"
    assert first.calls == [] and len(second.calls) == 2


def test_uncertain_prior_attempt_keeps_budget_and_cannot_execute_dependent(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    subject = driver(scheduler, Worker(fail=True))
    wave = subject.run_wave(plan, parent)
    assert all(a.status == "uncertain" for a in wave.outcomes)
    with ledger._connect() as db:
        ledger._tx(db)
        db.execute("UPDATE pod_attempt SET expires_at=0 WHERE state='active'")
    assert len(ledger.recover_expired()) == 2
    assert scheduler.reconcile_interrupted(plan, parent) == ("research", "triage")
    assert subject.run_wave(plan, parent).outcomes == ()
    assert scheduler.ready(plan, parent) == ()
    assert ledger.snapshot(parent.caller.run_id).held.actions > 0


def test_registry_and_supervisor_limits_are_validated(tmp_path):
    scheduler, _, _, _ = build(tmp_path)
    verifier = TrustedVerifier()
    worker = Worker()
    with pytest.raises(ValueError):
        GatewayTaskDriver(scheduler, engines=(), verifier=verifier)
    with pytest.raises(ValueError):
        driver(scheduler, worker, parallel=3)
    with pytest.raises(ValueError):
        EngineBinding("../../unsafe", frozenset(), worker)
    with pytest.raises(ValueError):
        GatewayTaskDriver(
            scheduler,
            engines=(
                EngineBinding("local/one", frozenset(), worker),
                EngineBinding("local/one", frozenset(), worker),
            ),
            verifier=verifier,
        )
    with pytest.raises(ValueError):
        GatewayTaskDriver(
            scheduler,
            engines=(EngineBinding("local/one", frozenset(), worker),),
            verifier=verifier,
            max_parallel=True,
        )


def test_dispatcher_never_gains_a_subprocess_or_ambient_tool_path():
    source = (
        Path(__file__).resolve().parents[2] /
        "locus_runtime" / "task_dispatch_driver.py"
    ).read_text(encoding="utf-8")
    for prohibited in ("subprocess.", "os.system(", "os.popen(", "authorize_action(",
                       "Gateway.open_session(", "shell=True", "eval("):
        assert prohibited not in source


def test_run_summary_requires_manual_reconciliation_after_worker_interruption(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    summary = driver(scheduler, Worker(fail=True)).run_until_idle(plan, parent)
    assert summary.status == "blocked"
    assert any(item.status == "uncertain" for item in summary.outcomes)
    assert summary.node_states["compose"] == "pending"
