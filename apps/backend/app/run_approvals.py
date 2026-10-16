"""Atomic-in-caller run outcome review transition (not a tool/grant approval)."""
from __future__ import annotations

from typing import Any

from locus_runtime.run_approval_contract import (
    InvalidApprovalTransition,
    validate_approval_transition,
)


def decide_run_approval(store: Any, run_id: str, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Validate before mutation; caller holds the shared review lock and persists.

    The operation may accept one exact pending deliverable. It cannot turn a
    guardrail-blocked run into a successful one or issue any Gateway grants.
    """
    run = store.runs.get(run_id)
    details = store.run_details.get(run_id)
    if run is None or not isinstance(details, dict):
        raise InvalidApprovalTransition("missing_run_details")
    target = validate_approval_transition(run.status, details, payload)
    approvals = details["approvals"]
    if target.decision == "approved":
        approvals["pending"] = False
        run.status = "Done"
        run.progressLabel = "Complete"
        details["status"] = "Done"
        for artifact in details["artifacts"]:
            if artifact["id"] == target.artifact_id and artifact["version"] == target.version:
                artifact["status"] = "Approved"
        for artifact in store.artifacts:
            if artifact.id == target.artifact_id and artifact.version == target.version:
                artifact.status = "Approved"
        store.inbox = [item for item in store.inbox if item.runId != run_id]
    else:
        # Requesting changes does not complete the task or widen authority.
        run.progressLabel = "Awaiting updates"
    run.updatedAt = "just now"
    approvals["last_decision"] = target.decision
    approvals["last_decided_by"] = actor
    details["approvals"] = approvals
    detail = "Run approved by operator." if target.decision == "approved" else "Operator requested changes before approval."
    if target.feedback:
        detail = f"{detail} Feedback: {target.feedback[:500]}"
    return {
        "summary": detail,
        "metadata": {
            "decision": target.decision,
            "artifact_id": target.artifact_id,
            "version": target.version,
            "actor": actor,
        },
    }
