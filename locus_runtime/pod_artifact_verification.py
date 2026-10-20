"""LOCUS-468: independent, read-only attestation of StageLease artifact outputs.

Only the trusted parent supervisor calls this code with an authentic PodLease
and PodStageBindings. No pod process, model-generated path, status enum or
plain filesystem diff can grant source write or artifact promotion authority.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.pod_artifact_inspection import ArtifactChange, inspect_staged_artifacts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_stage_bindings import PodStageBindings, StageLease
from locus_runtime.pod_workspace_staging import WorkspaceStagingDenied, staged_workspace

VERIFIER_ID = "locus.supervisor.artifact-verifier.v1"
MANIFEST_VERSION = "locus.artifact-manifest.v1"


@dataclass(frozen=True, slots=True)
class VerifiedArtifactManifest:
    version: str
    verifier_id: str
    stage_id: str
    reservation_id: str
    child_run_id: str
    node_id: str
    plan_digest: str
    graph_fingerprint: str
    engine_id: str
    engine_pins_sha256: str
    capability_sha256: str
    binding_sha256: str
    source_sha256: str
    generation_sha256: str
    receipt_id: str
    receipt_outcome_sha256: str
    docker_launch_sha256: str
    exit_code: int
    allowed_output_paths: tuple[str, ...]
    changes: tuple[ArtifactChange, ...]
    source_files: int
    stage_files: int
    manifest_sha256: str


def _attest_terminal(
    stages: PodStageBindings, lease: PodLease, stage: StageLease,
) -> tuple[str, str, str]:
    """Bind exact completed Docker invocation and sealed stage generation.

    The reservation/child/capability/engine/plan/receipt provenance has
    already been validated by inspect_executable. Recheck terminal evidence
    in a single ledger read so an ambiguous or incomplete action is denied.
    """
    if stage.action_id is None or stage.state != "receipt-linked":
        raise AdmissionDenied("artifact verifier requires a linked Docker action")
    with stages.ledger._connect() as db:
        generation = db.execute(
            "SELECT generation,binding_sha256 FROM pod_stage_exec_generation "
            "WHERE stage_id=?", (stage.stage_id,),
        ).fetchone()
        receipt = db.execute(
            "SELECT reservation_id,child_run_id,state,outcome_digest "
            "FROM pod_action_receipt WHERE id=?", (stage.action_id,),
        ).fetchone()
        launch = db.execute(
            "SELECT action_id,state,exit_code,launch_sha256,stage_proof "
            "FROM pod_stage_docker_launch WHERE stage_id=?", (stage.stage_id,),
        ).fetchone()
    if (
        generation is None
        or generation["binding_sha256"] != stage.binding_sha256
        or receipt is None
        or receipt["reservation_id"] != lease.reservation_id
        or receipt["child_run_id"] != lease.run_id
        or receipt["state"] != "succeeded"
        or not receipt["outcome_digest"]
        or launch is None
        or launch["action_id"] != stage.action_id
        or launch["state"] != "finished"
        or type(launch["exit_code"]) is not int
        or launch["exit_code"] != 0
        or not launch["stage_proof"]
    ):
        raise AdmissionDenied("artifact verifier requires a successful exact Docker receipt")
    return (
        hashlib.sha256(str(generation["generation"]).encode("ascii")).hexdigest(),
        str(receipt["outcome_digest"]),
        str(launch["launch_sha256"]),
    )


def _check_source_snapshot(stage: StageLease) -> None:
    """Use the SAME copier digest as the immutable StageLease, not a new hash."""
    with staged_workspace(
        stage.source_path, staging_parent=Path(stage.staged_path).parent,
    ) as independent:
        if independent.sha256 != stage.source_sha256:
            raise AdmissionDenied("artifact source drifted from bound snapshot")


def verify_staged_artifacts(
    stages: PodStageBindings, lease: PodLease, stage_id: str, *,
    allowed_output_paths: frozenset[str],
) -> VerifiedArtifactManifest:
    """Read-only supervisor verification. NOT approval and NOT publication.

    Validation is repeated around both scans. Stage files remain untrusted
    after the result: a future promotion must re-attest them against the
    manifest and check source preconditions under its own exclusive lock.
    """
    if (
        sys.platform != "linux"
        or not isinstance(stages, PodStageBindings)
        or not isinstance(lease, PodLease)
        or stages.ledger.path != lease._ledger.path
        or stages.receipts.reservations.path != stages.ledger.path
        or not isinstance(stages.receipts, PodActionReceipts)
    ):
        raise AdmissionDenied("trusted local Linux StageLease verifier required")
    stage = stages.inspect_executable(lease, stage_id)
    generation_sha, receipt_sha, launch_sha = _attest_terminal(stages, lease, stage)
    try:
        _check_source_snapshot(stage)
        first = inspect_staged_artifacts(
            stage.source_path, stage.staged_path,
            allowed_output_paths=allowed_output_paths,
        )
        # Reject a concurrent stage change, including same-size replacements
        # and mode-only edits. Do not silently trust an initial enumeration.
        second = inspect_staged_artifacts(
            stage.source_path, stage.staged_path,
            allowed_output_paths=allowed_output_paths,
        )
        _check_source_snapshot(stage)
    except WorkspaceStagingDenied as exc:
        raise AdmissionDenied("stage artifact provenance or scope invalid") from exc
    if first != second:
        raise AdmissionDenied("artifact inventory changed during independent review")
    if stages.inspect_executable(lease, stage_id) != stage:
        raise AdmissionDenied("StageLease changed during artifact verification")
    if _attest_terminal(stages, lease, stage) != (
        generation_sha, receipt_sha, launch_sha,
    ):
        raise AdmissionDenied("terminal receipt changed during artifact verification")
    payload = {
        "version": MANIFEST_VERSION, "verifier_id": VERIFIER_ID,
        "stage_id": stage.stage_id,
        "reservation_id": stage.reservation_id,
        "child_run_id": stage.child_run_id,
        "node_id": stage.node_id,
        "plan_digest": stage.plan_digest,
        "graph_fingerprint": stage.graph_fingerprint,
        "engine_id": stage.engine_id,
        "engine_pins_sha256": stage.engine_pins_sha256,
        "capability_sha256": stage.capability_sha256,
        "binding_sha256": stage.binding_sha256,
        "source_sha256": stage.source_sha256,
        "generation_sha256": generation_sha,
        "receipt_id": stage.action_id,
        "receipt_outcome_sha256": receipt_sha,
        "docker_launch_sha256": launch_sha,
        "exit_code": 0,
        "allowed_output_paths": tuple(sorted(allowed_output_paths)),
        "changes": tuple(first.changes),
        "source_files": first.source_files,
        "stage_files": first.stage_files,
    }
    canonical = json.dumps(
        {**payload, "changes": [asdict(change) for change in first.changes]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return VerifiedArtifactManifest(
        version=MANIFEST_VERSION,
        verifier_id=VERIFIER_ID,
        stage_id=stage.stage_id,
        reservation_id=stage.reservation_id,
        child_run_id=stage.child_run_id,
        node_id=stage.node_id,
        plan_digest=stage.plan_digest,
        graph_fingerprint=stage.graph_fingerprint,
        engine_id=stage.engine_id,
        engine_pins_sha256=stage.engine_pins_sha256,
        capability_sha256=stage.capability_sha256,
        binding_sha256=stage.binding_sha256,
        source_sha256=stage.source_sha256,
        generation_sha256=generation_sha,
        receipt_id=str(stage.action_id),
        receipt_outcome_sha256=receipt_sha,
        docker_launch_sha256=launch_sha,
        exit_code=0,
        allowed_output_paths=tuple(sorted(allowed_output_paths)),
        changes=first.changes,
        source_files=first.source_files,
        stage_files=first.stage_files,
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
    )
