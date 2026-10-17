from __future__ import annotations
from dataclasses import dataclass
from types import SimpleNamespace
import pytest
from app.run_approvals import decide_run_approval
from locus_runtime.run_approval_contract import InvalidApprovalTransition


@dataclass
class Run:
    status: str = "Needs Review"
    progressLabel: str = "Awaiting approval"
    updatedAt: str = "now"


@dataclass
class Artifact:
    id: str = "art-1"
    version: int = 2
    status: str = "Needs Review"


def state(status="Needs Review"):
    run = Run(status)
    details = {"status": status, "approvals": {"required": True, "pending": True, "artifact_id": "art-1", "version": 2}, "artifacts": [{"id": "art-1", "version": 2, "status": "Needs Review"}]}
    store = SimpleNamespace(runs={"run-1": run}, run_details={"run-1": details}, artifacts=[Artifact()], inbox=[SimpleNamespace(runId="run-1")])
    return store


def test_approval_changes_both_artifact_and_run_and_clears_inbox():
    store = state()
    result = decide_run_approval(store, "run-1", {"decision": "approved", "artifact_id": "art-1", "version": 2}, actor="user")
    assert store.runs["run-1"].status == "Done"
    assert store.run_details["run-1"]["artifacts"][0]["status"] == "Approved"
    assert store.artifacts[0].status == "Approved"
    assert store.inbox == []
    assert result["metadata"]["version"] == 2
    with pytest.raises(InvalidApprovalTransition, match="run_not_awaiting_review"):
        decide_run_approval(store, "run-1", {"decision": "approved", "artifact_id": "art-1", "version": 2}, actor="user")


def test_guardrail_refusal_cannot_be_overridden():
    store = state("Blocked")
    payload = {"decision": "approved", "artifact_id": "art-1", "version": 2}
    with pytest.raises(InvalidApprovalTransition, match="run_not_awaiting_review"):
        decide_run_approval(store, "run-1", payload, actor="user")
    assert store.runs["run-1"].status == "Blocked"
    assert store.artifacts[0].status == "Needs Review"


def test_changes_requested_consumes_revision_without_completing():
    store = state()
    result = decide_run_approval(store, "run-1", {"decision": "changes_requested", "artifact_id": "art-1", "version": 2, "feedback": "need source"}, actor="user")
    assert store.runs["run-1"].status == "Needs Review"
    assert store.run_details["run-1"]["approvals"]["pending"] is False
    assert store.run_details["run-1"]["approvals"]["last_decision_version"] == 2
    assert store.artifacts[0].status == "Draft"
    assert store.inbox == []
    assert "need source" in result["summary"]
    with pytest.raises(InvalidApprovalTransition, match="no_pending_approval"):
        decide_run_approval(store, "run-1", {"decision": "approved", "artifact_id": "art-1", "version": 2}, actor="user")
    # A new worker-produced revision may be reviewed, but the previous one
    # cannot be reactivated by merely toggling pending to true.
    store.run_details["run-1"]["approvals"]["pending"] = True
    store.run_details["run-1"]["artifacts"][0]["status"] = "Needs Review"
    with pytest.raises(InvalidApprovalTransition, match="revision_already_decided"):
        decide_run_approval(store, "run-1", {"decision": "approved", "artifact_id": "art-1", "version": 2}, actor="user")
    store.run_details["run-1"]["approvals"]["version"] = 3
    store.run_details["run-1"]["artifacts"][0]["version"] = 3
    store.artifacts[0].version = 3
    store.artifacts[0].status = "Needs Review"
    decide_run_approval(store, "run-1", {"decision": "approved", "artifact_id": "art-1", "version": 3}, actor="user")
    assert store.runs["run-1"].status == "Done"


def test_malformed_secondary_artifact_cannot_partially_advance_run():
    store = state()
    store.run_details["run-1"]["artifacts"].insert(0, {"status": "Draft"})
    store.run_details["run-1"]["artifacts"].append(None)
    decide_run_approval(store, "run-1", {"decision": "approved", "artifact_id": "art-1", "version": 2}, actor="user")
    assert store.run_details["run-1"]["artifacts"][0] == {"status": "Draft"}
    assert store.run_details["run-1"]["artifacts"][1]["status"] == "Approved"
    assert store.run_details["run-1"]["artifacts"][2] is None


def test_backfill_only_unambiguous_legacy_compiled_handback():
    from app.run_approvals import bind_legacy_compiled_reviews

    store = state()
    review = store.run_details["run-1"]["approvals"]
    review.pop("artifact_id")
    review.pop("version")
    store.run_details["run-1"]["artifacts"][0]["name"] = "Completed Feature — Team Handback"
    assert bind_legacy_compiled_reviews(store) == ["run-1"]
    assert review["artifact_id"] == "art-1" and review["version"] == 2
    assert bind_legacy_compiled_reviews(store) == []
    review.pop("artifact_id")
    review.pop("version")
    store.run_details["run-1"]["artifacts"].append({
        "id": "another", "name": "Completed Feature — Team Handback",
        "version": 1, "status": "Needs Review"
    })
    assert bind_legacy_compiled_reviews(store) == []
    assert "artifact_id" not in review

    store = state()
    review = store.run_details["run-1"]["approvals"]
    review.pop("artifact_id")
    review.pop("version")
    store.run_details["run-1"]["artifacts"][0]["name"] = "Unrelated output"
    assert bind_legacy_compiled_reviews(store) == []
