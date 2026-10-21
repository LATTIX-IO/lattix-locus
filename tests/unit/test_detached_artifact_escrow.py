"""LOCUS-469: durable escrow survives closed child and permits bounded review."""
from __future__ import annotations

import hashlib
import os
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
from locus_runtime.pod_workspace_staging import staged_workspace
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


@pytest.mark.skipif(sys.platform != "linux", reason="Linux escrow only")
def test_private_escrow_directory_rejects_unsafe_mode(
    tmp_path: Path,
) -> None:
    from locus_runtime.pod_artifact_escrow import _path_private
    unsafe = tmp_path / "escrow"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(AdmissionDenied):
        _path_private(unsafe)
