"""LOCUS-433: action write-ahead barrier, replay protection and hostile receipts."""
from __future__ import annotations

import concurrent.futures
import sqlite3
import threading

import pytest

from locus_runtime.action_receipts import ActionReplayDenied, PodActionReceipts
from locus_runtime.gateway import Capabilities, Gateway
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from tests.gateway_support import FakeEngine
from tests.unit.test_pod_reservations import build, request


def setup(tmp_path):
    ledger, plan, parent, clock = build(tmp_path)
    reserved = ledger.reserve(plan, parent, request())
    lease = ledger.admit(plan, parent, reserved.reservation_id, engine="receipt-test")
    receipts = PodActionReceipts(ledger)
    return ledger, receipts, parent, lease, clock


_ACTION = {"kind": "tool_call", "tool": "list_issues"}


def test_execute_once_creates_write_ahead_record_and_digest_only(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    called = []
    def external():
        # Simulates an external effect invoked ONLY after the dispatch barrier.
        events = receipts.audit(rec.action_id)
        called.append(events[-1][0])
        return "Authorization: Bearer secret and PII"
    rec = receipts.prepare(lease, idempotency_key="send-001", authorization=_ACTION)
    # Calling execute_once with that same key must use the existing prepared
    # record rather than allocate a second execution.
    final, result = receipts.execute_once(
        lease, idempotency_key="send-001", authorization=_ACTION,
        invoke=external,
    )
    assert final.action_id == rec.action_id
    assert result.startswith("Authorization")
    assert called == ["dispatching"]
    assert final.state == "succeeded"
    assert [x[0] for x in receipts.audit(final.action_id)] == [
        "prepared", "dispatching", "succeeded",
    ]
    with sqlite3.connect(tmp_path / "pods.db") as db:
        dump = "\n".join(str(row) for row in db.execute(
            "SELECT fingerprint,outcome_digest FROM pod_action_receipt"
        ).fetchall())
    assert "Bearer" not in dump and "PII" not in dump


def test_same_action_key_cannot_repeat_external_effect(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    count = []
    receipts.execute_once(lease, idempotency_key="op-a", authorization=_ACTION,
                          invoke=lambda: count.append(1))
    with pytest.raises(ActionReplayDenied):
        receipts.execute_once(lease, idempotency_key="op-a",
                              authorization=_ACTION,
                              invoke=lambda: count.append(2))
    assert count == [1]


def test_concurrent_dispatch_has_exactly_one_winner(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="once", authorization=_ACTION)
    barrier = threading.Barrier(16)
    def compete(_):
        other = PodActionReceipts(PodReservationLedger(tmp_path / "pods.db"))
        barrier.wait()
        try:
            return other.begin_dispatch(lease, rec.action_id).state
        except ActionReplayDenied:
            return "rejected"
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as workers:
        results = list(workers.map(compete, range(16)))
    assert results.count("dispatching") == 1
    assert results.count("rejected") == 15


def test_crash_before_dispatch_is_still_prepared(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="not-started", authorization=_ACTION)
    restarted = PodActionReceipts(PodReservationLedger(tmp_path / "pods.db"))
    assert restarted.recover_ambiguous() == ()
    assert restarted.get(rec.action_id).state == "prepared"
    assert restarted.begin_dispatch(lease, rec.action_id).state == "dispatching"


def test_crash_after_write_ahead_dispatch_is_uncertain_and_not_replayable(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    action = receipts.prepare(lease, idempotency_key="maybe-sent", authorization=_ACTION)
    receipts.begin_dispatch(lease, action.action_id)
    restarted = PodActionReceipts(PodReservationLedger(tmp_path / "pods.db"))
    assert restarted.recover_ambiguous() == (action.action_id,)
    assert restarted.get(action.action_id).state == "uncertain"
    with pytest.raises(ActionReplayDenied):
        restarted.begin_dispatch(lease, action.action_id)
    with pytest.raises(ActionReplayDenied):
        restarted.execute_once(lease, idempotency_key="maybe-sent",
                               authorization=_ACTION, invoke=lambda: None)


def test_timeout_after_external_effect_is_uncertain_not_reexecuted(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    calls = []
    def fails_after_effect():
        calls.append("externally committed")
        raise TimeoutError("provider says timeout; action may have completed")
    with pytest.raises(TimeoutError):
        receipts.execute_once(lease, idempotency_key="timeout",
                              authorization=_ACTION, invoke=fails_after_effect)
    with pytest.raises(ActionReplayDenied):
        receipts.execute_once(lease, idempotency_key="timeout",
                              authorization=_ACTION, invoke=fails_after_effect)
    assert calls == ["externally committed"]


def test_unapproved_risk_and_unknown_tool_never_invoke_external_callback(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    called = []
    with pytest.raises(AdmissionDenied):
        receipts.execute_once(
            lease, idempotency_key="email",
            authorization={"kind": "tool_call", "tool": "send_email"},
            invoke=lambda: called.append(1),
        )
    assert called == []
    with sqlite3.connect(tmp_path / "pods.db") as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_action_idempotency_collision_with_changed_action_is_denied(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    receipts.prepare(lease, idempotency_key="same", authorization=_ACTION)
    with pytest.raises(ActionReplayDenied):
        receipts.prepare(lease, idempotency_key="same",
                         authorization={"kind": "tool_call", "tool": "read_file"})


def test_stale_parent_or_finished_pod_does_not_prepare_or_dispatch(tmp_path):
    ledger, receipts, parent, lease, _ = setup(tmp_path)
    prepared = receipts.prepare(lease, idempotency_key="will-revoke", authorization=_ACTION)
    ledger.revoke_parent(parent)
    with pytest.raises(AdmissionDenied):
        receipts.begin_dispatch(lease, prepared.action_id)
    with pytest.raises(AdmissionDenied):
        receipts.prepare(lease, idempotency_key="after-revoke", authorization=_ACTION)


def test_cross_pod_or_forged_lease_is_rejected(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path / "a")
    other_ledger, _, _, other_lease, _ = setup(tmp_path / "b")
    assert other_ledger.path != receipts.reservations.path
    prepared = receipts.prepare(lease, idempotency_key="own", authorization=_ACTION)
    with pytest.raises(AdmissionDenied):
        receipts.begin_dispatch(other_lease, prepared.action_id)
    with pytest.raises(AdmissionDenied):
        receipts.begin_dispatch(object(), prepared.action_id)  # type: ignore[arg-type]


def test_ambiguous_reconciliation_requires_exact_registered_supervisor(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="reconcile", authorization=_ACTION)
    receipts.begin_dispatch(lease, rec.action_id)
    receipts.recover_ambiguous()
    foreign = Gateway(FakeEngine(), lambda _: None)
    foreign_session = foreign.open_session(
        run_id="unrelated", principal="attacker", engine="harness",
        allow_child_delegation=True, capabilities=Capabilities()
    )
    with pytest.raises(AdmissionDenied):
        receipts.reconcile(foreign_session, rec.action_id, external_receipt="forged")
    secret = "Bearer super-sensitive human=jane@example.com"
    end = receipts.reconcile(parent, rec.action_id, external_receipt=secret)
    assert end.state == "reconciled"
    assert secret not in str(receipts.audit(rec.action_id))
    with pytest.raises(ActionReplayDenied):
        receipts.reconcile(parent, rec.action_id, external_receipt=secret)


@pytest.mark.parametrize("key", ["", "bad key", "\n", "nonascii-☠", "a" * 161])
def test_reject_malformed_idempotency_keys(tmp_path, key):
    _, receipts, _, lease, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        receipts.prepare(lease, idempotency_key=key, authorization=_ACTION)


def test_receipt_schema_unknown_version_fails_closed(tmp_path):
    _, receipts, _, _, _ = setup(tmp_path)
    with receipts.reservations._connect() as db:
        db.execute("UPDATE pod_action_schema SET version = 999")
    with pytest.raises(RuntimeError, match="unsupported action receipt schema"):
        PodActionReceipts(PodReservationLedger(tmp_path / "pods.db"))


def test_action_attempt_count_never_exceeds_reserved_actions(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    for i in range(4):
        receipts.prepare(lease, idempotency_key=f"op-{i}", authorization=_ACTION)
    # Independent gateway tool-call ceiling is tighter than action count;
    # either boundary must deny before another executable intent is stored.
    with pytest.raises(AdmissionDenied):
        receipts.prepare(lease, idempotency_key="op-5", authorization=_ACTION)
    with receipts.reservations._connect() as db:
        count = db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0]
    assert count == 4
