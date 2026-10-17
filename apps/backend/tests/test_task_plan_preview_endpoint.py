"""LOCUS-461 endpoint admission tests. JWT contexts are stubbed in these tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
os.environ.setdefault("LOCUS_API_BEARER_TOKEN", "unit-test-bearer")

from app import main as main_module  # noqa: E402
from app.task_plan_preview import REGISTRY_SCHEMA  # noqa: E402

client = TestClient(main_module.app)


def project():
    return {
        "tenant_id": "tenant-a", "project_id": "proj-1",
        "allowed_principals": ["user-42"], "area": "professional",
        "classification_floor": "internal",
        "effective_classification": "confidential",
        "capabilities": ["sources.read"], "risk_ceiling": "R2",
        "budget": {
            "actions": 5, "tokens": 2000,
            "wall_seconds": 120, "spend_microunits": 100000,
        },
        "policy_epoch": 7, "revision": 2,
    }


def body():
    return {
        "project_id": "proj-1",
        "goal": "Summarize weekly source records",
        "acceptance_criteria": ["Verify sources"],
        "proposal": {
            "contract_version": "1.0",
            "nodes": [{
                "node_id": "summary", "depends_on": [],
                "capabilities": ["sources.read"], "risk_ceiling": "R1",
                "budget": {
                    "actions": 2, "tokens": 300,
                    "wall_seconds": 60, "spend_microunits": 1200,
                },
                "deliverables": ["source notes"],
                "acceptance_criteria": ["Cited output"],
            }],
        },
    }


@pytest.fixture
def preview_setup(tmp_path, monkeypatch):
    config = tmp_path / "trusted_registry.json"
    config.write_text(json.dumps({
        "schema_version": REGISTRY_SCHEMA, "projects": [project()]
    }), encoding="utf-8")
    monkeypatch.setenv("LOCUS_TASK_PLAN_PREVIEW_ENABLED", "1")
    monkeypatch.setenv("LOCUS_TASK_PLAN_REVIEW_REGISTRY_PATH", str(config))
    original = main_module._enforce_request_authn

    def authenticated(request, *, action, required=None, **kwargs):
        if action != "task.plan.preview":
            return original(request, action=action, required=required, **kwargs)
        assert required is True
        request.state.locus_auth_context = {
            "authenticated": True, "used_bearer_token": True,
            "bearer_auth_kind": "jwt", "principal_type": "user",
            "principal_id": "user-42", "tenant": "tenant-a",
        }
        return "user-42"

    monkeypatch.setattr(main_module, "_enforce_request_authn", authenticated)
    return config


def test_preview_classification_is_read_only_and_neutral():
    from app.request_security import (
        RouteAccessCategory, classify_route_access, classify_shell_proof,
        validate_route_inventory,
    )
    rule = classify_route_access("POST", "/task-plans/preview")
    assert rule is not None and rule.category == RouteAccessCategory.AUTHENTICATED_READ
    validate_route_inventory(main_module.app)
    assert classify_shell_proof("POST", "/task-plans/preview") is not None


def test_success_remains_structural_and_not_executable(preview_setup):
    response = client.post("/task-plans/preview", json=body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requester_id"] == "user-42"
    assert data["classification"] == "confidential"
    assert data["admission"]["execution_admitted"] is False
    assert data["admission"]["gateway_authorization"] == "not_granted"


def test_client_cannot_inject_tenant_or_capabilities(preview_setup):
    for field, value in (("tenant_id", "other"), ("capabilities", ["admin"])):
        response = client.post("/task-plans/preview", json=body() | {field: value})
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "unexpected_request_fields"


def test_unknown_project_and_missing_registry_fail_closed(preview_setup, monkeypatch):
    response = client.post("/task-plans/preview", json=body() | {"project_id": "other"})
    assert response.status_code == 404, response.text
    monkeypatch.delenv("LOCUS_TASK_PLAN_REVIEW_REGISTRY_PATH")
    response = client.post("/task-plans/preview", json=body())
    assert response.status_code == 503, response.text


def test_disabled_preview_never_admits_work(preview_setup, monkeypatch):
    monkeypatch.delenv("LOCUS_TASK_PLAN_PREVIEW_ENABLED")
    response = client.post("/task-plans/preview", json=body())
    assert response.status_code == 503, response.text


def test_duplicate_json_keys_are_refused_at_endpoint(preview_setup):
    request = json.dumps(body()).replace(
        '"project_id": "proj-1"',
        '"project_id": "proj-1", "project_id": "other"',
        1,
    )
    response = client.post(
        "/task-plans/preview", content=request,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "duplicate_json_key"
