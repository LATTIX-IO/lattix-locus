"""LOCUS-461: client cannot choose identity, tenant, classification or grants."""
from __future__ import annotations

import json

import pytest

from app.task_plan_preview import (
    REGISTRY_SCHEMA, PlanPreviewUnavailable,
    build_authenticated_preview, load_project_registry, parse_preview_request,
)


def _project():
    return {
        "tenant_id": "tenant-a",
        "project_id": "proj-1",
        "allowed_principals": ["user-42"],
        "area": "professional",
        "classification_floor": "internal",
        "effective_classification": "confidential",
        "capabilities": ["sources.read"],
        "risk_ceiling": "R2",
        "budget": {
            "actions": 5, "tokens": 3000,
            "wall_seconds": 300, "spend_microunits": 100000,
        },
        "policy_epoch": 7,
        "revision": 2,
    }


def _registry(tmp_path, projects=None):
    filename = tmp_path / "context.json"
    filename.write_text(json.dumps({
        "schema_version": REGISTRY_SCHEMA,
        "projects": [_project()] if projects is None else projects,
    }), encoding="utf-8")
    return str(filename)


def _identity(actor="user-42", tenant="tenant-a", kind="jwt"):
    return {
        "authenticated": True, "used_bearer_token": True,
        "bearer_auth_kind": kind, "principal_type": "user",
        "principal_id": actor, "tenant": tenant,
    }


def _request():
    return {
        "project_id": "proj-1",
        "goal": "Prepare a sourced weekly report",
        "acceptance_criteria": ["Sources independently checked"],
        "proposal": {
            "contract_version": "1.0",
            "nodes": [{
                "node_id": "research",
                "depends_on": [],
                "capabilities": ["sources.read"],
                "risk_ceiling": "R1",
                "budget": {
                    "actions": 2, "tokens": 1000,
                    "wall_seconds": 180, "spend_microunits": 40000,
                },
                "deliverables": ["Cited source digest"],
                "acceptance_criteria": ["Every finding has a citation"],
            }],
        },
    }


def _preview(tmp_path, *, request=None, identity=None, project=None, catalog=None, policy=None):
    return build_authenticated_preview(
        json.dumps(_request() if request is None else request).encode(),
        auth_context=_identity() if identity is None else identity,
        registry_path=_registry(tmp_path, [_project() if project is None else project]),
        catalog_versions={"agent-1": (3, "published")} if catalog is None else catalog,
        policy_snapshot={"rulesets": [("R1", 2)]} if policy is None else policy,
    )


def test_authoritative_projection_marks_every_gate_unverified(tmp_path):
    view = _preview(tmp_path)
    assert view["requester_id"] == "user-42"
    assert view["project_namespace"] == "tenants/tenant-a/projects/proj-1"
    assert view["classification"] == "confidential"
    assert view["parent_envelope"]["requested_capabilities"] == ["sources.read"]
    assert view["admission"]["execution_admitted"] is False
    assert view["admission"]["gateway_authorization"] == "not_granted"
    assert view["admission"]["policy_evaluation"] == "not_performed"
    assert view["nodes"][0]["policy_decision"] == "not_evaluated"


def test_project_or_policy_or_catalog_changes_break_review_binding(tmp_path):
    first = _preview(tmp_path)["review_digest"]
    assert first == _preview(tmp_path)["review_digest"]
    new_context = _project() | {"revision": 3}
    assert first != _preview(tmp_path, project=new_context)["review_digest"]
    assert first != _preview(tmp_path, policy={"rulesets": [("R1", 3)]})["review_digest"]
    assert first != _preview(tmp_path, catalog={"agent-1": (4, "published")})["review_digest"]


@pytest.mark.parametrize("identity", [
    {},
    {"authenticated": True, "used_bearer_token": True, "bearer_auth_kind": "static", "principal_type": "user", "principal_id": "user-42", "tenant": "tenant-a"},
    _identity(actor="other"),
    _identity(tenant="tenant-b"),
    _identity(kind="none"),
    _identity() | {"principal_type": "agent"},
])
def test_forged_or_nonmember_principal_is_never_authorized(tmp_path, identity):
    with pytest.raises(PlanPreviewUnavailable) as err:
        _preview(tmp_path, identity=identity)
    assert err.value.status_code in (403, 404)


@pytest.mark.parametrize("extra", [
    {"actor": "admin"}, {"tenant_id": "other"},
    {"classification": "public"}, {"risk_ceiling": "R3"},
    {"capabilities": ["secrets.read"]}, {"budget": {"actions": 9000}},
    {"policy_digest": "f" * 64},
])
def test_client_authority_forgery_fails_closed(tmp_path, extra):
    with pytest.raises(PlanPreviewUnavailable, match="unexpected_request_fields"):
        _preview(tmp_path, request=_request() | extra)


def test_duplicate_root_or_nested_keys_fail_before_compiler(tmp_path):
    raw = json.dumps(_request()).replace('"project_id": "proj-1"', '"project_id": "proj-1", "project_id": "other"', 1)
    with pytest.raises(PlanPreviewUnavailable, match="duplicate_json_key"):
        parse_preview_request(raw.encode())
    raw = json.dumps(_request()).replace('"actions": 2', '"actions": 2, "actions": 9000', 1)
    with pytest.raises(PlanPreviewUnavailable, match="duplicate_json_key"):
        parse_preview_request(raw.encode())


def test_missing_registry_and_ambiguous_rows_fail_closed(tmp_path):
    payload = json.dumps(_request()).encode()
    with pytest.raises(PlanPreviewUnavailable, match="project_registry_unavailable"):
        build_authenticated_preview(
            payload, auth_context=_identity(), registry_path="",
            catalog_versions={}, policy_snapshot={},
        )
    with pytest.raises(PlanPreviewUnavailable, match="duplicate_server_project"):
        load_project_registry(_registry(tmp_path, [_project(), _project()]))


def test_config_symlinks_denied(tmp_path):
    path = _registry(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(PlanPreviewUnavailable, match="project_registry_unavailable"):
        load_project_registry(str(alias))


def test_invalid_server_budget_and_classification_rejected(tmp_path):
    with pytest.raises(PlanPreviewUnavailable):
        _preview(tmp_path, project=_project() | {"budget": {"actions": True, "tokens": 200, "wall_seconds": 20, "spend_microunits": 0}})
    with pytest.raises(PlanPreviewUnavailable, match="invalid_plan_or_context"):
        _preview(tmp_path, project=_project() | {"classification_floor": "restricted"})


def test_graph_cycles_and_requested_budget_escalation_denied(tmp_path):
    proposal = _request()
    proposal["proposal"]["nodes"][0]["depends_on"] = ["research"]
    with pytest.raises(PlanPreviewUnavailable, match="invalid_plan_or_context"):
        _preview(tmp_path, request=proposal)
    proposal = _request()
    proposal["proposal"]["nodes"][0]["budget"]["actions"] = 99
    with pytest.raises(PlanPreviewUnavailable, match="invalid_plan_or_context"):
        _preview(tmp_path, request=proposal)


def test_local_bootstrap_is_explicit_tenant_and_identity(tmp_path):
    local = _project() | {
        "tenant_id": "local", "allowed_principals": ["user:local/operator"],
    }
    result = _preview(
        tmp_path, project=local,
        identity=_identity(
            actor="user:local/operator", tenant="", kind="bootstrap"
        )
    )
    assert result["tenant_id"] == "local"
    assert result["requester_id"] == "user:local/operator"
