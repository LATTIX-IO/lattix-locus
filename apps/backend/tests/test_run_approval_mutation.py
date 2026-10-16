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


def test_changes_requested_keeps_pending_and_artifact_unapproved():
    store = state()
    result = decide_run_approval(store, "run-1", {"decision": "changes_requested", "artifact_id": "art-1", "version": 2, "feedback": "need source"}, actor="user")
    assert store.runs["run-1"].status == "Needs Review"
    assert store.run_details["run-1"]["approvals"]["pending"] is True
    assert store.artifacts[0].status == "Needs Review"
    assert "need source" in result["summary"]
