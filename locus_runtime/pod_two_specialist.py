"""LOCUS-472: supervisor-controlled real native Locus -> Codex CLI vertical slice.

There is one durable parent TaskPlan with exactly two ordered nodes. The first
child is the genuine Locus Deep Agents runtime; the second is official Codex
CLI in Linux stage-only Docker. Both are independent engine-pinned child
attempts with separate budgets, bindings and signed Gateway authorization.

The only inter-specialist data is a bounded, independently verified artifact
from detached escrow. The final output remains unapproved until an independent
operator review + one-use Gateway approval; this module never creates consent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from locus_runtime.gateway import GatewaySession
from locus_runtime.harness.codex_cli_worker import (
    CodexCliWorker, ENGINE_ID as CODEX_ENGINE,
)
from locus_runtime.harness.native_draft_worker import (
    NATIVE_ENGINE_ID, NativeLocusDraftWorker,
)
from locus_runtime.pod_artifact_escrow import (
    DetachedArtifactEscrow, EscrowPreview, EscrowRecord,
)
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, ResourceBudget
from locus_runtime.pod_specialist_handoff import (
    VerifiedSpecialistHandoff, codex_review_prompt, prepare_verified_handoff,
)
from locus_runtime.pod_stage_docker import StageDockerBroker
from locus_runtime.pod_workspace_staging import staged_workspace
from locus_runtime.task_plan_compiler import CompiledTaskPlan
from locus_runtime.task_scheduler import DurableTaskScheduler

_PINS = (("codex", CODEX_ENGINE), ("native", NATIVE_ENGINE_ID))


class _IndependentEscrowVerifier:
    """Re-attest sealed bytes and lineage from disk, not from worker claims."""

    def __init__(self, escrow: DetachedArtifactEscrow, records: dict[str, EscrowRecord]):
        self.escrow = escrow
        self.records = records

    def verify(
        self, *, parent_run_id: str, plan_digest: str,
        node_id: str, child_run_id: str, artifact_digest: str,
    ) -> bool:
        record = self.records.get(node_id)
        if (
            record is None or record.parent_run_id != parent_run_id
            or record.child_run_id != child_run_id
            or record.manifest_sha256 != artifact_digest
        ):
            return False
        row = self.escrow._row(record.escrow_id)
        self.escrow._inspect(row, eligible=("ready",))
        if row["parent_run_id"] != parent_run_id or row["child_run_id"] != child_run_id:
            return False
        import json
        manifest = json.loads(str(row["manifest_json"]))
        return manifest.get("plan_digest") == plan_digest


@dataclass(frozen=True, slots=True)
class TwoSpecialistPendingReview:
    """Terminal evidence with neither child able to approve/promote the result."""

    native_escrow: EscrowRecord
    revised_escrow: EscrowRecord
    handoff_fingerprint: str
    native_model_calls: int
    codex_model_calls: int
    native_action_id: str
    codex_action_id: str
    parent_run_id: str
    plan_digest: str

    def review(
        self, escrow: DetachedArtifactEscrow, operator: GatewaySession,
    ) -> EscrowPreview:
        """Read-only review after both children have closed."""
        if not isinstance(escrow, DetachedArtifactEscrow):
            raise AdmissionDenied("trusted detached escrow service required")
        row = escrow._row(self.revised_escrow.escrow_id)
        if (
            row["parent_run_id"] != self.parent_run_id
            or row["manifest_sha256"] != self.revised_escrow.manifest_sha256
        ):
            raise AdmissionDenied("publication identity no longer matches accepted result")
        return escrow.review(operator, self.revised_escrow.escrow_id)

    def publish(
        self, escrow: DetachedArtifactEscrow, operator: GatewaySession,
    ) -> EscrowRecord:
        """Requires separately issued, one-use human Gateway action approval.

        This method cannot create approval or bypass the source-revision CAS.
        It delegates both checks to the existing detached escrow authority.
        """
        self.review(escrow, operator)
        escrow.approve(operator, self.revised_escrow.escrow_id)
        return escrow.promote(operator, self.revised_escrow.escrow_id)


def _active_used(lease: PodLease) -> ResourceBudget:
    return lease._ledger.require_active(lease.reservation_id).used


def execute_two_specialists(
    plan: CompiledTaskPlan,
    parent: GatewaySession,
    scheduler: DurableTaskScheduler,
    broker: StageDockerBroker,
    *, source: Path,
    staging_parent: Path,
    task: str,
    rubric: str,
    model: str = "llama3.2:1b",
) -> TwoSpecialistPendingReview:
    """Run one version-pinned native -> Codex task; leave output *unpublished*.

    Requires a previously accepted and bound durable TaskPlan. No alternative
    engine, unapproved provider, unscoped filesystem or fallback is admitted.
    Failures leave uncertain attempts for the existing guardian/reconciler.
    """
    if (
        not isinstance(parent, GatewaySession)
        or not isinstance(plan, CompiledTaskPlan)
        or not isinstance(scheduler, DurableTaskScheduler)
        or not isinstance(broker, StageDockerBroker)
        or scheduler.ledger.path != broker.receipts.reservations.path
        or broker.stages.ledger.path != scheduler.ledger.path
        or {node.node_id for node in plan.plan.nodes} != {"native", "codex"}
        or any(
            node.depends_on != (("native",) if node.node_id == "codex" else ())
            for node in plan.plan.nodes
        )
        or plan.plan.intent.requester_id != parent.caller.principal
    ):
        raise AdmissionDenied("exact durable two-specialist task plan required")
    records: dict[str, EscrowRecord] = {}
    escrow = DetachedArtifactEscrow(broker.stages)
    native_claim = scheduler.claim_next(plan, parent, expected_engine_pins=_PINS)
    if native_claim is None or native_claim.node_id != "native":
        raise AdmissionDenied("native specialist cannot be freshly claimed")
    scheduler.reserve(
        plan, parent, native_claim,
        budget=ResourceBudget(14, 14, 30000, 60, 0),
        lease_seconds=100, pod_revision="locus-native-brief-v1",
    )
    native_lease = scheduler.admit(
        plan, parent, native_claim, engine=NATIVE_ENGINE_ID,
    )
    with staged_workspace(source, staging_parent=staging_parent) as snap:
        native_stage = broker.stages.bind(native_lease, snap)
        produced = NativeLocusDraftWorker(broker, model=model).run(
            native_lease, native_stage.stage_id, task=task,
        )
        records["native"] = produced.escrow
        native_lease.close(
            total=_active_used(native_lease),
            evidence=produced.escrow.manifest_sha256,
        )
    scheduler.verify_closed(
        plan, parent, native_claim, artifact_digest=produced.escrow.manifest_sha256,
        verifier=_IndependentEscrowVerifier(escrow, records),
    )
    codex_claim = scheduler.claim_next(plan, parent, expected_engine_pins=_PINS)
    if codex_claim is None or codex_claim.node_id != "codex":
        raise AdmissionDenied("Codex specialist dependency not independently verified")
    scheduler.reserve(
        plan, parent, codex_claim,
        budget=ResourceBudget(18, 18, 35000, 64, 0),
        lease_seconds=100, pod_revision="codex-stdio-v1",
    )
    codex_lease = scheduler.admit(plan, parent, codex_claim, engine=CODEX_ENGINE)
    with staged_workspace(source, staging_parent=staging_parent) as snap:
        codex_stage = broker.stages.bind(codex_lease, snap)
        handoff: VerifiedSpecialistHandoff = prepare_verified_handoff(
            escrow, broker.stages, source=produced.escrow,
            recipient_lease=codex_lease, recipient_stage_id=codex_stage.stage_id,
        )
        revised = CodexCliWorker(broker, model=model).run(
            codex_lease, codex_stage.stage_id,
            prompt=codex_review_prompt(handoff, rubric=rubric),
            output_name="answer.txt",
            idempotency_key="codex-review-" + handoff.fingerprint,
            timeout_seconds=58,
        )
        records["codex"] = escrow.seal(
            codex_lease, codex_stage.stage_id,
            allowed_output_paths=frozenset({"answer.txt"}),
        )
        if records["codex"].payload_sha256 != revised.output_sha256:
            raise AdmissionDenied("independent Codex artifact differs from CLI claim")
        codex_lease.close(
            total=_active_used(codex_lease),
            evidence=records["codex"].manifest_sha256,
        )
    scheduler.verify_closed(
        plan, parent, codex_claim, artifact_digest=records["codex"].manifest_sha256,
        verifier=_IndependentEscrowVerifier(escrow, records),
    )
    return TwoSpecialistPendingReview(
        native_escrow=records["native"],
        revised_escrow=records["codex"],
        handoff_fingerprint=handoff.fingerprint,
        native_model_calls=produced.model_calls,
        codex_model_calls=revised.model_calls,
        native_action_id=produced.action_id,
        codex_action_id=revised.action_id,
        parent_run_id=parent.caller.run_id,
        plan_digest=plan.digest,
    )
