"""Focused tests for inbox folder (group) CRUD + assignment."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

from app.main import app, store

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-frontier-actor": "frontier-admin"}


def test_inbox_group_crud_and_assignment() -> None:
    created = client.post("/inbox/groups", json={"name": "Escalations"}, headers=ADMIN_HEADERS)
    assert created.status_code == 200
    group = created.json()
    group_id = group["id"]
    try:
        assert group["name"] == "Escalations"
        assert group["run_ids"] == []

        listing = client.get("/inbox/groups", headers=ADMIN_HEADERS)
        assert listing.status_code == 200
        assert any(g["id"] == group_id for g in listing.json())

        assigned = client.post(
            f"/inbox/groups/{group_id}", json={"add_run_id": "run-123"}, headers=ADMIN_HEADERS
        )
        assert assigned.status_code == 200
        assert assigned.json()["run_ids"] == ["run-123"]

        # Idempotent add + rename in one call.
        again = client.post(
            f"/inbox/groups/{group_id}",
            json={"add_run_id": "run-123", "name": "Customer escalations"},
            headers=ADMIN_HEADERS,
        )
        assert again.json()["run_ids"] == ["run-123"]
        assert again.json()["name"] == "Customer escalations"

        removed = client.post(
            f"/inbox/groups/{group_id}", json={"remove_run_id": "run-123"}, headers=ADMIN_HEADERS
        )
        assert removed.json()["run_ids"] == []

        deleted = client.delete(f"/inbox/groups/{group_id}", headers=ADMIN_HEADERS)
        assert deleted.status_code == 200
        group_id = None
    finally:
        if group_id:
            store.inbox_groups.pop(group_id, None)


def test_create_group_requires_name() -> None:
    response = client.post("/inbox/groups", json={}, headers=ADMIN_HEADERS)
    assert response.status_code == 400


def test_update_missing_group_is_404() -> None:
    response = client.post(
        "/inbox/groups/does-not-exist", json={"name": "x"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 404


def test_runs_expose_kind_field() -> None:
    response = client.get("/workflow-runs", headers={"x-frontier-actor": "frontier-admin"})
    assert response.status_code == 200
    for run in response.json():
        assert run.get("kind") in {"individual", "agent", "workflow", "chat", "playbook", "task"}


def test_rename_run_updates_title() -> None:
    runs = client.get("/workflow-runs", headers={"x-frontier-actor": "frontier-admin"}).json()
    if not runs:
        return
    run_id = runs[0]["id"]
    original = store.runs[run_id].title
    try:
        response = client.post(
            f"/workflow-runs/{run_id}/rename",
            json={"title": "Renamed by test"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed by test"
        assert store.runs[run_id].title == "Renamed by test"
    finally:
        store.runs[run_id].title = original


def test_rename_run_requires_title() -> None:
    runs = client.get("/workflow-runs", headers={"x-frontier-actor": "frontier-admin"}).json()
    if not runs:
        return
    response = client.post(f"/workflow-runs/{runs[0]['id']}/rename", json={}, headers=ADMIN_HEADERS)
    assert response.status_code == 400
