"""LOCUS-468 artifact verifier and untrusted-stage boundary tests."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from locus_runtime.pod_artifact_verification import (
    MANIFEST_VERSION, VERIFIER_ID, verify_staged_artifacts,
)
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.test_stage_docker_launch import (
    ONLY_LINUX_UNPRIVILEGED, mock_host, rig, run_command,
)


@ONLY_LINUX_UNPRIVILEGED
def test_verified_manifest_is_scoped_to_exact_receipt_and_original_is_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lease, _, receipts, stages, broker = rig(tmp_path)
    original = (source / "code.txt").read_bytes()
    with staged_workspace(source, staging_parent=tmp_path) as snapshot:
        bound = stages.bind(lease, snapshot)
        mock_host(monkeypatch, snapshot.staged / "output.txt")
        outcome = broker.execute(lease, bound.stage_id, run_command(snapshot))
        assert outcome.exit_code == 0
        verified = verify_staged_artifacts(
            stages, lease, bound.stage_id,
            allowed_output_paths=frozenset({"output.txt"}),
        )
        assert verified.version == MANIFEST_VERSION
        assert verified.verifier_id == VERIFIER_ID
        assert verified.receipt_id == outcome.receipt.action_id
        assert verified.source_sha256 == snapshot.sha256
        assert verified.binding_sha256 == bound.binding_sha256
        assert verified.engine_id == "cli/test"
        assert len(verified.manifest_sha256) == 64
        assert verified.manifest_sha256 == verify_staged_artifacts(
            stages, lease, bound.stage_id,
            allowed_output_paths=frozenset({"output.txt"}),
        ).manifest_sha256
        assert [change.kind for change in verified.changes] == ["added"]
        assert verified.changes[0].after_sha256 == hashlib.sha256(
            b"agent changed stage"
        ).hexdigest()
        assert (source / "code.txt").read_bytes() == original
        assert not (source / "output.txt").exists()
        assert receipts.get(outcome.receipt.action_id).state == "succeeded"
        with pytest.raises(AdmissionDenied):
            verify_staged_artifacts(
                stages, lease, bound.stage_id,
                allowed_output_paths=frozenset({"code.txt"}),
            )
        # The inspected stage is never promoted, even implicitly.
        assert not (source / "output.txt").exists()


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("attack", ["source_drift", "receipt_uncertain", "launch_nonzero",
                                    "stage_mutation", "scope", "foreign_stage"])
def test_verifier_rejects_stale_or_untrusted_artifact_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    source, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(source, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        mock_host(monkeypatch, snapshot.staged / "output.txt")
        outcome = broker.execute(lease, stage.stage_id, run_command(snapshot))
        scope = frozenset({"output.txt"})
        claimed_id = stage.stage_id
        if attack == "source_drift":
            (source / "code.txt").write_text("changed by an independent operator")
        elif attack == "receipt_uncertain":
            with receipts.reservations._connect() as db:
                receipts.reservations._tx(db)
                db.execute(
                    "UPDATE pod_action_receipt SET state='uncertain' WHERE id=?",
                    (outcome.receipt.action_id,),
                )
        elif attack == "launch_nonzero":
            with receipts.reservations._connect() as db:
                receipts.reservations._tx(db)
                db.execute(
                    "UPDATE pod_stage_docker_launch SET exit_code=7 WHERE stage_id=?",
                    (stage.stage_id,),
                )
        elif attack == "stage_mutation":
            (snapshot.staged / ".aws").mkdir()
        elif attack == "scope":
            scope = frozenset({"unrelated.txt"})
        else:
            claimed_id = "foreign-stage-attempt"
        with pytest.raises(AdmissionDenied):
            verify_staged_artifacts(
                stages, lease, claimed_id, allowed_output_paths=scope,
            )
        assert not (source / "output.txt").exists()


@ONLY_LINUX_UNPRIVILEGED
def test_verifier_requires_a_terminal_action_not_just_a_bound_stage(
    tmp_path: Path,
) -> None:
    source, lease, _, _, stages, _ = rig(tmp_path)
    with staged_workspace(source, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        with pytest.raises(AdmissionDenied):
            verify_staged_artifacts(
                stages, lease, stage.stage_id,
                allowed_output_paths=frozenset(),
            )


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="anchored POSIX artifact scanner required",
)
def test_mode_only_change_and_unbounded_entries_are_not_invisible(
    tmp_path: Path,
) -> None:
    from locus_runtime.pod_artifact_inspection import inspect_staged_artifacts
    from locus_runtime.pod_workspace_staging import WorkspaceStagingDenied

    src = tmp_path / "source"
    src.mkdir()
    (src / "code.txt").write_text("same")
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        os.chmod(snapshot.staged / "code.txt", 0o644)
        result = inspect_staged_artifacts(
            src, snapshot.staged,
            allowed_output_paths=frozenset({"code.txt"}),
        )
        assert len(result.changes) == 1
        assert result.changes[0].after_mode == 0o644
        (snapshot.staged / "other").write_bytes(b"new")
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(
                src, snapshot.staged,
                allowed_output_paths=frozenset({"code.txt"}),
            )
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        for index in range(5):
            (snapshot.staged / f"output-{index}").write_text("a")
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(
                src, snapshot.staged,
                allowed_output_paths=frozenset(f"output-{i}" for i in range(5)),
                max_files=2,
            )
