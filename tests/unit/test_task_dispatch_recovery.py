"""LOCUS-413/433: restart the real pod driver without reissuing uncertain work.

Tests exercise durable SQLite pod admission, Gateway child authorization and
independently verified artifacts with trusted fake worker adapters.
"""
from __future__ import annotations

import concurrent.futures

import pytest

from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ReservationRequest,
)
from locus_runtime.task_scheduler import DurableTaskScheduler
from tests.unit.test_task_dispatch_driver import (
    TrustedVerifier, Worker, driver,
)
from tests.unit.test_task_scheduler import REQUEST, build


def restart(tmp_path, *, parallel=2):
    return DurableTaskScheduler(
        PodReservationLedger(tmp_path / "pods.db"), max_inflight=parallel,
    )


def test_crashed_claim_is_resumed_and_all_dependent_waves_verify(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    stranded = scheduler.claim_next(plan, parent)
    assert stranded is not None and stranded.node_id == "research"
    recovered = restart(tmp_path)
    worker = Worker()
    summary = driver(recovered, worker).run_until_idle(plan, parent)
    assert summary.status == "done"
    assert all(value == "verified" for value in summary.node_states.values())
    assert len(worker.calls) == 4
    assert len({child for _, child, _ in worker.calls}) == 4
    assert {name for name, *_ in worker.calls[:2]} == {"research", "triage"}
    assert recovered.recover_unstarted(plan, parent) == ()


def test_crash_between_ledger_reservation_and_scheduler_commit_executes_once(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    original = ledger.reserve(
        plan, parent, ReservationRequest(
            claim.node_id, claim.attempt_key, "v1", REQUEST, 30,
        ),
    )
    recovered = restart(tmp_path)
    worker = Worker()
    result = driver(recovered, worker).run_until_idle(plan, parent)
    assert result.status == "done"
    assert [name for name, *_ in worker.calls].count("research") == 1
    assert recovered.ledger.get(original.reservation_id).state == "closed"
    with recovered.ledger._connect() as db:
        rows = db.execute(
            "SELECT id FROM pod_attempt WHERE parent_id=? AND node_id='research'",
            (parent.caller.run_id,),
        ).fetchall()
        assert [r["id"] for r in rows] == [original.reservation_id]


def test_canceled_unadmitted_hold_rekeys_then_runs_without_old_side_effect(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    original = scheduler.reserve(
        plan, parent, claim, budget=REQUEST, lease_seconds=30,
    )
    assert ledger.cancel_reserved(original.reservation_id).state == "canceled"
    recovered = restart(tmp_path)
    worker = Worker()
    summary = driver(recovered, worker).run_until_idle(plan, parent)
    assert summary.status == "done"
    assert len(worker.calls) == 4
    with recovered.ledger._connect() as db:
        attempts = db.execute(
            "SELECT attempt_key,state FROM pod_attempt "
            "WHERE parent_id=? AND node_id='research' ORDER BY rowid",
            (parent.caller.run_id,),
        ).fetchall()
    assert len(attempts) == 2
    assert [record["state"] for record in attempts] == ["canceled", "closed"]
    assert attempts[0]["attempt_key"] != attempts[1]["attempt_key"]
    with pytest.raises(AdmissionDenied):
        recovered.reserve(
            plan, parent, claim, budget=REQUEST, lease_seconds=30,
        )


def test_admitted_child_with_lost_scheduler_commit_is_quarantined_not_rerun(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    reservation = ledger.reserve(
        plan, parent, ReservationRequest(
            claim.node_id, claim.attempt_key, "v1", REQUEST, 30,
        ),
    )
    admitted = ledger.admit(
        plan, parent, reservation.reservation_id, engine="prior-agent",
    )
    assert admitted.run_id
    recovered = restart(tmp_path)
    worker = Worker()
    report = driver(recovered, worker).run_until_idle(plan, parent)
    assert report.status == "blocked"
    assert report.node_states["research"] == "uncertain"
    assert not any(name == "research" for name, *_ in worker.calls)
    assert ledger.snapshot(parent.caller.run_id).held.actions >= REQUEST.actions
    assert report.status != "done"


def test_reserved_claim_does_not_disappear_on_unavailable_engine(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    reserve = scheduler.reserve(
        plan, parent, claim, budget=REQUEST, lease_seconds=30,
    )
    recovered = restart(tmp_path)
    unsupported = driver(
        recovered, Worker(), caps=frozenset({"read_file"}),
    )
    outcome = unsupported.run_wave(plan, parent, recover_unstarted=True)
    research = next(item for item in outcome.outcomes if item.node_id == "research")
    assert research.status == "uncertain"
    assert research.reason_code == "engine_capability_unavailable"
    assert ledger.get(reserve.reservation_id).state == "reserved"
    assert recovered.states(plan, parent)["research"] == "reserved"
    # A later operator-authorized worker may still consume the original hold.
    successful = driver(recovered, Worker(), verifier=TrustedVerifier())
    resumed = successful.run_wave(plan, parent, recover_unstarted=True)
    assert any(item.node_id == "research" and item.status == "verified"
               for item in resumed.outcomes)
    assert ledger.get(reserve.reservation_id).state == "closed"


def test_recovery_cannot_relaunch_worker_after_uncertain_side_effect(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    first = Worker(fail=True)
    stopped = driver(scheduler, first).run_wave(plan, parent)
    assert all(item.status == "uncertain" for item in stopped.outcomes)
    recovered = restart(tmp_path)
    replacement = Worker()
    summary = driver(recovered, replacement).run_until_idle(plan, parent)
    assert summary.status != "done"
    assert not any(name in {"research", "triage"} for name, *_ in replacement.calls)
    assert ledger.snapshot(parent.caller.run_id).held.actions > 0


def test_two_recovering_supervisors_cannot_duplicate_admission(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    first = restart(tmp_path)
    second = restart(tmp_path)
    shared_worker = Worker()
    a = driver(first, shared_worker, parallel=1)
    b = driver(second, shared_worker, parallel=1)
    # Only one lease can transition reserved -> admitting -> active, even
    # when both coordinators reconstruct the same durable claim at once.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run.run_wave, plan, parent, recover_unstarted=True)
            for run in (a, b)
        ]
        waves = [task.result() for task in futures]
    assert [name for name, *_ in shared_worker.calls].count("research") == 1
    assert len({child for name, child, _ in shared_worker.calls if name == "research"}) == 1
    assert sum(x.status == "verified" and x.node_id == "research"
               for wave in waves for x in wave.outcomes) == 1
    assert ledger.snapshot(parent.caller.run_id).used.actions <= 2


@pytest.mark.parametrize("invalid", [None, "true", 1])
def test_recovery_requires_explicit_boolean_flag(tmp_path, invalid):
    scheduler, _, plan, parent = build(tmp_path)
    subject = driver(scheduler, Worker())
    with pytest.raises(ValueError, match="recovery flag"):
        subject.run_wave(plan, parent, recover_unstarted=invalid)
    with pytest.raises(ValueError, match="recovery flag"):
        subject.run_until_idle(plan, parent, recover_unstarted=invalid)


def test_cancel_before_recovery_does_not_open_child_or_reserve_budget(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    worker = Worker()
    recovered = driver(restart(tmp_path), worker)
    report = recovered.run_until_idle(
        plan, parent, should_stop=lambda: True,
    )
    assert report.status == "stopped"
    assert not worker.calls
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
