"""Fail-closed, side-effect-free gate for human artifact review transitions.

The run and its reviewable artifact must be in the exact pending-review
state; this is not the Gateway's authorization to execute any action.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class InvalidApprovalTransition(ValueError):
    """No run or artifact state may be mutated for this decision."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ApprovalTarget:
    artifact_id: str
    version: int
    decision: str
    feedback: str


def validate_approval_transition(
    run_status: str, details: Mapping[str, Any], request: Mapping[str, Any]
) -> ApprovalTarget:
    """Bind the decision to a pending review and exact artifact revision.

    A policy-blocked, failed, already-completed, or archived run cannot be
    advanced to Done via this endpoint. All checks precede state mutation.
    """
    decision = request.get("decision")
    if decision not in ("approved", "changes_requested"):
        raise InvalidApprovalTransition("unknown_decision")
    if run_status != "Needs Review" or details.get("status") != "Needs Review":
        raise InvalidApprovalTransition("run_not_awaiting_review")
    review = details.get("approvals")
    if not isinstance(review, Mapping) or review.get("required") is not True or review.get("pending") is not True:
        raise InvalidApprovalTransition("no_pending_approval")
    artifact_id = request.get("artifact_id")
    expected_id = review.get("artifact_id")
    version = request.get("version")
    expected_version = review.get("version")
    if not isinstance(artifact_id, str) or not artifact_id.strip() or artifact_id != expected_id:
        raise InvalidApprovalTransition("artifact_mismatch")
    if type(version) is not int or version <= 0 or type(expected_version) is not int or version != expected_version:
        raise InvalidApprovalTransition("revision_mismatch")
    if review.get("last_decision") in ("approved", "changes_requested") and review.get(
        "last_decision_version", expected_version
    ) == version:
        raise InvalidApprovalTransition("revision_already_decided")
    artifacts = details.get("artifacts")
    if not isinstance(artifacts, list) or not any(
        isinstance(a, Mapping)
        and a.get("id") == artifact_id
        and type(a.get("version")) is int
        and a.get("version") == version
        and a.get("status") == "Needs Review"
        for a in artifacts
    ):
        raise InvalidApprovalTransition("artifact_not_reviewable")
    feedback = request.get("feedback")
    if feedback is None:
        feedback = ""
    if not isinstance(feedback, str) or len(feedback) > 2000:
        raise InvalidApprovalTransition("invalid_feedback")
    feedback = feedback.strip()
    if decision == "changes_requested" and not feedback:
        raise InvalidApprovalTransition("feedback_required")
    return ApprovalTarget(artifact_id, version, decision, feedback)
