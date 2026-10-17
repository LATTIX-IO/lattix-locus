"""Negative lifecycle cases for the human artifact approval contract."""
from __future__ import annotations

import pytest
from locus_runtime.run_approval_contract import InvalidApprovalTransition, validate_approval_transition


def pending():
    return {
        "status": "Needs Review",
        "approvals": {"required": True, "pending": True, "artifact_id": "art-1", "version": 2},
        "artifacts": [{"id": "art-1", "version": 2, "status": "Needs Review"}],
    }


def approved():
    return {"decision": "approved", "artifact_id": "art-1", "version": 2}


def test_exact_pending_artifact_revision_is_eligible():
    result = validate_approval_transition("Needs Review", pending(), approved())
    assert result.artifact_id == "art-1"
    assert result.version == 2
    assert result.decision == "approved"


@pytest.mark.parametrize("run_status", ["Blocked", "Running", "Done", "Failed", "Archived", "Canceled"])
def test_policy_blocked_and_terminal_runs_cannot_be_marked_approved(run_status):
    with pytest.raises(InvalidApprovalTransition, match="run_not_awaiting_review"):
        validate_approval_transition(run_status, pending(), approved())


def test_detail_status_mismatch_fails_closed():
    details = pending()
    details["status"] = "Blocked"
    with pytest.raises(InvalidApprovalTransition, match="run_not_awaiting_review"):
        validate_approval_transition("Needs Review", details, approved())


@pytest.mark.parametrize("state", [None, {}, {"required": False, "pending": True}, {"required": True, "pending": False}])
def test_no_pending_approval_refuses_decision(state):
    details = pending()
    details["approvals"] = state
    with pytest.raises(InvalidApprovalTransition, match="no_pending_approval"):
        validate_approval_transition("Needs Review", details, approved())


@pytest.mark.parametrize("override,code", [
    ({"artifact_id": "wrong"}, "artifact_mismatch"),
    ({"artifact_id": ""}, "artifact_mismatch"),
    ({"version": 3}, "revision_mismatch"),
    ({"version": True}, "revision_mismatch"),
    ({"decision": "rejected"}, "unknown_decision"),
    ({"decision": "changes_requested"}, "feedback_required"),
    ({"feedback": "x" * 2001}, "invalid_feedback"),
])
def test_tampering_and_invalid_decisions_refuse(override, code):
    payload = approved() | override
    with pytest.raises(InvalidApprovalTransition) as err:
        validate_approval_transition("Needs Review", pending(), payload)
    assert err.value.code == code


def test_artifact_must_still_be_reviewable_not_just_mentioned_by_approval():
    for artifacts in ([], [{"id": "art-1", "version": 2, "status": "Blocked"}], [{"id": "art-1", "version": 1, "status": "Needs Review"}]):
        details = pending()
        details["artifacts"] = artifacts
        with pytest.raises(InvalidApprovalTransition, match="artifact_not_reviewable"):
            validate_approval_transition("Needs Review", details, approved())


def test_decided_revision_cannot_be_reopened_by_toggling_pending():
    details = pending()
    details["approvals"].update({
        "last_decision": "changes_requested",
        "last_decision_version": 2,
    })
    with pytest.raises(InvalidApprovalTransition, match="revision_already_decided"):
        validate_approval_transition("Needs Review", details, approved())
    details["approvals"]["version"] = 3
    details["artifacts"][0]["version"] = 3
    assert validate_approval_transition(
        "Needs Review", details, {**approved(), "version": 3}
    ).version == 3


def test_request_changes_is_narrowing_but_requires_feedback_and_exact_revision():
    target = validate_approval_transition("Needs Review", pending(), approved() | {"decision": "changes_requested", "feedback": "  Fix citations  "})
    assert target.feedback == "Fix citations"
