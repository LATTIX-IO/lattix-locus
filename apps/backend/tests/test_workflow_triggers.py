"""Focused tests for webhook workflow triggers (reference-plan Phase D)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import WorkflowDefinition, app, store

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-frontier-actor": "frontier-admin"}


def _seed_published_workflow() -> str:
    workflow_id = str(uuid4())
    store.workflow_definitions[workflow_id] = WorkflowDefinition(
        id=workflow_id,
        name=f"Trigger Flow {workflow_id[:8]}",
        description="Webhook trigger test workflow",
        version=1,
        status="published",
    )
    return workflow_id


def test_trigger_create_list_revoke_lifecycle() -> None:
    workflow_id = _seed_published_workflow()
    created_token = None
    try:
        created = client.post(
            f"/workflow-definitions/{workflow_id}/triggers",
            json={"label": "CI webhook"},
            headers=ADMIN_HEADERS,
        )
        assert created.status_code == 200
        body = created.json()
        created_token = body["token"]
        assert body["webhook_url"] == f"/triggers/webhook/{created_token}"
        assert created_token in store.workflow_triggers

        listing = client.get(f"/workflow-definitions/{workflow_id}/triggers", headers=ADMIN_HEADERS)
        assert listing.status_code == 200
        entries = listing.json()
        assert len(entries) == 1
        # The full token is never echoed back after creation.
        assert created_token not in str(entries)
        assert entries[0]["label"] == "CI webhook"

        revoked = client.delete(f"/triggers/{created_token}", headers=ADMIN_HEADERS)
        assert revoked.status_code == 200
        assert created_token not in store.workflow_triggers
        created_token = None
    finally:
        if created_token:
            store.workflow_triggers.pop(created_token, None)
        store.workflow_definitions.pop(workflow_id, None)


def test_create_trigger_requires_existing_workflow() -> None:
    response = client.post(
        "/workflow-definitions/does-not-exist/triggers", json={}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 404


def test_webhook_fire_unknown_token_is_404() -> None:
    response = client.post("/triggers/webhook/not-a-real-token", json={"prompt": "hi"})
    assert response.status_code == 404


def test_webhook_fire_starts_a_run_without_operator_session() -> None:
    workflow_id = _seed_published_workflow()
    token = None
    run_id = None
    try:
        created = client.post(
            f"/workflow-definitions/{workflow_id}/triggers",
            json={"label": "fire-test"},
            headers=ADMIN_HEADERS,
        )
        token = created.json()["token"]

        # No auth headers at all — the token is the sole credential.
        fired = client.post(f"/triggers/webhook/{token}", json={"prompt": "Triggered task."})
        assert fired.status_code == 200
        body = fired.json()
        run_id = body["id"]
        assert body["status"] in {"started", "blocked", "failed"}
        assert run_id in store.runs

        # Run settles on the bounded executor.
        deadline = time.time() + 15
        while time.time() < deadline and store.runs[run_id].status == "Running":
            time.sleep(0.05)
        assert store.runs[run_id].status != "Running"

        # The run is attributed to the trigger owner and tagged as webhook-sourced.
        fire_events = [
            event for event in store.audit_events if event.action == "workflow.trigger.fire"
        ]
        assert any(event.metadata.get("workflow_id") == workflow_id for event in fire_events)
    finally:
        if token:
            store.workflow_triggers.pop(token, None)
        if run_id:
            store.runs.pop(run_id, None)
            store.run_details.pop(run_id, None)
            store.run_events.pop(run_id, None)
        store.workflow_definitions.pop(workflow_id, None)


def test_webhook_rejects_unpublished_workflow() -> None:
    workflow_id = _seed_published_workflow()
    store.workflow_definitions[workflow_id].status = "draft"
    token = None
    try:
        # Seed a trigger directly (creation endpoint allows any existing workflow).
        token = "manual-test-token-draft"
        store.workflow_triggers[token] = {
            "workflow_id": workflow_id,
            "actor": "frontier-admin",
            "label": "draft",
            "created_at": main_module._now_iso(),
        }
        fired = client.post(f"/triggers/webhook/{token}", json={})
        assert fired.status_code == 409
    finally:
        if token:
            store.workflow_triggers.pop(token, None)
        store.workflow_definitions.pop(workflow_id, None)
