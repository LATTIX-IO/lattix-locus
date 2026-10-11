"""LOCUS-433 fenced orphan dispatch recovery: real kernel lock and adversarial tests."""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import threading

import pytest

from locus_runtime.action_fencing import action_lock
from locus_runtime.action_receipts import ActionReplayDenied, PodActionReceipts
from locus_runtime.fenced_recovery import FencedActionRecovery
from locus_runtime.gateway import Capabilities, Gateway
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from tests.gateway_support import FakeEngine
from tests.unit.test_action_receipts import _ACTION, setup


def test_completed_guarded_dispatch_no_longer_appears_as_orphan(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    rec, data = receipts.execute_once(
        lease, idempotency_key="complete", authorization=_ACTION,
        invoke=lambda: {"approved": False},
    )
    assert data == {"approved": False}
    recovery = FencedActionRecovery(receipts)
    assert rec.state == "succeeded"
    assert recovery.inspect(parent) == ()
    assert recovery.recover(parent, action_ids=[rec.action_id])[0].verdict == "already_terminal"


def test_live_callback_is_never_reclaimed_or_replayed(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    started, release = threading.Event(), threading.Event()
    state: list[str] = []
    def live_callback():
        started.set()
        assert release.wait(timeout=12)
        state.append("executed")
        return "ok"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            receipts.execute_once, lease, idempotency_key="active",
            authorization=_ACTION, invoke=live_callback,
        )
        try:
            assert started.wait(timeout=12)
            ids = FencedActionRecovery(receipts).inspect(parent)
            assert len(ids) == 1
            actual = FencedActionRecovery(receipts).recover(parent, action_ids=ids)
            assert actual[0].verdict == "active"
            assert receipts.get(ids[0]).state == "dispatching"
        finally:
            release.set()
        finished, _ = future.result(timeout=12)
    assert finished.state == "succeeded"
    assert state == ["executed"]
    assert FencedActionRecovery(receipts).recover(
        parent, action_ids=[finished.action_id])[0].verdict == "already_terminal"


def test_multiple_pods_actions_are_not_globally_serialized(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    barrier = threading.Barrier(2)
    calls: list[int] = []
    def work(n: int) -> int:
        barrier.wait(timeout=12)
        calls.append(n)
        return n

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(receipts.execute_once, lease, idempotency_key="parallel1",
                            authorization=_ACTION, invoke=lambda: work(1))
        second = pool.submit(receipts.execute_once, lease, idempotency_key="parallel2",
                             authorization=_ACTION, invoke=lambda: work(2))
        assert first.result(timeout=12)[0].state == "succeeded"
        assert second.result(timeout=12)[0].state == "succeeded"
    assert sorted(calls) == [1, 2]


def test_proven_crashed_guarded_dispatch_becomes_uncertain_never_replayed(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    r = receipts.prepare(lease, idempotency_key="orphan", authorization=_ACTION)
    # Represent a terminated worker: the protected write-ahead transaction
    # was committed while holding the OS lock, then the process exited without
    # writing the final outcome. No callback is run in this fixture.
    with action_lock(receipts.reservations.path, r.action_id) as acquired:
        assert acquired
        receipts._begin_dispatch(lease, r.action_id, guarded=True)
    controller = FencedActionRecovery(receipts)
    outcome = controller.recover(parent, action_ids=[r.action_id])
    assert outcome[0].verdict == "uncertain"
    assert receipts.get(r.action_id).state == "uncertain"
    assert receipts.audit(r.action_id)[-1][0] == "fenced_uncertain"
    with pytest.raises(ActionReplayDenied):
        receipts.execute_once(
            lease, idempotency_key="orphan", authorization=_ACTION,
            invoke=lambda: pytest.fail("a recovered side effect replayed"),
        )
    assert controller.recover(parent, action_ids=[r.action_id])[0].verdict == "already_terminal"


def test_unfenced_legacy_dispatch_is_never_claimed_as_proven_crash(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="legacy", authorization=_ACTION)
    receipts.begin_dispatch(lease, rec.action_id)
    verdict = FencedActionRecovery(receipts).recover(parent, action_ids=[rec.action_id])[0]
    assert verdict.verdict == "ineligible"
    assert receipts.get(rec.action_id).state == "dispatching"


def test_recovery_is_bound_to_original_supervisor_token_not_claimed_run_id(tmp_path):
    _, receipts, owner, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="foreign", authorization=_ACTION)
    receipts.begin_dispatch(lease, rec.action_id)
    fake_gateway = Gateway(FakeEngine(), lambda _: None)
    intruder = fake_gateway.open_session(
        run_id=owner.caller.run_id, principal=owner.caller.principal,
        engine="trusted-supervisor", allow_child_delegation=True,
        capabilities=Capabilities(max_actions=10),
    )
    controller = FencedActionRecovery(receipts)
    # A forged run ID gets no matching records and cannot recover an
    # explicitly named action owned by the original token.
    assert controller.inspect(intruder) == ()
    with pytest.raises(AdmissionDenied):
        controller.recover(intruder, action_ids=[rec.action_id])
    assert receipts.get(rec.action_id).state == "dispatching"


def test_non_delegating_supervisor_cannot_recover(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="lease", authorization=_ACTION)
    receipts.begin_dispatch(lease, rec.action_id)
    outsider_gateway = Gateway(FakeEngine(), lambda _: None)
    outsider = outsider_gateway.open_session(
        run_id="session-research", principal="alice",
        engine="worker", capabilities=Capabilities(max_actions=10),
    )
    with pytest.raises(AdmissionDenied, match="authenticated"):
        FencedActionRecovery(receipts).recover(outsider, action_ids=[rec.action_id])


def test_cross_tenant_action_id_recovery_is_denied(tmp_path):
    _, receipts, _, lease, _ = setup(tmp_path / "tenant-a")
    _, other_receipts, other_parent, _, _ = setup(tmp_path / "tenant-b")
    rec = receipts.prepare(lease, idempotency_key="foreign", authorization=_ACTION)
    with pytest.raises(AdmissionDenied):
        FencedActionRecovery(other_receipts).recover(other_parent, action_ids=[rec.action_id])


@pytest.mark.parametrize("ids", [
    ["x" * 257], ["bad-id", "bad-id"], ["../malicious"], [""],
])
def test_adversarial_recovery_identifiers_fail_closed(tmp_path, ids):
    _, receipts, parent, _, _ = setup(tmp_path)
    with pytest.raises(AdmissionDenied):
        FencedActionRecovery(receipts).recover(parent, action_ids=ids)


def test_recovery_bounds_and_untrusted_lock_directory(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    controller = FencedActionRecovery(receipts)
    with pytest.raises(ValueError):
        controller.inspect(parent, max_actions=0)
    r = receipts.prepare(lease, idempotency_key="lock", authorization=_ACTION)
    receipts._begin_dispatch(lease, r.action_id, guarded=True)
    private = tmp_path / "pods.db.action-locks"
    private.mkdir(mode=0o700)
    if os.name == "posix":
        private.chmod(0o777)
        try:
            with pytest.raises(AdmissionDenied, match="private"):
                controller.recover(parent, action_ids=[r.action_id])
        finally:
            private.chmod(0o700)


def test_recovery_does_not_persist_raw_worker_or_identity_secret(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="private", authorization=_ACTION)
    with action_lock(receipts.reservations.path, rec.action_id) as owned:
        assert owned
        receipts._begin_dispatch(lease, rec.action_id, guarded=True)
    FencedActionRecovery(receipts).recover(parent, action_ids=[rec.action_id])
    with receipts.reservations._connect() as db:
        notes = str(db.execute("SELECT * FROM pod_action_audit").fetchall())
        serialized = "\n".join(
            str(tuple(row)) for row in db.execute("SELECT * FROM pod_action_receipt")
        )
    assert "Bearer" not in notes
    assert parent.caller.token not in serialized


def test_real_subprocess_lock_prevents_recovery_until_hard_exit(tmp_path):
    _, receipts, parent, lease, _ = setup(tmp_path)
    rec = receipts.prepare(lease, idempotency_key="process-crash", authorization=_ACTION)
    # The parent establishes the persisted fence-guarded dispatch. The child
    # process proves that an independent OS lock holder blocks reconciliation.
    with action_lock(receipts.reservations.path, rec.action_id) as owned:
        assert owned
        receipts._begin_dispatch(lease, rec.action_id, guarded=True)
    path = receipts.reservations.path
    script = """
import sys
from locus_runtime.action_fencing import action_lock
with action_lock(sys.argv[1], sys.argv[2]) as held:
    if not held: sys.exit(91)
    print("LOCKED", flush=True)
    sys.stdin.buffer.read(1)
    sys.exit(99)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, path, rec.action_id],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == b"LOCKED"
        assert FencedActionRecovery(receipts).recover(
            parent, action_ids=[rec.action_id])[0].verdict == "active"
        proc.kill()
        proc.wait(timeout=12)
        assert FencedActionRecovery(receipts).recover(
            parent, action_ids=[rec.action_id])[0].verdict == "uncertain"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=12)
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
