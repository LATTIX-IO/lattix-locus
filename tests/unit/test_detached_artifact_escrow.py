"""LOCUS-469: durable escrow survives closed child and permits bounded review."""
from __future__ import annotations

import hashlib
from contextlib import closing
import os
import signal
import sys
from pathlib import Path

import pytest

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.gateway import BudgetFigures, Capabilities, RiskClass
from locus_runtime.pod_artifact_escrow import DetachedArtifactEscrow
from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ResourceBudget,
)
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_stage_docker import StageDockerBroker
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.stage_guardian_support import managed_guardian_for_test
from tests.unit.test_stage_docker_launch import (
    ONLY_LINUX_UNPRIVILEGED, mock_host, rig, run_command,
)


def _rig_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                  *, name: str = "output.txt"):
    source, lease, parent, _, stages, broker = rig(tmp_path)
    snapshot_cm = staged_workspace(source, staging_parent=tmp_path)
    snapshot = snapshot_cm.__enter__()
    stage = stages.bind(lease, snapshot)
    mock_host(monkeypatch, snapshot.staged / name)
    outcome = broker.execute(
        lease, stage.stage_id,
        run_command(snapshot, argument="/workspace/" + name),
    )
    assert outcome.exit_code == 0
    escrow = DetachedArtifactEscrow(stages)
    sealed = escrow.seal(
        lease, stage.stage_id, allowed_output_paths=frozenset({name}),
    )
    lease.close(
        total=ResourceBudget(1, 1, 0, 1, 0),
        evidence="detached-verified-terminal:" + sealed.manifest_sha256,
    )
    with pytest.raises(AdmissionDenied):
        lease._ledger.require_active(lease.reservation_id)
    snapshot_cm.__exit__(None, None, None)
    assert not snapshot.staged.exists()
    # A newly constructed process-side authority must work without the
    # expired child's session, stage directory or memory-only Gateway handle.
    disk = PodReservationLedger(tmp_path / "runs.db")
    restored = DetachedArtifactEscrow(
        PodStageBindings(disk, PodActionReceipts(disk)),
    )
    return source, lease, parent, restored, sealed


def _operator(gateway, source: Path, *, principal: str = "alice"):
    return gateway.open_session(
        run_id="detached-artifact-reviewer", principal=principal,
        engine="operator", allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({
                "read_file", "write_file", "approve_artifact_promotion",
            }),
            read_roots=(str(source),), write_roots=(str(source),),
            max_actions=30, max_tool_calls=30, max_risk_class=RiskClass.R3,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=500, duration_used_seconds=0,
                max_duration_seconds=300, cost_used_usd=0, max_cost_usd=0,
            ),
        ),
    )


def _human_approve(operator, source: Path, record) -> None:
    action = operator.action(
        kind="tool_call", tool="approve_artifact_promotion",
        target=str(source / record.path),
        args={
            "escrow_id": record.escrow_id,
            "stage_id": record.stage_id,
            "manifest_sha256": record.manifest_sha256,
            "relative_path": record.path,
            "after_sha256": record.payload_sha256,
        },
    )
    prompted = operator.gateway.authorize(action)
    assert prompted.outcome == "ask"
    operator.gateway.approvals.approve(
        operator.caller.run_id, prompted.fingerprint,
        operator.caller.principal,
    )


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="actual Linux Docker daemon acceptance must be explicitly enabled",
)
def test_real_docker_output_sealed_then_published_only_after_child_closure(
    tmp_path: Path,
) -> None:
    """Real container and supervised guardian; no mock launch and no host bind."""
    source, lease, parent, receipts, stages, _ = rig(tmp_path)
    original = (source / "code.txt").read_bytes()
    assert not (source / "output.txt").exists()
    with staged_workspace(source, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ):
        bound = stages.bind(lease, snapshot)
        broker = StageDockerBroker(
            stages, receipts, image=os.environ["LOCUS_DOCKER_ACCEPTANCE_IMAGE"],
        )
        outcome = broker.execute(
            lease, bound.stage_id,
            run_command(snapshot, argument="/workspace/output.txt", timeout=15),
        )
        assert outcome.receipt.state == "succeeded" and outcome.exit_code == 0
        assert (snapshot.staged / "output.txt").is_file()
        assert (source / "code.txt").read_bytes() == original
        assert not (source / "output.txt").exists()
        first = DetachedArtifactEscrow(stages)
        record = first.seal(
            lease, bound.stage_id,
            allowed_output_paths=frozenset({"output.txt"}),
        )
        lease.close(
            total=ResourceBudget(1, 1, 0, 1, 0),
            evidence="real-docker-terminal-escrow:" + record.manifest_sha256,
        )
    assert not snapshot.staged.exists()
    assert not (source / "output.txt").exists()
    detached = DetachedArtifactEscrow(
        PodStageBindings(
            PodReservationLedger(tmp_path / "runs.db"),
            PodActionReceipts(PodReservationLedger(tmp_path / "runs.db")),
        ),
    )
    reviewer = _operator(parent.gateway, source)
    assert detached.review(reviewer, record.escrow_id).after_sha256 == hashlib.sha256(b"").hexdigest()
    with pytest.raises(AdmissionDenied):
        detached.approve(reviewer, record.escrow_id)
    _human_approve(reviewer, source, record)
    assert detached.approve(reviewer, record.escrow_id).state == "approved"
    assert not (source / "output.txt").exists()
    assert detached.promote(reviewer, record.escrow_id).state == "promoted"
    assert (source / "output.txt").read_bytes() == b""
    assert (source / "code.txt").read_bytes() == original
    assert (source / ".aws" / "secret.txt").read_text() == "private"


@ONLY_LINUX_UNPRIVILEGED
def test_escrow_read_and_human_approval_after_child_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, parent, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    operator = _operator(parent.gateway, source)
    assert sealed.state == "ready"
    folder = tmp_path / ".locus-artifact-escrow" / sealed.escrow_id
    assert (folder / "artifact").read_text() == "agent changed stage"
    assert (folder / "artifact").stat().st_mode & 0o777 == 0o400
    assert folder.stat().st_mode & 0o777 == 0o700
    assert not (source / "output.txt").exists()
    review = escrow.review(operator, sealed.escrow_id)
    assert review.after_sha256 == hashlib.sha256(b"agent changed stage").hexdigest()
    assert review.diff.startswith("diff --git a/output.txt b/output.txt\n")
    assert "+agent changed stage" in review.diff
    assert not review.truncated
    with pytest.raises(AdmissionDenied, match="human approval"):
        escrow.approve(operator, sealed.escrow_id)
    _human_approve(operator, source, sealed)
    approved = escrow.approve(operator, sealed.escrow_id)
    assert approved.state == "approved"
    assert not (source / "output.txt").exists()
    with pytest.raises(AdmissionDenied):
        escrow.approve(operator, sealed.escrow_id)
    assert parent.caller.principal == operator.caller.principal
    assert lease.run_id != operator.caller.run_id


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("attack", [
    "foreign_principal", "source_drift", "source_symlink",
    "tamper_escrow", "swap_inode", "expire_escrow", "revoke_parent",
])
def test_closed_lease_escrow_review_fails_closed_on_attack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    source, lease, parent, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    operator = _operator(
        parent.gateway, source,
        principal="mallory" if attack == "foreign_principal" else "alice",
    )
    payload = tmp_path / ".locus-artifact-escrow" / sealed.escrow_id / "artifact"
    if attack == "source_drift":
        (source / "code.txt").write_text("changed since child closed")
    elif attack == "source_symlink":
        (source / "code.txt").unlink()
        (source / "code.txt").symlink_to(tmp_path / "runs.db")
    elif attack == "tamper_escrow":
        os.chmod(payload, 0o600)
        payload.write_text("substituted bytes")
        os.chmod(payload, 0o400)
    elif attack == "swap_inode":
        replacement = payload.parent / "replacement"
        replacement.write_bytes(payload.read_bytes())
        os.chmod(replacement, 0o400)
        os.replace(replacement, payload)
    elif attack == "expire_escrow":
        with escrow.ledger._connect() as db:
            escrow.ledger._tx(db)
            db.execute(
                "UPDATE pod_artifact_escrow SET expires_at=0 WHERE id=?",
                (sealed.escrow_id,),
            )
    elif attack == "revoke_parent":
        with escrow.ledger._connect() as db:
            escrow.ledger._tx(db)
            db.execute(
                "UPDATE parent_budget SET revoked=1 WHERE run_id=?",
                (sealed.parent_run_id,),
            )
    with pytest.raises(AdmissionDenied):
        escrow.review(operator, sealed.escrow_id)
    assert not (source / "output.txt").exists()


@ONLY_LINUX_UNPRIVILEGED
def test_seal_requires_active_lease_and_disallows_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, parent, _, stages, broker = rig(tmp_path)
    with staged_workspace(source, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        mock_host(monkeypatch, snapshot.staged / "output.txt")
        broker.execute(lease, bound.stage_id, run_command(snapshot))
        escrow = DetachedArtifactEscrow(stages)
        ready = escrow.seal(
            lease, bound.stage_id,
            allowed_output_paths=frozenset({"output.txt"}),
        )
        with pytest.raises(AdmissionDenied, match="already has"):
            escrow.seal(
                lease, bound.stage_id,
                allowed_output_paths=frozenset({"output.txt"}),
            )
        lease.close(
            total=ResourceBudget(1, 1, 0, 1, 0),
            evidence="sealed:" + ready.manifest_sha256,
        )
        with pytest.raises(AdmissionDenied):
            escrow.seal(
                lease, bound.stage_id,
                allowed_output_paths=frozenset({"output.txt"}),
            )


@ONLY_LINUX_UNPRIVILEGED
def test_deleted_stage_cannot_mutate_independent_detached_escrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, parent, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    preview = escrow.review(_operator(parent.gateway, source), sealed.escrow_id)
    assert sealed.path == "output.txt"
    assert preview.after_sha256 == sealed.payload_sha256
    with escrow.ledger._connect() as db:
        record = db.execute(
            "SELECT state,parent_run_id,child_run_id,manifest_json,"
            "payload_dev,payload_ino FROM pod_artifact_escrow WHERE id=?",
            (sealed.escrow_id,),
        ).fetchone()
        assert record["state"] == "ready"
        assert record["parent_run_id"] == sealed.parent_run_id
        assert record["child_run_id"] == sealed.child_run_id
        assert len(record["manifest_json"]) > 200
        assert record["payload_dev"] > 0 and record["payload_ino"] > 0


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("output", ["output.txt", "code.txt"])
def test_delayed_post_lease_human_approval_publishes_exactly_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    source, lease, parent, escrow, sealed = _rig_terminal(
        tmp_path, monkeypatch, name=output,
    )
    assert lease._ledger.get(lease.reservation_id).state == "closed"
    operator = _operator(parent.gateway, source)
    assert escrow.review(operator, sealed.escrow_id).after_sha256 == sealed.payload_sha256
    with pytest.raises(AdmissionDenied, match="expired or replayed"):
        escrow.promote(operator, sealed.escrow_id)
    _human_approve(operator, source, sealed)
    escrow.approve(operator, sealed.escrow_id)
    if output == "code.txt":
        assert (source / output).read_text() == "original"
    else:
        assert not (source / output).exists()
    published = escrow.promote(operator, sealed.escrow_id)
    assert published.state == "promoted"
    assert (source / output).read_text() == "agent changed stage"
    assert (source / ".git" / "secret.txt").read_text() == "private"
    assert escrow.cleanup_promoted(sealed.escrow_id) == "clean"
    with pytest.raises(AdmissionDenied):
        escrow.promote(operator, sealed.escrow_id)


@ONLY_LINUX_UNPRIVILEGED
def test_delayed_publication_rejects_expired_approval_without_source_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, parent, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    operator = _operator(parent.gateway, source)
    _human_approve(operator, source, sealed)
    escrow.approve(operator, sealed.escrow_id)
    with escrow.ledger._connect() as db:
        escrow.ledger._tx(db)
        db.execute(
            "UPDATE pod_artifact_escrow SET approval_expires_at=0 WHERE id=?",
            (sealed.escrow_id,),
        )
    with pytest.raises(AdmissionDenied, match="expired or replayed"):
        escrow.promote(operator, sealed.escrow_id)
    assert not (source / "output.txt").exists()


@ONLY_LINUX_UNPRIVILEGED
def test_sigkill_after_detached_atomic_exchange_rolls_back_exact_original_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from locus_runtime import pod_artifact_escrow as module

    source, _, parent, escrow, sealed = _rig_terminal(
        tmp_path, monkeypatch, name="code.txt",
    )
    original_inode = (source / "code.txt").stat().st_ino
    operator = _operator(parent.gateway, source)
    _human_approve(operator, source, sealed)
    escrow.approve(operator, sealed.escrow_id)
    pid = os.fork()
    if pid == 0:
        actual = module._rename_at

        def kill_at_exchange(origin, old, destination, new, *, flags):
            actual(origin, old, destination, new, flags=flags)
            if flags == 2:
                os.kill(os.getpid(), signal.SIGKILL)

        module._rename_at = kill_at_exchange
        try:
            escrow.promote(operator, sealed.escrow_id)
        except Exception:
            os._exit(91)
        os._exit(92)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert (source / "code.txt").read_text() == "agent changed stage"
    assert escrow.recover_uncertain(sealed.escrow_id) == "rolled_back"
    assert (source / "code.txt").read_text() == "original"
    assert (source / "code.txt").stat().st_ino == original_inode
    with pytest.raises(AdmissionDenied):
        escrow.promote(operator, sealed.escrow_id)


@ONLY_LINUX_UNPRIVILEGED
def test_committed_detached_promotion_cleaned_after_real_supervisor_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, parent, escrow, sealed = _rig_terminal(
        tmp_path, monkeypatch, name="code.txt",
    )
    operator = _operator(parent.gateway, source)
    _human_approve(operator, source, sealed)
    escrow.approve(operator, sealed.escrow_id)
    pid = os.fork()
    if pid == 0:
        transition = escrow._transition

        def kill_at_commit(escrow_id, before, after):
            transition(escrow_id, before, after)
            if after == "promoted":
                os.kill(os.getpid(), signal.SIGKILL)

        escrow._transition = kill_at_commit  # type: ignore[method-assign]
        try:
            escrow.promote(operator, sealed.escrow_id)
        except Exception:
            os._exit(91)
        os._exit(92)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert (source / "code.txt").read_text() == "agent changed stage"
    refreshed = DetachedArtifactEscrow(escrow.stages)
    assert refreshed.cleanup_promoted(sealed.escrow_id) == "clean"
    assert refreshed.cleanup_promoted(sealed.escrow_id) == "clean"
    assert (source / "code.txt").read_text() == "agent changed stage"


@ONLY_LINUX_UNPRIVILEGED
def test_foreign_identical_bytes_never_qualify_for_detached_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, parent, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    operator = _operator(parent.gateway, source)
    _human_approve(operator, source, sealed)
    escrow.approve(operator, sealed.escrow_id)
    escrow.promote(operator, sealed.escrow_id)
    target = source / "output.txt"
    promoted_inode = target.stat().st_ino
    with escrow.ledger._connect() as db:
        escrow.ledger._tx(db)
        db.execute(
            "UPDATE pod_artifact_escrow SET state='promoting' WHERE id=?",
            (sealed.escrow_id,),
        )
    other = source / "foreign.txt"
    other.write_bytes(target.read_bytes())
    os.replace(other, target)
    assert target.stat().st_ino != promoted_inode
    assert escrow.recover_uncertain(sealed.escrow_id) == "uncertain"
    assert target.read_text() == "agent changed stage"
    with pytest.raises(AdmissionDenied):
        escrow.promote(operator, sealed.escrow_id)


@ONLY_LINUX_UNPRIVILEGED
def test_expired_detached_escrow_reclaimed_without_source_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, parent, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    dirpath = tmp_path / ".locus-artifact-escrow" / sealed.escrow_id
    assert dirpath.is_dir()
    with escrow.ledger._connect() as db:
        escrow.ledger._tx(db)
        db.execute(
            "UPDATE pod_artifact_escrow SET expires_at=0 WHERE id=?",
            (sealed.escrow_id,),
        )
    outcomes = dict(escrow.reconcile_pending())
    assert outcomes[sealed.escrow_id] == "clean"
    assert not dirpath.exists()
    assert escrow._row(sealed.escrow_id)["state"] == "expired"
    assert dict(escrow.reconcile_pending())[sealed.escrow_id] == "clean"
    assert not (source / "output.txt").exists()
    with pytest.raises(AdmissionDenied):
        escrow.review(_operator(parent.gateway, source), sealed.escrow_id)


@ONLY_LINUX_UNPRIVILEGED
def test_interrupted_seal_reconciliation_refuses_tampered_escrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, _, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    artifact = (
        tmp_path / ".locus-artifact-escrow" / sealed.escrow_id / "artifact"
    )
    with escrow.ledger._connect() as db:
        escrow.ledger._tx(db)
        db.execute(
            "UPDATE pod_artifact_escrow SET state='sealing',stamp=0 "
            "WHERE id=?", (sealed.escrow_id,),
        )
    os.chmod(artifact, 0o600)
    artifact.write_text("foreign payload")
    os.chmod(artifact, 0o400)
    assert dict(escrow.reconcile_pending())[sealed.escrow_id] == "held-for-review"
    assert artifact.exists()
    assert not (source / "output.txt").exists()


@ONLY_LINUX_UNPRIVILEGED
def test_interrupted_seal_clean_inode_reclaimed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, _, escrow, sealed = _rig_terminal(tmp_path, monkeypatch)
    with escrow.ledger._connect() as db:
        escrow.ledger._tx(db)
        db.execute(
            "UPDATE pod_artifact_escrow SET state='sealing',stamp=0 "
            "WHERE id=?", (sealed.escrow_id,),
        )
    again = DetachedArtifactEscrow(escrow.stages)
    assert dict(again.reconcile_pending())[sealed.escrow_id] == "clean"
    assert again._row(sealed.escrow_id)["state"] == "expired"
    assert not (source / "output.txt").exists()
    assert not (
        tmp_path / ".locus-artifact-escrow" / sealed.escrow_id
    ).exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux escrow only")
def test_private_escrow_directory_rejects_unsafe_mode(
    tmp_path: Path,
) -> None:
    from locus_runtime.pod_artifact_escrow import _path_private
    unsafe = tmp_path / "escrow"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(AdmissionDenied):
        with closing(_path_private(unsafe)):
            pytest.fail("unsafe escrow root unexpectedly passed anchored check")
