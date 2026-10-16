"""End-to-end negative checks: a human outcome approval must not bypass guardrails."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
os.environ.setdefault("LOCUS_API_BEARER_TOKEN", "unit-test-bearer")

from app.main import app, store, ArtifactSummary, WorkflowRunSummary  # noqa: E402

client = TestClient(app)
AUTH = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}


@pytest.fixture
def run_state(monkeypatch):
    monkeypatch.delenv("LOCUS_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("LOCUS_SECURE_LOCAL_MODE", raising=False)
    monkeypatch.delenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", raising=False)
    ident = "approval-" + str(uuid4())
    artifact_id = "artifact-" + str(uuid4())
    artifact = ArtifactSummary(id=artifact_id, name="Review artifact", status="Needs Review", version=2)
    original_settings = store.platform_settings.model_copy(deep=True)
    store.runs[ident] = WorkflowRunSummary(id=ident, title="review", status="Needs Review", updatedAt="now", progressLabel="Awaiting approval")
    store.run_details[ident] = {
        "status": "Needs Review",
        "artifacts": [artifact.model_dump()],
        "approvals": {"required": True, "pending": True, "artifact_id": artifact_id, "version": 2},
        "access": {"actor": "locus-admin"},
    }
    store.run_events[ident] = []
    store.artifacts.append(artifact)
    try:
        yield ident, artifact_id
    finally:
        store.runs.pop(ident, None)
        store.run_details.pop(ident, None)
        store.run_events.pop(ident, None)
        store.artifacts = [a for a in store.artifacts if a.id != artifact_id]
        store.inbox = [i for i in store.inbox if i.runId != ident]
        store.platform_settings = original_settings


def request(run_id, artifact_id, *, decision="approved", version=2, feedback=None):
    payload = {"run_id": run_id, "artifact_id": artifact_id, "version": version, "decision": decision}
    if feedback is not None:
        payload["feedback"] = feedback
    return client.post("/approvals", json=payload, headers=AUTH)


def test_guardrail_blocked_run_is_not_approvable(run_state):
    run_id, artifact_id = run_state
    store.runs[run_id].status = "Blocked"
    store.run_details[run_id]["status"] = "Blocked"
    result = request(run_id, artifact_id)
    assert result.status_code == 409, result.text
    assert store.runs[run_id].status == "Blocked"
    assert store.run_details[run_id]["approvals"]["pending"] is True
    assert store.run_details[run_id]["artifacts"][0]["status"] == "Needs Review"
    assert not store.run_events[run_id]


@pytest.mark.parametrize("bad", [("other", 2), (None, 3), (None, True)])
def test_forged_artifact_or_stale_revision_is_refused(run_state, bad):
    run_id, artifact_id = run_state
    target_id, version = bad
    result = request(run_id, target_id or artifact_id, version=version)
    assert result.status_code == 409, result.text
    assert store.runs[run_id].status == "Needs Review"


def test_approval_updates_exact_artifact_and_is_not_replayable(run_state):
    run_id, artifact_id = run_state
    accepted = request(run_id, artifact_id)
    assert accepted.status_code == 200, accepted.text
    assert store.runs[run_id].status == "Done"
    assert store.run_details[run_id]["artifacts"][0]["status"] == "Approved"
    assert next(a for a in store.artifacts if a.id == artifact_id).status == "Approved"
    assert request(run_id, artifact_id).status_code == 409
    assert len([e for e in store.run_events[run_id] if e.type == "approval_decision"]) == 1


def test_changes_requested_requires_feedback_and_does_not_complete(run_state):
    run_id, artifact_id = run_state
    assert request(run_id, artifact_id, decision="changes_requested").status_code == 409
    result = request(run_id, artifact_id, decision="changes_requested", feedback="Recheck citations")
    assert result.status_code == 200, result.text
    assert store.runs[run_id].status == "Needs Review"
    assert store.run_details[run_id]["approvals"]["pending"] is True
