"""Exact-review decisions and conservative legacy binding for compiled handbacks.

No task execution grant is created here. Approval mutations must be serialized
by the caller. The live backend remains single-process only until a storage CAS
and worker-coordinated transition protocol is implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from locus_runtime.run_approval_contract import (
    InvalidApprovalTransition,
    validate_approval_transition,
)


def bind_legacy_compiled_reviews(store: Any) -> list[str]:
    """Restore absent exact artifact bindings only for unambiguous handbacks.

    Earlier compiler runs stored `required/pending` but not the selected
    artifact id/revision. Do not silently select the first artifact or widen
    ambiguous and unrelated requests. Called during persisted-state startup,
    before the next state snapshot is saved.
    """
    migrated: list[str] = []
    for run_id, details in store.run_details.items():
        if not isinstance(details, dict) or details.get("status") != "Needs Review":
            continue
        run = store.runs.get(run_id)
        if run is None or run.status != "Needs Review":
            continue
        review = details.get("approvals")
        if (
            not isinstance(review, dict)
            or review.get("required") is not True
            or review.get("pending") is not True
            or "artifact_id" in review
            or "version" in review
            or review.get("last_decision") is not None
        ):
            continue
        artifacts = details.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        candidates = [
            item for item in artifacts
            if isinstance(item, Mapping)
            and item.get("name") == "Completed Feature — Team Handback"
            and item.get("status") == "Needs Review"
            and isinstance(item.get("id"), str)
            and item["id"].strip()
            and type(item.get("version")) is int
            and item["version"] > 0
        ]
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        # Only migrate a handback also found in the already-owned durable
        # artifact summary. Unrelated or malformed reviews fail closed.
        if not any(
            getattr(artifact, "id", None) == candidate["id"]
            and getattr(artifact, "version", None) == candidate["version"]
            and getattr(artifact, "status", None) == "Needs Review"
            for artifact in store.artifacts
        ):
            continue
        review["artifact_id"] = candidate["id"]
        review["version"] = candidate["version"]
        migrated.append(run_id)
    return migrated


def decide_run_approval(
    store: Any, run_id: str, payload: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    """Validate and mutate one exact outcome revision; caller persists/audits.

    A guardrail-blocked run cannot be marked Done. Both accepted outcomes
    consume their revision; requesting changes requires a freshly issued
    artifact version before any subsequent decision.
    """
    run = store.runs.get(run_id)
    details = store.run_details.get(run_id)
    if run is None or not isinstance(details, dict):
        raise InvalidApprovalTransition("missing_run_details")
    target = validate_approval_transition(run.status, details, payload)
    review = details["approvals"]
    if not isinstance(review, dict):
        raise InvalidApprovalTransition("invalid_approval_record")

    artifacts = details["artifacts"]
    matches = [
        artifact for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("id") == target.artifact_id
        and artifact.get("version") == target.version
        and artifact.get("status") == "Needs Review"
    ]
    if not matches:
        raise InvalidApprovalTransition("artifact_not_reviewable")
    matching_summaries = [
        artifact for artifact in store.artifacts
        if getattr(artifact, "id", None) == target.artifact_id
        and getattr(artifact, "version", None) == target.version
    ]
    # All potentially malformed entries have been skipped before mutation.
    review["pending"] = False
    review["last_decision"] = target.decision
    review["last_decision_version"] = target.version
    review["last_decided_by"] = actor
    run.updatedAt = "just now"
    if target.decision == "approved":
        run.status = "Done"
        run.progressLabel = "Complete"
        details["status"] = "Done"
        artifact_status = "Approved"
        summary = "Run outcome accepted by operator."
    else:
        # The existing version has been rejected: no second approval or
        # immediate replay. A later worker must issue a NEW version.
        run.progressLabel = "Awaiting revised artifact"
        artifact_status = "Draft"
        summary = "Operator requested changes before outcome acceptance."
    for artifact in matches:
        artifact["status"] = artifact_status
    for artifact in matching_summaries:
        artifact.status = artifact_status
    store.inbox = [
        item for item in store.inbox
        if getattr(item, "runId", None) != run_id
    ]
    details["approvals"] = review
    if target.feedback:
        summary = f"{summary} Feedback: {target.feedback[:500]}"
    return {
        "summary": summary,
        "metadata": {
            "decision": target.decision,
            "artifact_id": target.artifact_id,
            "version": target.version,
            "actor": actor,
        },
    }
