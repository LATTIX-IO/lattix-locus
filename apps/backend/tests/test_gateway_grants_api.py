"""LOCUS-334: approving a gateway ask with a scope mints Biscuit grants; list and revoke them."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app import policy_gateway
from app.main import app, store
from locus_runtime import grants as gr
from locus_runtime.gateway import Gateway, NoGrants
from tests.gateway_support import FakeEngine, installed

client = TestClient(app)
HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "alice"}
BOB = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "bob"}


@pytest.fixture()
def grants_gateway(request: pytest.FixtureRequest) -> Gateway:
    saved = (store.capability_grants, store.revoked_grant_ids)
    store.capability_grants, store.revoked_grant_ids = {}, []
    verifier = gr.BiscuitGrantVerifier(gr.GrantAuthority.generate(), main_module._GRANT_STORE)
    gateway = Gateway(FakeEngine(), main_module._gateway_audit_sink, grants=verifier)
    ctx = installed(gateway)
    ctx.__enter__()

    def restore() -> None:
        ctx.__exit__(None, None, None)
        store.capability_grants, store.revoked_grant_ids = saved

    request.addfinalizer(restore)
    return gateway


@pytest.fixture()
def native_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_native(**kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs.get("tool_id")))
        return {"ok": True}

    monkeypatch.setattr(main_module, "_execute_native_tool_call", fake_native)
    monkeypatch.setattr(main_module, "_admit_graph_tool_policy", lambda **_: None)
    return calls


def _node(tool_id: str = "publish_release") -> Any:
    return main_module.GraphNode(
        id="tool-1",
        type="locus/tool-call",
        title="Tool",
        config={"tool_id": tool_id, "endpoint_url": "http://localhost:9101/x", "method": "POST"},
    )


def _start_run(run_id: str, principal: str = "alice") -> dict[str, Any]:
    store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id, title="grants", status="Running", updatedAt="now", progressLabel="running"
    )
    store.run_details[run_id] = {"access": {"actor": principal}}
    return {
        "run_id": run_id,
        "gateway_session": main_module._open_tool_node_session(run_id, principal, [_node()]),
    }


def _run(state: dict[str, Any], tool_id: str = "publish_release") -> str:
    result = main_module._execute_node(
        node=_node(tool_id),
        incoming=[{"message": "hello"}],
        incoming_by_port={"request": [{"message": "hello"}]},
        run_input={"message": "hello"},
        execution_state=state,
        mem_store={},
    )
    return str(result["status"]["state"])


def _pending(run_id: str) -> dict[str, Any]:
    escalations = store.run_details[run_id]["escalations"]
    return next(e for e in escalations if e.get("kind") == "gateway" and e["status"] == "pending")


def _approve(run_id: str, body: dict[str, Any], headers: dict[str, str] = HEADERS) -> Any:
    escalation = _pending(run_id)
    return client.post(
        f"/workflow-runs/{run_id}/escalations/{escalation['id']}/approve",
        json=body,
        headers=headers,
    )


def test_ask_escalation_carries_the_tightest_pattern(grants_gateway, native_calls) -> None:
    state = _start_run("run-grant-pattern")
    assert _run(state) == "approval_required"
    escalation = _pending("run-grant-pattern")
    pattern = gr.GrantPattern.from_dict(escalation["grant_pattern"])
    assert (pattern.kind, pattern.tool, pattern.target) == (
        "tool_call",
        "publish_release",
        "localhost",
    )
    assert escalation["grant_scopes"] == ["once", "run", "standing"]


def test_standing_approval_mints_a_reusable_grant_and_revoke_ends_it(
    grants_gateway, native_calls
) -> None:
    state = _start_run("run-grant-standing")
    assert _run(state) == "approval_required"
    response = _approve("run-grant-standing", {"scope": "standing"})
    assert response.status_code == 200, response.text
    escalation = response.json()["escalation"]
    grant_id = escalation["grant_id"]
    assert escalation["approval_scope"] == "standing"
    record = main_module._GRANT_STORE.get(grant_id)
    assert record is not None and record.principal == "alice" and record.scope == "standing"
    assert record.expires_at is not None and not record.pinned

    # Not single-use: repeated runs of the covered action go through, in other runs too.
    assert _run(state) == "completed"
    assert _run(state) == "completed"
    assert _run(_start_run("run-grant-standing-2")) == "completed"
    assert native_calls == ["publish_release"] * 3

    listing = client.get("/gateway/grants", headers=HEADERS)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["grants_enabled"] is True
    [view] = [g for g in body["grants"] if g["grant_id"] == grant_id]
    assert "token" not in view and view["status"] == "active"

    # Another principal neither sees nor revokes it.
    assert all(
        g["grant_id"] != grant_id
        for g in client.get("/gateway/grants", headers=BOB).json()["grants"]
    )
    assert client.post(f"/gateway/grants/{grant_id}/revoke", headers=BOB).status_code == 404

    revoked = client.post(f"/gateway/grants/{grant_id}/revoke", headers=HEADERS)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["grant"]["status"] == "revoked"
    assert set(record.revocation_ids) <= set(store.revoked_grant_ids)
    assert _run(state) == "approval_required"
    assert client.post(f"/gateway/grants/{grant_id}/revoke", headers=HEADERS).status_code == 200
    audit = [e for e in store.audit_events if e.action == "gateway.grants.revoke"]
    assert audit and audit[-1].metadata["grant_id"] == grant_id


def test_run_scope_is_bound_to_the_run(grants_gateway, native_calls) -> None:
    state = _start_run("run-grant-run")
    assert _run(state) == "approval_required"
    response = _approve("run-grant-run", {"scope": "run"})
    assert response.status_code == 200, response.text
    record = main_module._GRANT_STORE.get(response.json()["escalation"]["grant_id"])
    assert record is not None and record.run_id == "run-grant-run"
    assert _run(state) == "completed" and _run(state) == "completed"
    assert _run(_start_run("run-grant-run-other")) == "approval_required"


def test_once_scope_stays_single_use(grants_gateway, native_calls) -> None:
    state = _start_run("run-grant-once")
    assert _run(state) == "approval_required"
    assert _approve("run-grant-once", {}).status_code == 200
    assert _run(state) == "completed"
    assert _run(state) == "approval_required"
    assert store.capability_grants == {}


def test_pinned_standing_grant_has_no_expiry(grants_gateway, native_calls) -> None:
    state = _start_run("run-grant-pin")
    assert _run(state) == "approval_required"
    response = _approve("run-grant-pin", {"scope": "standing", "pin": True})
    assert response.status_code == 200, response.text
    record = main_module._GRANT_STORE.get(response.json()["escalation"]["grant_id"])
    assert record is not None and record.pinned and record.expires_at is None


def test_request_body_cannot_inject_or_widen_a_grant(grants_gateway, native_calls) -> None:
    state = _start_run("run-grant-inject")
    assert _run(state) == "approval_required"
    wide = {"kind": "tool_call", "tool": "publish_release", "target_mode": "prefix", "target": "/"}
    for body in (
        {"scope": "standing", "grant_pattern": wide},
        {"scope": "standing", "pattern": wide},
        {"scope": "standing", "token": "En0KEwoEZ3JhbnQ"},
        {"scope": "forever"},
        {"scope": "run", "pin": True},
    ):
        response = _approve("run-grant-inject", body)
        assert response.status_code == 422, (body, response.text)
    assert store.capability_grants == {}
    assert _pending("run-grant-inject")["status"] == "pending"

    # A stored escalation tampered to an R4 risk is refused too.
    _pending("run-grant-inject")["risk"] = "R4"
    assert _approve("run-grant-inject", {"scope": "standing"}).status_code == 409
    assert store.capability_grants == {}


def test_grant_scopes_need_a_grant_authority(native_calls, request) -> None:
    gateway = Gateway(FakeEngine(), main_module._gateway_audit_sink)
    assert isinstance(gateway.grants, NoGrants)
    ctx = installed(gateway)
    ctx.__enter__()
    request.addfinalizer(lambda: ctx.__exit__(None, None, None))
    state = _start_run("run-grant-nokey")
    assert _run(state) == "approval_required"
    response = _approve("run-grant-nokey", {"scope": "standing"})
    assert response.status_code == 409
    assert "approve once" in response.json()["detail"]
    assert client.get("/gateway/grants", headers=HEADERS).json()["grants_enabled"] is False


def test_backend_gateway_runs_without_grants_when_no_key_is_available() -> None:
    def no_key() -> None:
        return None

    store_ = gr.GrantStore()
    assert isinstance(policy_gateway.build_grant_verifier(store_, no_key), NoGrants)
    assert isinstance(policy_gateway.build_grant_verifier(None), NoGrants)
    authority = gr.GrantAuthority.generate()
    verifier = policy_gateway.build_grant_verifier(store_, lambda: authority)
    assert isinstance(verifier, gr.BiscuitGrantVerifier) and verifier.ready


def test_grants_survive_a_state_round_trip(grants_gateway) -> None:
    authority = grants_gateway.grants.authority
    pattern = gr.GrantPattern("tool_call", "publish_release", "exact", "localhost", args="{}")
    record = main_module._GRANT_STORE.add(
        authority.mint(pattern, principal="alice", scope="standing")
    )
    main_module._GRANT_STORE.revoke(record.grant_id)
    snapshot = main_module._serialize_store_state()
    store.capability_grants, store.revoked_grant_ids = {}, []
    main_module._apply_store_state(
        {key: snapshot[key] for key in ("capability_grants", "revoked_grant_ids")}
    )
    restored = main_module._GRANT_STORE.get(record.grant_id)
    assert restored is not None and restored.revoked
    assert set(record.revocation_ids) <= set(store.revoked_grant_ids)
