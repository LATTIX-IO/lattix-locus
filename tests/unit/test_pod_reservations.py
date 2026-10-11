"""LOCUS-433 transactional admission: adversarial, concurrent and restart tests."""
from __future__ import annotations

import concurrent.futures
import dataclasses
import sqlite3
import threading

import pytest

from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ReservationRequest, ResourceBudget,
)
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from tests.gateway_support import FakeEngine


class Clock:
    now = 100000

    def __call__(self) -> int:
        return self.now


def budget(actions=5, tools=4, tokens=500, seconds=60, spend=2_000_000):
    return ResourceBudget(actions, tools, tokens, seconds, spend)


def zero():
    return ResourceBudget.zero()


def build(tmp_path, *, parent_actions=18, parent_tokens=1800, parent_tools=12):
    clock = Clock()
    ledger = PodReservationLedger(tmp_path / "pods.db", clock=clock)
    gateway = Gateway(FakeEngine(), lambda _: None)
    limit = TaskBudget(parent_actions, parent_tokens, 120, 8_000_000)
    parent = gateway.open_session(
        run_id="session-research", principal="alice", engine="trusted-supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"list_issues", "read_file"}),
            max_actions=parent_actions, max_tool_calls=parent_tools,
            budget=BudgetFigures(tokens_used=0, max_tokens=parent_tokens,
                duration_used_seconds=0, max_duration_seconds=120,
                cost_used_usd=0, max_cost_usd=8),
            max_risk_class=RiskClass.R2,
        ),
    )
    def node(name):
        return {
            "node_id": name, "depends_on": [],
            "capabilities": ["list_issues"], "risk_ceiling": "R2",
            "budget": {
                "actions": 5, "tokens": 550, "wall_seconds": 70,
                "spend_microunits": 2_500_000,
            },
            "deliverables": [f"{name} output"], "acceptance_criteria": ["independent check"],
        }
    plan = compile_task_plan(
        {"contract_version": "1.0", "nodes": [node("research"), node("analysis"), node("documents")]},
        intent=TaskIntent("Complete a cross-domain brief", "professional", "alice", ("all artifacts checked",)),
        parent_authority=TaskAuthority(frozenset({"list_issues","read_file"}), "R2", limit)
    )
    ledger.register_parent(plan, parent, max_tool_calls=parent_tools)
    return ledger, plan, parent, clock


def request(name="research", attempt="run-1", *, amount=None, lease=90):
    return ReservationRequest(name, attempt, "pod-v1", amount or budget(), lease)


def test_reserve_is_durable_and_budget_is_conserved_across_settle_and_release(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    first = ledger.reserve(plan, parent, request())
    before = ledger.snapshot(parent.caller.run_id)
    assert first.plan_digest == plan.digest
    assert before.held.actions == 5 and before.available.actions == 13
    assert ledger.reserve(plan, parent, request()).reservation_id == first.reservation_id
    lease = ledger.admit(plan, parent, first.reservation_id, engine="ephemeral-worker")
    assert lease.run_id.startswith("session-research:research:")
    assert lease.authorize(kind="tool_call", tool="list_issues").outcome == "allow"
    assert lease.authorize(kind="tool_call", tool="send_email").outcome == "deny"
    consumed = ResourceBudget(2, 1, 120, 12, 200000)
    lease.settle(consumed)
    lease.settle(consumed)  # cumulative retry is a no-op, not double spend
    midway = ledger.snapshot(parent.caller.run_id)
    assert midway.used == consumed
    assert midway.held == first.requested.subtract(consumed)
    final = lease.close(total=consumed, evidence="trace-123 verified")
    assert final.state == "closed"
    snapshot = ledger.snapshot(parent.caller.run_id)
    assert snapshot.used == consumed
    assert snapshot.held == zero()
    assert snapshot.available == snapshot.limits.subtract(consumed)
    with pytest.raises(AdmissionDenied):
        lease.authorize(kind="tool_call", tool="list_issues")
    reopened = PodReservationLedger(tmp_path / "pods.db")
    assert reopened.snapshot(parent.caller.run_id).used == consumed
    events = [entry[0] for entry in reopened.audit_events(parent.caller.run_id)]
    assert events == ["parent_registered", "reserved", "admitting", "active",
                      "usage_settled", "usage_settled", "closed"]


def test_parent_budget_is_never_double_reserved_by_parallel_contenders(tmp_path):
    ledger, plan, parent, _ = build(tmp_path, parent_actions=18, parent_tokens=1800, parent_tools=8)
    # 30 concurrent reservers compete for 2 allowed reservations of 5 actions.
    barrier = threading.Barrier(30)
    def contender(i):
        other = PodReservationLedger(tmp_path / "pods.db")
        barrier.wait()
        name = ("research", "analysis", "documents")[i % 3]
        try:
            return other.reserve(plan, parent, request(name, f"attempt-{i}"))
        except AdmissionDenied:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(contender, range(30)))
    successes = [r for r in results if r is not None]
    assert len(successes) == 2
    snap = ledger.snapshot(parent.caller.run_id)
    assert snap.held.actions == 10 and snap.available.tool_calls == 0
    assert snap.held.tokens <= snap.limits.tokens


def test_idempotency_collision_cross_parent_or_changed_revision_is_rejected(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    first = ledger.reserve(plan, parent, request())
    assert ledger.reserve(plan, parent, request()).reservation_id == first.reservation_id
    with pytest.raises(AdmissionDenied, match="idempotency collision"):
        ledger.reserve(plan, parent, request(attempt="run-1", amount=budget(actions=2, tools=1)))
    with pytest.raises(AdmissionDenied, match="node"):
        ledger.reserve(plan, parent, request(name="unknown", attempt="forged"))
    altered = dataclasses.replace(plan, digest="c" * 64)
    with pytest.raises(AdmissionDenied, match="plan binding"):
        ledger.reserve(altered, parent, request(attempt="second"))


def test_retry_after_reserve_before_open_is_safe_and_expired_reserve_releases(tmp_path):
    ledger, plan, parent, clock = build(tmp_path)
    reserved = ledger.reserve(plan, parent, request(lease=90))
    # Crash (no child session): new ledger can still see the exact reservation.
    restart = PodReservationLedger(tmp_path / "pods.db", clock=clock)
    assert restart.get(reserved.reservation_id).state == "reserved"
    clock.now += 91
    assert restart.recover_expired() == (reserved.reservation_id,)
    assert restart.get(reserved.reservation_id).state == "canceled"
    assert restart.snapshot(parent.caller.run_id).held == zero()
    with pytest.raises(AdmissionDenied):
        restart.admit(plan, parent, reserved.reservation_id, engine="agent")


def test_expired_active_attempt_is_uncertain_and_retains_budget(tmp_path):
    ledger, plan, parent, clock = build(tmp_path)
    reserved = ledger.reserve(plan, parent, request())
    lease = ledger.admit(plan, parent, reserved.reservation_id, engine="agent")
    assert lease.authorize(kind="tool_call", tool="list_issues").outcome == "allow"
    clock.now += 91
    restarted = PodReservationLedger(tmp_path / "pods.db", clock=clock)
    assert restarted.recover_expired() == (reserved.reservation_id,)
    assert restarted.get(reserved.reservation_id).state == "uncertain"
    assert restarted.snapshot(parent.caller.run_id).held.actions == 5
    with pytest.raises(AdmissionDenied, match="pod lease"):
        lease.authorize(kind="tool_call", tool="list_issues")
    # Explicit reconciliation with final usage can release unused reservations.
    restarted.finish(reserved.reservation_id, budget(2, 1, 100, 10, 0),
                     evidence="external tool ledger reconciled")
    assert restarted.snapshot(parent.caller.run_id).held == zero()


def test_cancelled_unused_attempt_frees_budget_and_forbids_execution(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    r = ledger.reserve(plan, parent, request())
    assert ledger.cancel_reserved(r.reservation_id).state == "canceled"
    assert ledger.snapshot(parent.caller.run_id).held == zero()
    with pytest.raises(AdmissionDenied):
        ledger.admit(plan, parent, r.reservation_id, engine="agent")
    # Same idempotency key never silently becomes a second execution attempt.
    assert ledger.reserve(plan, parent, request()).state == "canceled"


def test_parent_revocation_disables_lease_and_future_admission(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    first = ledger.reserve(plan, parent, request())
    lease = ledger.admit(plan, parent, first.reservation_id, engine="agent")
    next_one = ledger.reserve(plan, parent, request("analysis", "waiting"))
    ledger.revoke_parent(parent)
    assert ledger.get(first.reservation_id).state == "revoked"
    assert ledger.get(next_one.reservation_id).state == "canceled"
    with pytest.raises(AdmissionDenied):
        lease.authorize(kind="tool_call", tool="list_issues")
    with pytest.raises(AdmissionDenied):
        ledger.reserve(plan, parent, request(attempt="after-revoke"))


def test_reject_unbounded_or_mismatched_claims(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    with pytest.raises(AdmissionDenied):
        ledger.reserve(plan, parent, request(amount=budget(actions=6)))
    with pytest.raises(ValueError):
        request(amount=budget(seconds=100), lease=90)
    with pytest.raises(ValueError):
        ResourceBudget(True, 1, 1, 1, 0)
    with pytest.raises(ValueError):
        request(amount=ResourceBudget(1, 2, 1, 1, 0))
    with pytest.raises(AdmissionDenied):
        ledger.reserve(plan, parent, request(amount=budget(tokens=551)))


def test_cumulative_settlement_cannot_decrease_or_overdraw(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    r = ledger.reserve(plan, parent, request())
    lease = ledger.admit(plan, parent, r.reservation_id, engine="agent")
    lease.settle(budget(2, 1, 120, 12, 1000))
    with pytest.raises(AdmissionDenied, match="monotone"):
        lease.settle(budget(1, 1, 120, 12, 1000))
    with pytest.raises(AdmissionDenied, match="monotone"):
        lease.settle(budget(2, 1, 501, 12, 1000))
    assert ledger.snapshot(parent.caller.run_id).used.tokens == 120


def test_non_delegating_parent_cannot_register_ledger(tmp_path):
    clock = Clock()
    ledger = PodReservationLedger(tmp_path / "pods.db", clock=clock)
    gw = Gateway(FakeEngine(), lambda _: None)
    session = gw.open_session(run_id="x", principal="alice", engine="normal",
        capabilities=Capabilities(max_actions=10))
    comp = compile_task_plan(
        {"contract_version": "1.0", "nodes": [{
            "node_id": "n", "depends_on": [], "capabilities": [], "risk_ceiling": "R0",
            "budget": {"actions": 1, "tokens": 1, "wall_seconds": 1, "spend_microunits": 0},
            "deliverables": ["out"], "acceptance_criteria": ["check"],
        }]},
        intent=TaskIntent("goal", "a", "alice", ("check",)),
        parent_authority=TaskAuthority(frozenset(), "R0", TaskBudget(10, 100, 10, 0))
    )
    with pytest.raises(AdmissionDenied, match="cannot delegate"):
        ledger.register_parent(comp, session, max_tool_calls=1)


def test_schema_fail_closed_for_unknown_migration_and_audit_is_durable(tmp_path):
    db_path = tmp_path / "pods.db"
    PodReservationLedger(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE schema_meta SET version = 999")
    with pytest.raises(RuntimeError, match="unsupported reservation-ledger schema"):
        PodReservationLedger(db_path)



def test_concurrent_schema_startup_is_atomic(tmp_path):
    # Repeat on multiple new paths: schema+WAL lock races are intermittent
    # and first-open contention must never reject independent supervisors.
    for n in range(3):
        path = tmp_path / f"initialized-together-{n}.db"
        def start(_i):
            service = PodReservationLedger(path)
            with service._connect() as db:
                return db.execute("SELECT version FROM schema_meta").fetchall()[0][0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            assert list(pool.map(start, range(12))) == [1] * 12
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM schema_meta").fetchone()[0] == 1


def test_failed_gateway_session_admission_releases_unstarted_hold(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    record = ledger.reserve(plan, parent, request())
    # Root is already registered; admission cannot silently evict it to
    # create a worker session.
    parent.gateway._max_sessions = 1
    with pytest.raises(RuntimeError, match="capacity"):
        ledger.admit(plan, parent, record.reservation_id, engine="unlaunchable")
    assert ledger.get(record.reservation_id).state == "canceled"
    assert ledger.snapshot(parent.caller.run_id).held == zero()


def test_snapshot_version_advances_on_settlement(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    r = ledger.reserve(plan, parent, request())
    old_version = ledger.snapshot(parent.caller.run_id).version
    lease = ledger.admit(plan, parent, r.reservation_id, engine="agent")
    version_admitted = ledger.snapshot(parent.caller.run_id).version
    assert version_admitted > old_version
    lease.settle(budget(1, 1, 10, 1, 100))
    assert ledger.snapshot(parent.caller.run_id).version > version_admitted



def test_sqlite_connection_is_closed_after_context_manager(tmp_path):
    ledger = PodReservationLedger(tmp_path / "pods.db")
    with ledger._connect() as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT version FROM schema_meta")



def test_parent_nonzero_usage_does_not_block_bounded_child(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    # Gateway independently tracks parent inference/action usage. A child
    # receives only its remaining delta, not a reset of inherited counters.
    parent.report_budget(BudgetFigures(
        tokens_used=200, max_tokens=1800,
        duration_used_seconds=8, max_duration_seconds=120,
        cost_used_usd=0.4, max_cost_usd=8,
    ))
    reserved = ledger.reserve(plan, parent, request())
    lease = ledger.admit(plan, parent, reserved.reservation_id, engine="worker")
    b = lease._session.capabilities.budget
    assert b is not None
    assert b.tokens_used == 200 and b.max_tokens == 700
    assert b.duration_used_seconds == 8 and b.max_duration_seconds == 68
    assert b.cost_used_usd == 0.4
    lease.settle(ResourceBudget(2, 1, 80, 10, 100000))
    updated = lease._session.capabilities.budget
    assert updated is not None
    assert updated.tokens_used == 280
    assert updated.duration_used_seconds == 18
    assert abs((updated.cost_used_usd or 0) - 0.5) < 1e-9


def test_parent_used_budget_cannot_be_overallocated_during_admission(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    parent.report_budget(BudgetFigures(
        tokens_used=1600, max_tokens=1800,
        duration_used_seconds=20, max_duration_seconds=120,
        cost_used_usd=1.0, max_cost_usd=8,
    ))
    reservation = ledger.reserve(plan, parent, request())
    with pytest.raises(PermissionError, match="max_tokens"):
        ledger.admit(plan, parent, reservation.reservation_id, engine="worker")
    assert ledger.get(reservation.reservation_id).state == "canceled"


def test_reconciliation_evidence_never_saved_as_plaintext(tmp_path):
    ledger, plan, parent, _ = build(tmp_path)
    reservation = ledger.reserve(plan, parent, request())
    lease = ledger.admit(plan, parent, reservation.reservation_id, engine="worker")
    secret = "Authorization: Bearer super-secret-token user=jane@example.com"
    lease.close(total=zero(), evidence=secret)
    with sqlite3.connect(tmp_path / "pods.db") as db:
        row = db.execute(
            "SELECT evidence FROM pod_attempt WHERE id=?", (reservation.reservation_id,)
        ).fetchone()
        audit = db.execute("SELECT note FROM pod_audit").fetchall()
    assert row is not None
    assert len(row[0]) == 64 and row[0] != secret
    assert all(secret not in note[0] for note in audit)
    assert all("super-secret-token" not in note[0] for note in audit)
