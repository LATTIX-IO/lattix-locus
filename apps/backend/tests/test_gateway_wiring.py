"""LOCUS-332: backend entry points call the gateway before executing; ask/deny surface on the run."""

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
from app.main import app, store
from locus_runtime.gateway import Gateway
from tests.gateway_support import FakeEngine, installed

client = TestClient(app)
HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "alice"}


@pytest.fixture()
def gateway_with(request: pytest.FixtureRequest):
    def make(engine: FakeEngine) -> Gateway:
        gateway = Gateway(engine, main_module._gateway_audit_sink)
        ctx = installed(gateway)
        ctx.__enter__()
        request.addfinalizer(lambda: ctx.__exit__(None, None, None))
        return gateway

    return make


def _tool_node(tool_id: str) -> Any:
    return main_module.GraphNode(
        id="tool-1",
        type="locus/tool-call",
        title="Tool",
        config={"tool_id": tool_id, "endpoint_url": "http://localhost:9101/x", "method": "POST"},
    )


def _run_node(node: Any, execution_state: dict[str, Any]) -> dict[str, Any]:
    return main_module._execute_node(
        node=node,
        incoming=[{"message": "hello"}],
        incoming_by_port={"request": [{"message": "hello"}]},
        run_input={"message": "hello"},
        execution_state=execution_state,
        mem_store={},
    )


@pytest.fixture()
def native_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_native(**kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs.get("tool_id")))
        return {"ok": True}

    monkeypatch.setattr(main_module, "_execute_native_tool_call", fake_native)
    monkeypatch.setattr(main_module, "_admit_graph_tool_policy", lambda **_: None)
    return calls


def _gateway_events(run_id: str) -> list[Any]:
    return [
        event
        for event in store.run_events.get(run_id, [])
        if (event.metadata or {}).get("phase") == "gateway"
    ]


def test_tool_node_deny_is_typed_and_visible(gateway_with, native_calls: list[str]) -> None:
    gateway_with(FakeEngine({"agent_policy": False}))
    run_id = "run-gw-deny"
    node = _tool_node("list_issues")
    state = {
        "run_id": run_id,
        "gateway_session": main_module._open_tool_node_session(run_id, "alice", [node]),
    }

    result = _run_node(node, state)

    assert result["status"]["state"] == "policy_rejected"
    assert result["policy"]["control"] == "gateway"
    assert result["tool_output"]["message"].startswith("[denied by policy]")
    assert native_calls == []
    events = _gateway_events(run_id)
    assert events and events[-1].type == "guardrail_result"
    audit = next(e for e in store.audit_events if e.action == "gateway.decision")
    assert audit.outcome == "blocked"
    assert audit.metadata["gateway_outcome"] == "deny"
    for key in (
        "principal",
        "run_id",
        "action_kind",
        "tool",
        "target",
        "reasons",
        "policy_version",
        "risk_class",
    ):
        assert key in audit.metadata, key
    assert audit.metadata["principal"] == "alice"


def test_tool_node_allow_executes_after_gate(gateway_with, native_calls: list[str]) -> None:
    engine = FakeEngine()
    gateway_with(engine)
    run_id = "run-gw-allow"
    node = _tool_node("list_issues")
    state = {
        "run_id": run_id,
        "gateway_session": main_module._open_tool_node_session(run_id, "alice", [node]),
    }

    result = _run_node(node, state)

    assert result["status"]["state"] == "completed"
    assert native_calls == ["list_issues"]
    agent_input = next(payload for policy, payload in engine.calls if policy == "agent_policy")
    assert agent_input["tool"] == "list_issues"
    assert "list_issues" in agent_input["allowed_tools"]
    assert [policy for policy, _ in engine.calls] == ["agent_policy", "network_egress"]


def test_tool_node_ask_blocks_until_approved(gateway_with, native_calls: list[str]) -> None:
    gateway_with(FakeEngine())
    run_id = "run-gw-ask"
    store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id, title="gateway ask", status="Running", updatedAt="now", progressLabel="running"
    )
    store.run_details[run_id] = {"access": {"actor": "alice"}}
    node = _tool_node("publish_release")
    state = {
        "run_id": run_id,
        "gateway_session": main_module._open_tool_node_session(run_id, "alice", [node]),
    }

    first = _run_node(node, state)
    assert first["status"]["state"] == "approval_required"
    assert first["tool_output"]["message"].startswith("[permission required]")
    assert native_calls == []
    escalations = store.run_details[run_id]["escalations"]
    pending = [e for e in escalations if e.get("kind") == "gateway" and e["status"] == "pending"]
    assert len(pending) == 1 and pending[0]["risk"] == "R3"
    assert _gateway_events(run_id)[-1].type == "approval_required"

    # Still blocked on retry before approval (no duplicate escalation).
    assert _run_node(node, state)["status"]["state"] == "approval_required"
    assert len([e for e in escalations if e.get("kind") == "gateway"]) == 1

    response = client.post(
        f"/workflow-runs/{run_id}/escalations/{pending[0]['id']}/approve", headers=HEADERS
    )
    assert response.status_code == 200, response.text

    approved = _run_node(node, state)
    assert approved["status"]["state"] == "completed"
    assert native_calls == ["publish_release"]
    # Single use: the next identical call asks again.
    assert _run_node(node, state)["status"]["state"] == "approval_required"


def test_tool_node_without_session_is_denied_by_real_gateway(
    gateway_with, native_calls: list[str]
) -> None:
    gateway_with(FakeEngine())
    result = _run_node(_tool_node("list_issues"), {"run_id": "run-gw-unbound"})
    assert result["status"]["state"] == "policy_rejected"
    assert "gateway.unauthenticated_caller" in result["policy"]["reasons"]
    assert native_calls == []


def test_mcp_tool_calls_pass_the_gateway(gateway_with, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway_with(FakeEngine())
    server_calls: list[tuple[str, dict[str, Any]]] = []

    class FakeServer:
        base_url = "http://localhost:7071/mcp"

        def call_tool(self, name: str, arguments: dict[str, Any], *, decision: Any) -> str:
            from app.mcp_client import _check_decision

            _check_decision(decision, name, arguments)
            server_calls.append((name, arguments))
            return "ok"

    dispatch = {
        "srv__list_issues": (FakeServer(), "list_issues"),
        "srv__send_email": (FakeServer(), "send_email"),
    }
    monkeypatch.setattr(
        main_module,
        "_gather_mcp_run_tools",
        lambda **_: ([{"type": "function"}], dispatch, ["srv"]),
    )
    outputs: dict[str, str] = {}

    def fake_iterations(run_id: str, *_: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        executor = kwargs["tool_executor"]
        for name in ("srv__list_issues", "srv__send_email"):
            try:
                outputs[name] = executor(name, {"q": "x"})
            except Exception as exc:  # noqa: BLE001 - mirrors the real tool loop
                outputs[name] = f"failed: {exc}"
        return "done", {"mode": "live"}

    monkeypatch.setattr(main_module, "_run_agent_iterations", fake_iterations)
    monkeypatch.setattr(main_module, "_submit_run_task", lambda fn: fn())

    response = client.post(
        "/workflow-runs", json={"prompt": "check issues and email"}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]

    assert outputs["srv__list_issues"] == "ok"
    assert outputs["srv__send_email"].startswith("failed: gateway ask")
    assert server_calls == [("list_issues", {"q": "x"})]
    assert _gateway_events(run_id)[-1].type == "approval_required"


def test_provisioned_harness_executor_carries_run_session(gateway_with, tmp_path: Path) -> None:
    from app import policy_gateway
    from locus_runtime.harness.workspace_binding import WorkspaceBinding, WorkspaceManager

    engine = FakeEngine()
    gateway_with(engine)
    opened: list[Any] = []
    factory = policy_gateway.harness_session_factory(
        run_id="run-gw-harness",
        principal="alice",
        egress=(),
        on_decision=main_module._gateway_decision_listener,
        opened=opened,
    )
    binding = WorkspaceBinding(repo_path=str(tmp_path), isolation="in-place")
    prov = WorkspaceManager().provision(binding, "run-gw-harness", session_factory=factory)
    session = prov.workspace.executor.gateway_session
    assert session is not None and opened == [session]
    assert session.caller.run_id == "run-gw-harness" and session.caller.engine == "harness"
    assert session.capabilities.write_roots == (str(tmp_path.resolve()),)

    prov.workspace.executor.write_file("a.txt", "x")
    fs_input = next(payload for policy, payload in engine.calls if policy == "filesystem_access")
    assert fs_input["action"] == "write"
    assert fs_input["allowed_write_paths"] == [str(tmp_path.resolve())]
    policy_gateway.close_sessions(opened)
