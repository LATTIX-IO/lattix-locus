"""LOCUS-437: exact trusted engine choices survive supervisor restarts.

Uses real SQLite scheduler and Gateway leases, synthetic workers only.
"""
from __future__ import annotations

import pytest

from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ResourceBudget,
)
from locus_runtime.task_dispatch_driver import (
    EngineBinding, GatewayTaskDriver, TrustedEnginePins,
)
from locus_runtime.task_scheduler import DurableTaskScheduler
from tests.unit.test_task_dispatch_driver import TrustedVerifier, Worker
from tests.unit.test_task_engine_selection import driver, pins
from tests.unit.test_task_scheduler import build


def test_engine_pins_survive_restart_and_refuse_substitution(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    first_native, first_codex = Worker(), Worker()
    selector = pins(plan.digest)
    first_wave = driver(
        scheduler, first_native, first_codex, selector,
    ).run_wave(plan, parent)
    assert {outcome.status for outcome in first_wave.outcomes} == {"verified"}

    reopened = DurableTaskScheduler(
        PodReservationLedger(tmp_path / "pods.db"), max_inflight=2,
    )
    reopened.bind(plan, parent, max_tool_calls=12)
    native, codex = Worker(), Worker()
    replacement = TrustedEnginePins(
        plan.digest,
        tuple(
            (node, "cli/codex" if node == "research" else engine)
            for node, engine in selector.node_engines
        ),
    )
    with pytest.raises(AdmissionDenied, match="cannot change"):
        driver(reopened, native, codex, replacement).run_wave(plan, parent)
    assert not native.calls and not codex.calls

    outcome = driver(reopened, native, codex, selector).run_until_idle(plan, parent)
    assert outcome.status == "done"
    assert {node for node, _, _ in native.calls} == {"compose", "publish"}
    assert codex.calls == []
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0


def test_mismatched_engine_rejected_at_authoritative_child_admission(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    scheduler.bind_engine_pins(plan, parent, pins(plan.digest).node_engines)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None and claim.node_id == "research"
    held = scheduler.reserve(
        plan, parent, claim,
        budget=ResourceBudget(3, 2, 120, 20, 0),
        lease_seconds=30,
    )
    with pytest.raises(AdmissionDenied, match="does not match durable pin"):
        scheduler.admit(plan, parent, claim, engine="cli/codex")
    assert ledger.get(held.reservation_id).state == "reserved"
    lease = scheduler.admit(plan, parent, claim, engine="native/deep-agents")
    assert lease.run_id.startswith(parent.caller.run_id + ":research:")
    lease.close(
        total=ResourceBudget(1, 1, 25, 5, 0),
        evidence="a" * 64,
    )
    scheduler.verify_closed(
        plan, parent, claim, artifact_digest="a" * 64,
        verifier=TrustedVerifier(),
    )
    assert scheduler.states(plan, parent)["research"] == "verified"


def test_cannot_retroactively_pin_unpinned_started_graph(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    standard = GatewayTaskDriver(
        scheduler,
        engines=(EngineBinding(
            "native/deep-agents", frozenset({"list_issues"}), Worker(),
        ),),
        verifier=TrustedVerifier(),
        max_parallel=2,
    )
    assert len(standard.run_wave(plan, parent).outcomes) == 2
    with pytest.raises(AdmissionDenied, match="already started"):
        driver(scheduler, Worker(), Worker(), pins(plan.digest)).run_wave(plan, parent)


def test_durable_pins_prevent_unpinned_driver_from_claiming(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    scheduler.bind_engine_pins(plan, parent, pins(plan.digest).node_engines)
    ordinary = GatewayTaskDriver(
        scheduler,
        engines=(EngineBinding(
            "native/deep-agents", frozenset({"list_issues"}), Worker(),
        ),),
        verifier=TrustedVerifier(),
    )
    with pytest.raises(AdmissionDenied, match="requires exact engine pins"):
        ordinary.run_wave(plan, parent)
    assert all(s == "pending" for s in scheduler.states(plan, parent).values())
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0


@pytest.mark.parametrize("tamper", ["child", "parent"])
def test_corrupt_durable_binding_fails_closed_before_child(tmp_path, tamper):
    scheduler, ledger, plan, parent = build(tmp_path)
    scheduler.bind_engine_pins(plan, parent, pins(plan.digest).node_engines)
    with ledger._connect() as db:
        ledger._tx(db)
        if tamper == "child":
            db.execute(
                "UPDATE task_dispatch_node SET pinned_engine_id=? "
                "WHERE parent_run_id=? AND node_id=?",
                ("cli/codex", parent.caller.run_id, "research"),
            )
        else:
            db.execute(
                "UPDATE task_dispatch_parent SET engine_pins_json=? "
                "WHERE parent_run_id=?",
                ('[["research","cli/codex"]]', parent.caller.run_id),
            )
    with pytest.raises(AdmissionDenied, match="scheduler engine binding integrity"):
        scheduler.ready(plan, parent)
    with pytest.raises(AdmissionDenied):
        driver(scheduler, Worker(), Worker(), pins(plan.digest)).run_wave(plan, parent)


def test_idempotent_exact_binding_does_not_change_existing_attempts(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    same = pins(plan.digest).node_engines
    scheduler.bind_engine_pins(plan, parent, same)
    scheduler.bind_engine_pins(plan, parent, tuple(reversed(same)))
    assert scheduler.ready(plan, parent) == ("research", "triage")


def test_incomplete_direct_pin_mapping_is_denied_before_claim(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    with pytest.raises(AdmissionDenied, match="node set mismatch"):
        scheduler.bind_engine_pins(
            plan, parent,
            (("research", "native/deep-agents"),),
        )
    assert scheduler.ready(plan, parent) == ("research", "triage")
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
