"""LOCUS-465 A: StageLease transactional and adversarial qualification.

Synthetic, authenticated Gateway children only. NO Docker CLI or real agent
is executed, and artifact promotion is deliberately absent.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import sqlite3

import pytest

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.pod_reservations import AdmissionDenied, ResourceBudget
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.test_task_scheduler import REQUEST, build

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="stage snapshot binding requires anchored POSIX no-follow paths",
)

ENGINES = (
    ("compose", "native/deep-agents"),
    ("publish", "native/deep-agents"),
    ("research", "native/deep-agents"),
    ("triage", "cli/codex"),
)


def rig(tmp_path, *, sealed=True):
    scheduler, ledger, plan, parent = build(tmp_path)
    receipts = PodActionReceipts(ledger, supervisor=parent)
    stage_ledger = PodStageBindings(ledger, receipts)
    if sealed:
        scheduler.bind_engine_pins(plan, parent, ENGINES)
    return scheduler, ledger, plan, parent, receipts, stage_ledger


def child(scheduler, plan, parent, *, sealed=True):
    claim = scheduler.claim_next(
        plan, parent, expected_engine_pins=ENGINES if sealed else None,
    )
    assert claim is not None
    hold = scheduler.reserve(
        plan, parent, claim, budget=REQUEST, lease_seconds=30,
        pod_revision="pod-stage-v1",
    )
    engine = dict(ENGINES)[claim.node_id]
    lease = scheduler.admit(plan, parent, claim, engine=engine)
    assert hold.reservation_id == lease.reservation_id
    return claim, lease


def workspace(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "work.txt").write_text("original")
    return src


def action(receipts, lease, *, key="stage-activity"):
    return receipts.prepare(
        lease, idempotency_key=key,
        authorization={"kind": "tool_call", "tool": "list_issues"},
    )


def test_stage_lease_is_durable_bound_to_engine_and_prepared_receipt(tmp_path):
    scheduler, ledger, plan, parent, receipts, stages = rig(tmp_path)
    claim, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        assert bound.plan_digest == plan.digest
        assert bound.reservation_id == lease.reservation_id
        assert bound.parent_run_id == parent.caller.run_id
        assert bound.child_run_id == lease.run_id
        assert bound.node_id == claim.node_id
        assert bound.engine_id == "native/deep-agents"
        assert bound.pod_revision == "pod-stage-v1"
        assert bound.source_sha256 == snapshot.sha256
        assert bound.state == "bound" and bound.action_id is None
        assert bound.capability_sha256 and bound.binding_sha256
        assert stages.bind(lease, snapshot) == bound
        # Usage counters advance during a pod's normal lifetime. Stage identity
        # is a pin of capability ceilings, not volatile consumed budget.
        lease.settle(ResourceBudget(1, 1, 25, 5, 0))
        assert stages.inspect(lease, bound.stage_id) == bound
        # Same DB, independent wrapper after restart: no new authority granted.
        reopened = PodStageBindings(ledger, receipts)
        assert reopened.inspect(lease, bound.stage_id) == bound
        prepared = action(receipts, lease)
        linked = reopened.link_prepared_receipt(lease, bound.stage_id, prepared.action_id)
        assert linked.state == "receipt-linked" and linked.action_id == prepared.action_id
        assert reopened.link_prepared_receipt(lease, bound.stage_id, prepared.action_id) == linked
        assert stages.audit(lease, bound.stage_id) == ("bound", "receipt-linked")
        retired = reopened.retire(lease, bound.stage_id)
        assert retired.state == "retired"
        assert reopened.retire(lease, bound.stage_id) == retired
        assert stages.audit(lease, bound.stage_id) == (
            "bound", "receipt-linked", "retired",
        )
        with pytest.raises(AdmissionDenied, match="stale, foreign or revoked"):
            stages.inspect(lease, bound.stage_id)
        assert (src / "work.txt").read_text() == "original"
    assert not snapshot.staged.exists()
    with ledger._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_stage_binding").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0] == 1


def test_stage_not_reusable_after_context_cleanup_or_source_replacement(tmp_path):
    scheduler, _, plan, parent, receipts, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
    with pytest.raises(AdmissionDenied, match="stage"):
        stages.inspect(lease, bound.stage_id)
    # A different directory at the same absolute path must not pass attestation.
    snapshot.staged.mkdir(mode=0o700)
    try:
        with pytest.raises(AdmissionDenied, match="workspace identity"):
            stages.inspect(lease, bound.stage_id)
    finally:
        snapshot.staged.rmdir()
    assert (src / "work.txt").read_text() == "original"


def test_stage_requires_sealed_scheduler_and_exact_reservation(tmp_path):
    scheduler, ledger, plan, parent, receipts, stages = rig(tmp_path, sealed=False)
    _, lease = child(scheduler, plan, parent, sealed=False)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        with pytest.raises(AdmissionDenied, match="sealed scheduler"):
            stages.bind(lease, snapshot)
    with ledger._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM pod_stage_binding").fetchone()[0] == 0


def test_foreign_child_cannot_inspect_attach_or_rebind_first_stage(tmp_path):
    scheduler, _, plan, parent, receipts, stages = rig(tmp_path)
    _, first = child(scheduler, plan, parent)
    _, second = child(scheduler, plan, parent)
    assert first.reservation_id != second.reservation_id
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        first_bound = stages.bind(first, snapshot)
        with pytest.raises(AdmissionDenied, match="stale, foreign"):
            stages.inspect(second, first_bound.stage_id)
        with pytest.raises(AdmissionDenied, match="already bound"):
            stages.bind(second, snapshot)
        second_receipt = action(receipts, second, key="other-child")
        with pytest.raises(AdmissionDenied, match="stale, foreign"):
            stages.link_prepared_receipt(second, first_bound.stage_id, second_receipt.action_id)
        with pytest.raises(AdmissionDenied, match="does not belong"):
            stages.link_prepared_receipt(first, first_bound.stage_id, second_receipt.action_id)
        second_source = tmp_path / "second-source"
        second_source.mkdir()
        (second_source / "work.txt").write_text("second")
        with staged_workspace(second_source, staging_parent=tmp_path) as stage2:
            sibling = stages.bind(second, stage2)
            assert sibling.engine_id == "cli/codex"
            assert sibling.capability_sha256 == first_bound.capability_sha256
            assert sibling.stage_id != first_bound.stage_id
            assert sibling.staged_path != first_bound.staged_path


def test_different_stage_cannot_replace_existing_attempt(tmp_path):
    scheduler, _, plan, parent, _, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot1:
        stages.bind(lease, snapshot1)
        with staged_workspace(src, staging_parent=tmp_path) as snapshot2:
            with pytest.raises(AdmissionDenied, match="cannot replace"):
                stages.bind(lease, snapshot2)


def test_stale_engine_and_durable_pin_tamper_fail_closed(tmp_path):
    scheduler, ledger, plan, parent, _, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        with ledger._connect() as db:
            db.execute(
                "UPDATE task_dispatch_node SET pinned_engine_id='cli/codex' "
                "WHERE parent_run_id=? AND node_id='research'",
                (parent.caller.run_id,),
            )
        with pytest.raises(AdmissionDenied, match="engine binding differs"):
            stages.inspect(lease, bound.stage_id)
        with pytest.raises(AdmissionDenied, match="engine binding differs"):
            stages.link_prepared_receipt(lease, bound.stage_id, "forged-receipt")


def test_revoked_or_closed_child_fails_before_receipt_or_stage_reuse(tmp_path):
    scheduler, ledger, plan, parent, receipts, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        # No effect occurred. Closing child must terminate live stage authority.
        lease.close(
            total=ResourceBudget.zero(), evidence="stage binding only; no launch",
        )
        with pytest.raises(AdmissionDenied, match="current admitted"):
            stages.inspect(lease, bound.stage_id)
        with pytest.raises(AdmissionDenied, match="current admitted"):
            stages.link_prepared_receipt(lease, bound.stage_id, "invalid")
    assert ledger.get(lease.reservation_id).state == "closed"


def test_prepared_receipt_is_one_use_and_cannot_rebind_other_action(tmp_path):
    scheduler, _, plan, parent, receipts, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        first = action(receipts, lease, key="first")
        stages.link_prepared_receipt(lease, stage.stage_id, first.action_id)
        second = action(receipts, lease, key="second")
        with pytest.raises(AdmissionDenied, match="different prepared receipt"):
            stages.link_prepared_receipt(lease, stage.stage_id, second.action_id)
        with pytest.raises(AdmissionDenied, match="does not belong"):
            stages.link_prepared_receipt(lease, stage.stage_id, "unknown")
        assert stages.inspect(lease, stage.stage_id).action_id == first.action_id


def test_already_dispatched_action_cannot_be_claimed_as_prepared(tmp_path):
    scheduler, _, plan, parent, receipts, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        first = action(receipts, lease)
        receipts.begin_dispatch(lease, first.action_id)
        with pytest.raises(AdmissionDenied, match="does not belong"):
            stages.link_prepared_receipt(lease, stage.stage_id, first.action_id)
        assert stages.inspect(lease, stage.stage_id).state == "bound"


def test_stage_snapshot_path_forgery_denied(tmp_path):
    scheduler, _, plan, parent, _, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        forged = dataclasses.replace(snapshot, staged=src)
        with pytest.raises(AdmissionDenied):
            stages.bind(lease, forged)
        fake = dataclasses.replace(snapshot, sha256="no-checksum")
        with pytest.raises(AdmissionDenied, match="snapshot metadata"):
            stages.bind(lease, fake)
        symlink = tmp_path / (snapshot.staged.name + "-symlink")
        symlink.symlink_to(snapshot.staged, target_is_directory=True)
        try:
            forged = dataclasses.replace(snapshot, staged=symlink)
            with pytest.raises(AdmissionDenied, match="stage path"):
                stages.bind(lease, forged)
        finally:
            symlink.unlink()
        assert (src / "work.txt").read_text() == "original"


def test_concurrent_stage_rebinding_only_one_attempt_wins(tmp_path):
    scheduler, ledger, plan, parent, _, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as left:
        with staged_workspace(src, staging_parent=tmp_path) as right:
            def attempt(snapshot):
                try:
                    return stages.bind(lease, snapshot)
                except AdmissionDenied:
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(attempt, (left, right)))
            assert sum(value is not None for value in outcomes) == 1
            with ledger._connect() as db:
                assert db.execute(
                    "SELECT COUNT(*) FROM pod_stage_binding WHERE reservation_id=?",
                    (lease.reservation_id,),
                ).fetchone()[0] == 1


def test_stage_schema_rejects_unknown_version_without_migration(tmp_path):
    scheduler, ledger, _, parent, receipts, _ = rig(tmp_path)
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE pod_stage_schema SET version=9")
    with pytest.raises(RuntimeError, match="unsupported stage-lease schema"):
        PodStageBindings(ledger, receipts)



def test_staging_inode_reuse_at_new_path_does_not_blacklist_future_pod(
    tmp_path, monkeypatch,
):
    from locus_runtime import pod_stage_bindings as module

    scheduler, _, plan, parent, _, stages = rig(tmp_path)
    _, first = child(scheduler, plan, parent)
    _, second = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as first_snapshot:
        old = stages.bind(first, first_snapshot)
        old_stage_path = old.staged_path
    assert not os.path.exists(old_stage_path)

    # Filesystems may immediately recycle device/inode after rmtree. Force
    # that identity for a distinct newly created stage while its old path
    # stays absent; a past record must not prevent normal future workloads.
    with staged_workspace(src, staging_parent=tmp_path) as second_snapshot:
        with stages.ledger._connect() as db:
            old_row = db.execute(
                "SELECT source_dev,source_ino,stage_dev,stage_ino "
                "FROM pod_stage_binding WHERE id=?", (old.stage_id,),
            ).fetchone()
        real_identity = module._snapshot_identity

        def recycled_source_stage(source, stage):
            orig_source, orig_stage = real_identity(source, stage)
            if stage == second_snapshot.staged:
                return (
                    orig_source,
                    (old_row["stage_dev"], old_row["stage_ino"],
                     orig_stage[2], orig_stage[3]),
                )
            return orig_source, orig_stage

        monkeypatch.setattr(module, "_snapshot_identity", recycled_source_stage)
        next_stage = stages.bind(second, second_snapshot)
        assert next_stage.stage_id != old.stage_id
        assert next_stage.staged_path != old.staged_path
        assert stages.inspect(second, next_stage.stage_id) == next_stage



def test_durable_graph_fingerprint_drift_is_not_an_engine_authority(
    tmp_path,
):
    scheduler, ledger, plan, parent, _, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        with ledger._connect() as db:
            db.execute(
                "UPDATE task_dispatch_node SET graph_fingerprint=? "
                "WHERE parent_run_id=? AND node_id='research'",
                ("b" * 64, parent.caller.run_id),
            )
        with pytest.raises(AdmissionDenied, match="engine binding differs"):
            stages.inspect(lease, bound.stage_id)


def test_linked_receipt_substitution_is_detected_on_inspect(tmp_path):
    scheduler, ledger, plan, parent, receipts, stages = rig(tmp_path)
    _, lease = child(scheduler, plan, parent)
    _, sibling = child(scheduler, plan, parent)
    src = workspace(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        receipt = action(receipts, lease)
        linked = stages.link_prepared_receipt(lease, bound.stage_id, receipt.action_id)
        assert linked.state == "receipt-linked"
        # A valid receipt FK owned by a different live sibling is still
        # unauthorized for this StageLease and must fail relational attestation.
        stolen = action(receipts, sibling, key="sibling-action")
        with ledger._connect() as db:
            db.execute(
                "UPDATE pod_stage_binding SET action_id=? WHERE id=?",
                (stolen.action_id, bound.stage_id),
            )
        with pytest.raises(AdmissionDenied, match="receipt linkage was substituted"):
            stages.inspect(lease, bound.stage_id)
