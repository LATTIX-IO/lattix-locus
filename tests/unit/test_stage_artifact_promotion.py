"""Host-only, one-use Gateway-human-approved single-file promotion acceptance."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from locus_runtime.gateway import BudgetFigures, Capabilities, RiskClass
from locus_runtime.pod_artifact_promotion import StageArtifactPromoter
from locus_runtime.pod_artifact_verification import verify_staged_artifacts
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.test_stage_docker_launch import (
    ONLY_LINUX_UNPRIVILEGED, mock_host, rig, run_command,
)


def _operator(lease, source: Path):
    return lease._session.gateway.open_session(
        run_id="artifact-human-reviewer", principal="alice", engine="operator",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"approve_artifact_promotion", "write_file"}),
            write_roots=(str(source),), read_roots=(str(source),),
            max_actions=12, max_tool_calls=12, max_risk_class=RiskClass.R3,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=500, duration_used_seconds=0,
                max_duration_seconds=60, cost_used_usd=0, max_cost_usd=0,
            ),
        ),
    )


def _human_approves(operator, source: Path, verified) -> None:
    change = verified.changes[0]
    facts = {
        "stage_id": verified.stage_id,
        "manifest_sha256": verified.manifest_sha256,
        "relative_path": change.path,
        "after_sha256": change.after_sha256,
    }
    action = operator.action(
        kind="tool_call", tool="approve_artifact_promotion",
        target=str(source / change.path), args=facts,
    )
    asked = operator.gateway.authorize(action)
    assert asked.outcome == "ask"
    operator.gateway.approvals.approve(
        operator.caller.run_id, asked.fingerprint, "alice",
    )


def _stage_change(tmp_path, monkeypatch, output: str = "output.txt"):
    source, lease, _, receipts, stages, broker = rig(tmp_path)
    context = staged_workspace(source, staging_parent=tmp_path)
    snapshot = context.__enter__()
    stage = stages.bind(lease, snapshot)
    mock_host(monkeypatch, snapshot.staged / output)
    result = broker.execute(
        lease, stage.stage_id,
        run_command(snapshot, argument="/workspace/" + output),
    )
    assert result.exit_code == 0
    verified = verify_staged_artifacts(
        stages, lease, stage.stage_id,
        allowed_output_paths=frozenset({output}),
    )
    return source, lease, stages, snapshot, context, verified


@ONLY_LINUX_UNPRIVILEGED
def test_single_file_promotion_requires_one_human_approval_and_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, stages, _, context, manifest = _stage_change(tmp_path, monkeypatch)
    try:
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        assert not (source / "output.txt").exists()
        with pytest.raises(AdmissionDenied, match="human-approved"):
            promoter.approve(operator, lease, manifest)
        assert not (source / "output.txt").exists()
        _human_approves(operator, source, manifest)
        consent = promoter.approve(operator, lease, manifest, ttl_seconds=120)
        assert consent.state == "approved"
        assert not (source / "output.txt").exists()
        published = promoter.promote(
            operator, lease, manifest, approval_id=consent.approval_id,
        )
        assert published.state == "promoted"
        assert (source / "output.txt").read_text() == "agent changed stage"
        assert (source / "code.txt").read_text() == "original"
        with pytest.raises(AdmissionDenied, match="replayed"):
            promoter.promote(operator, lease, manifest, approval_id=consent.approval_id)
        assert (source / "output.txt").read_text() == "agent changed stage"
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_unexpected_source_change_refuses_approved_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, stages, _, context, manifest = _stage_change(tmp_path, monkeypatch)
    try:
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        _human_approves(operator, source, manifest)
        approval = promoter.approve(operator, lease, manifest)
        (source / "code.txt").write_text("independently modified")
        with pytest.raises(AdmissionDenied):
            promoter.promote(
                operator, lease, manifest, approval_id=approval.approval_id,
            )
        assert not (source / "output.txt").exists()
        assert (source / "code.txt").read_text() == "independently modified"
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("attack", ["symlink", "hardlink", "special_mode", "outside_scope"])
def test_promotion_rejects_untrusted_stage_or_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    source, lease, stages, snapshot, context, manifest = _stage_change(
        tmp_path, monkeypatch,
    )
    try:
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        if attack == "symlink":
            (snapshot.staged / "output.txt").unlink()
            (snapshot.staged / "output.txt").symlink_to(source / "code.txt")
        elif attack == "hardlink":
            (snapshot.staged / "output.txt").unlink()
            os.link(source / "code.txt", snapshot.staged / "output.txt")
        elif attack == "special_mode":
            (snapshot.staged / "output.txt").chmod(0o4600)
        else:
            (snapshot.staged / "other.txt").write_text("unexpected")
        with pytest.raises(AdmissionDenied):
            promoter.approve(operator, lease, manifest)
        assert not (source / "output.txt").exists()
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_restart_reconciles_interrupted_promotion_without_replaying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, stages, _, context, manifest = _stage_change(tmp_path, monkeypatch)
    try:
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        _human_approves(operator, source, manifest)
        approval = promoter.approve(operator, lease, manifest)
        promoter.promote(operator, lease, manifest, approval_id=approval.approval_id)
        assert (source / "output.txt").exists()
        # Represent a process killed after atomic rename but before the
        # ledger's last COMMIT. Recreate the supervisor using only SQLite.
        with stages.ledger._connect() as db:
            stages.ledger._tx(db)
            db.execute(
                "UPDATE pod_artifact_promotion SET state='promoting' WHERE id=?",
                (approval.approval_id,),
            )
        restarted = StageArtifactPromoter(stages)
        assert restarted.recover_uncertain(approval.approval_id) == "rolled_back"
        assert not (source / "output.txt").exists()
        with pytest.raises(AdmissionDenied):
            restarted.promote(
                operator, lease, manifest, approval_id=approval.approval_id,
            )
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_atomic_new_file_publish_refuses_racing_operator_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from locus_runtime import pod_artifact_promotion as module

    source, lease, stages, _, context, manifest = _stage_change(tmp_path, monkeypatch)
    try:
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        _human_approves(operator, source, manifest)
        approval = promoter.approve(operator, lease, manifest)
        kernel_rename = module._publish_noreplace

        def concurrent_create(fd: int, temporary: str, name: str) -> None:
            (source / name).write_text("third-party data")
            kernel_rename(fd, temporary, name)

        monkeypatch.setattr(module, "_publish_noreplace", concurrent_create)
        with pytest.raises(AdmissionDenied, match="concurrent source file"):
            promoter.promote(operator, lease, manifest, approval_id=approval.approval_id)
        assert (source / "output.txt").read_text() == "third-party data"
        assert StageArtifactPromoter(stages).recover_uncertain(
            approval.approval_id,
        ) == "uncertain"
        with pytest.raises(AdmissionDenied):
            promoter.promote(operator, lease, manifest, approval_id=approval.approval_id)
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_existing_file_rollback_preserves_previous_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, stages, _, context, manifest = _stage_change(
        tmp_path, monkeypatch, "code.txt",
    )
    try:
        assert (source / "code.txt").read_text() == "original"
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        _human_approves(operator, source, manifest)
        approval = promoter.approve(operator, lease, manifest)
        promoter.promote(operator, lease, manifest, approval_id=approval.approval_id)
        assert (source / "code.txt").read_text() == "agent changed stage"
        with stages.ledger._connect() as db:
            stages.ledger._tx(db)
            db.execute(
                "UPDATE pod_artifact_promotion SET state='promoting' WHERE id=?",
                (approval.approval_id,),
            )
        assert StageArtifactPromoter(stages).recover_uncertain(
            approval.approval_id,
        ) == "rolled_back"
        assert (source / "code.txt").read_text() == "original"
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_real_sigkill_after_atomic_swap_is_rolled_back_by_new_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kill an actual Linux process after rename but before SQLite outcome."""
    from locus_runtime import pod_artifact_promotion as module

    source, lease, stages, _, context, manifest = _stage_change(
        tmp_path, monkeypatch, "code.txt",
    )
    try:
        promoter = StageArtifactPromoter(stages)
        operator = _operator(lease, source)
        _human_approves(operator, source, manifest)
        consent = promoter.approve(operator, lease, manifest)
        pid = os.fork()
        if pid == 0:
            actual_replace = module.os.replace

            def die_after_swap(*args, **kwargs):
                actual_replace(*args, **kwargs)
                os.kill(os.getpid(), signal.SIGKILL)

            module.os.replace = die_after_swap
            try:
                promoter.promote(
                    operator, lease, manifest,
                    approval_id=consent.approval_id,
                )
            except BaseException:
                os._exit(91)
            os._exit(92)
        _, status = os.waitpid(pid, 0)
        assert os.WIFSIGNALED(status), f"promotion child exited unexpectedly: {status}"
        assert os.WTERMSIG(status) == signal.SIGKILL
        assert (source / "code.txt").read_text() == "agent changed stage"
        recovered = StageArtifactPromoter(stages).recover_uncertain(consent.approval_id)
        assert recovered == "rolled_back"
        assert (source / "code.txt").read_text() == "original"
        with pytest.raises(AdmissionDenied):
            promoter.promote(
                operator, lease, manifest, approval_id=consent.approval_id,
            )
    finally:
        context.__exit__(None, None, None)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux supervisor only")
def test_no_approval_without_trusted_manifest(tmp_path: Path) -> None:
    source, lease, _, _, stages, _ = rig(tmp_path)
    promoter = StageArtifactPromoter(stages)
    operator = _operator(lease, source)
    with pytest.raises(AdmissionDenied):
        promoter.approve(operator, lease, object())  # type: ignore[arg-type]
