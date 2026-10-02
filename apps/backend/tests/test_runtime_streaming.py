from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_generated_artifacts import (
    ADMIN_HEADERS,
    AUTH_HEADERS,
    OWNER_AUTH_HEADERS,
    _run_access,
    _sample_graph,
    client,
    main_module,
    store,
)


@pytest.fixture(autouse=True)
def _default_runtime_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTIER_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("FRONTIER_SECURE_LOCAL_MODE", raising=False)
    monkeypatch.delenv("FRONTIER_REQUIRE_AUTHENTICATED_REQUESTS", raising=False)
    monkeypatch.delenv("FRONTIER_REQUIRE_A2A_RUNTIME_HEADERS", raising=False)
    monkeypatch.delenv("FRONTIER_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", raising=False)
    monkeypatch.delenv("FRONTIER_ADMIN_ACTORS", raising=False)
    monkeypatch.delenv("FRONTIER_BUILDER_ACTORS", raising=False)
    monkeypatch.setenv("FRONTIER_BOOTSTRAP_ADMIN_USERNAME", "frontier-admin")
    monkeypatch.setenv("FRONTIER_BOOTSTRAP_ADMIN_EMAIL", "admin@frontier.localhost")
    monkeypatch.setenv("FRONTIER_BOOTSTRAP_ADMIN_SUBJECT", "frontier-admin")


def _seed_streamable_run(run_id: str, *, owner: str = "tester") -> None:
    store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id,
        title="Stream Coverage Run",
        status="Running",
        updatedAt="2026-04-06T00:00:00Z",
        progressLabel="Queued",
        kind="workflow",
    )
    store.run_details[run_id] = {
        "artifacts": [],
        "status": "Running",
        "graph": {"nodes": [], "links": []},
        "agent_traces": [],
        "approvals": {"required": False, "pending": False},
        "access": _run_access(owner),
    }
    store.run_events[run_id] = []
    store.run_streams[run_id] = []
    store.run_stream_complete[run_id] = False


def _clear_streamable_run(run_id: str) -> None:
    store.runs.pop(run_id, None)
    store.run_details.pop(run_id, None)
    store.run_events.pop(run_id, None)
    store.run_streams.pop(run_id, None)
    store.run_stream_complete.pop(run_id, None)


def _stream_payloads(run_id: str, *, headers: dict[str, str]) -> list[dict[str, object]]:
    response = client.get(f"/workflow-runs/{run_id}/stream", headers=headers)
    assert response.status_code == 200
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_graph_validate_reports_invalid_prompt_node_configuration() -> None:
    invalid_graph = _sample_graph()
    invalid_graph["nodes"][1]["config"] = {"system_prompt_text": ""}

    response = client.post(
        "/graph/validate",
        json={
            "schema_version": "frontier-graph/1.0",
            "nodes": invalid_graph["nodes"],
            "links": invalid_graph["links"],
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(issue["code"] == "PROMPT_TEXT_REQUIRED" for issue in body["issues"])


def test_graph_validate_reports_invalid_agent_skill_configuration() -> None:
    invalid_graph = _sample_graph()
    agent_node = next(node for node in invalid_graph["nodes"] if node["type"] == "agent")
    agent_node["config"]["skills"] = ["/incident-triage", {"bad": "shape"}]

    response = client.post(
        "/graph/validate",
        json={
            "schema_version": "frontier-graph/1.0",
            "nodes": invalid_graph["nodes"],
            "links": invalid_graph["links"],
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(issue["code"] == "AGENT_SKILL_TYPE_INVALID" for issue in body["issues"])


def test_graph_validate_reports_missing_transform_source_input() -> None:
    invalid_graph = {
        "nodes": [
            {
                "id": "trigger",
                "title": "Trigger",
                "type": "trigger",
                "config": {"trigger_mode": "manual"},
            },
            {
                "id": "transform",
                "title": "Transform",
                "type": "frontier/transform",
                "config": {
                    "transform_mode": "map",
                    "mapping_json": '{"priority":"{{var.source.priority}}"}',
                },
            },
            {
                "id": "output",
                "title": "Output",
                "type": "output",
                "config": {"destination": "artifact_store", "format": "json"},
            },
        ],
        "links": [
            {"from": "trigger", "to": "transform", "from_port": "out", "to_port": "in"},
            {"from": "transform", "to": "output", "from_port": "out", "to_port": "in"},
            {"from": "transform", "to": "output", "from_port": "result", "to_port": "result"},
        ],
    }

    response = client.post(
        "/graph/validate",
        json={
            "schema_version": "frontier-graph/1.0",
            "nodes": invalid_graph["nodes"],
            "links": invalid_graph["links"],
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(issue["code"] == "TRANSFORM_SOURCE_INPUT_REQUIRED" for issue in body["issues"])


def test_router_node_executes_rules_mode() -> None:
    node = main_module.GraphNode(
        id="router-1",
        type="frontier/router",
        title="Priority Router",
        config={
            "router_mode": "rules",
            "decision_key": "priority",
            "default_route": "default",
            "rules_json": json.dumps(
                [{"route": "priority", "key": "priority", "operator": "eq", "value": "high"}]
            ),
        },
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"priority": "high", "ticket": "INC-42"}],
        incoming_by_port={"candidate": [{"priority": "high", "ticket": "INC-42"}]},
        run_input={"message": "route this incident"},
        execution_state={},
        mem_store={},
    )

    assert result["decision"]["selected_route"] == "priority"
    assert result["matched_payload"]["ticket"] == "INC-42"


def test_transform_node_maps_payload_deterministically() -> None:
    node = main_module.GraphNode(
        id="transform-1",
        type="frontier/transform",
        title="Normalize Ticket",
        config={
            "transform_mode": "map",
            "mapping_json": json.dumps(
                {
                    "priority": "{{var.source.priority}}",
                    "summary": "{{var.source.summary}}",
                }
            ),
        },
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"priority": "high", "summary": "Database latency spike"}],
        incoming_by_port={"source": [{"priority": "high", "summary": "Database latency spike"}]},
        run_input={"message": "transform this payload"},
        execution_state={},
        mem_store={},
    )

    assert result["result"] == {
        "priority": "high",
        "summary": "Database latency spike",
    }


def test_iterator_node_emits_loop_branch_for_items() -> None:
    node = main_module.GraphNode(
        id="iterator-1",
        type="frontier/iterator",
        title="Iterate Accounts",
        config={"iteration_mode": "foreach", "item_path": "items", "max_items": 10},
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"items": [{"id": "a1"}, {"id": "a2"}]}],
        incoming_by_port={"items": [{"items": [{"id": "a1"}, {"id": "a2"}]}]},
        run_input={"message": "iterate accounts"},
        execution_state={},
        mem_store={},
    )

    assert result["iteration"]["emitted_branch"] == "loop"
    assert result["item"] == {"id": "a1"}
    assert result["aggregate"] == [{"id": "a1"}, {"id": "a2"}]


def test_event_node_publishes_structured_event() -> None:
    node = main_module.GraphNode(
        id="event-1",
        type="frontier/event",
        title="Publish Event",
        config={"event_mode": "publish", "topic": "ops.alerts", "event_name": "incident.created"},
    )
    execution_state: dict[str, object] = {"run_id": "run-1"}

    result = main_module._execute_node(
        node=node,
        incoming=[{"id": "INC-42", "severity": "high"}],
        incoming_by_port={"payload": [{"id": "INC-42", "severity": "high"}]},
        run_input={"message": "publish incident event"},
        execution_state=execution_state,
        mem_store={},
    )

    assert result["event"]["topic"] == "ops.alerts"
    assert result["event"]["event_name"] == "incident.created"
    assert result["receipt"]["correlation_id"] == "run-1"


def test_event_node_consume_without_match_emits_idle_branch() -> None:
    node = main_module.GraphNode(
        id="event-2",
        type="frontier/event",
        title="Consume Event",
        config={"event_mode": "consume", "topic": "ops.alerts", "event_name": "incident.created"},
    )
    execution_state: dict[str, object] = {"run_id": "run-2", "events": []}

    result = main_module._execute_node(
        node=node,
        incoming=[{"id": "INC-99"}],
        incoming_by_port={"payload": [{"id": "INC-99"}]},
        run_input={"message": "consume incident event"},
        execution_state=execution_state,
        mem_store={},
    )

    assert result["receipt"]["found"] is False
    assert result["out"]["branch"] == "idle"


def test_data_store_node_upserts_and_reads_record() -> None:
    upsert_node = main_module.GraphNode(
        id="store-1",
        type="frontier/data-store",
        title="Store Record",
        config={"operation": "upsert", "collection": "tickets", "record_key": "id"},
    )
    read_node = main_module.GraphNode(
        id="store-2",
        type="frontier/data-store",
        title="Read Record",
        config={"operation": "read", "collection": "tickets", "record_key": "id"},
    )
    execution_state: dict[str, object] = {}

    upsert_result = main_module._execute_node(
        node=upsert_node,
        incoming=[{"id": "ticket-1", "status": "open"}],
        incoming_by_port={"record": [{"id": "ticket-1", "status": "open"}]},
        run_input={"message": "store record"},
        execution_state=execution_state,
        mem_store={},
    )
    read_result = main_module._execute_node(
        node=read_node,
        incoming=[{"id": "ticket-1"}],
        incoming_by_port={"record": [{"id": "ticket-1"}]},
        run_input={"message": "read record"},
        execution_state=execution_state,
        mem_store={},
    )

    assert upsert_result["status"]["record_id"] == "ticket-1"
    assert read_result["result"] == {"id": "ticket-1", "status": "open"}


def test_agent_node_threads_skills_into_runtime_request_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_run_openai_chat(
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        messages: list[dict[str, str]] | None = None,
        runtime: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, object]]:
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["model"] = model
        captured["temperature"] = temperature
        captured["messages"] = messages
        captured["runtime"] = runtime
        return (
            "Investigate the incident with /incident-triage first.",
            {"provider": "openai", "model": model, "mode": "simulated"},
        )

    monkeypatch.setattr(main_module, "_run_openai_chat", _fake_run_openai_chat)

    node = main_module.GraphNode(
        id="agent-1",
        type="frontier/agent",
        title="Incident Agent",
        config={
            "agent_id": "incident-agent",
            "model": "gpt-5.4",
            "temperature": 0.4,
            "system_prompt": "Be precise.",
            "skills": ["incident-triage", "/tenant-oncall", "incident-triage"],
        },
    )

    result = main_module._execute_node(
        node=node,
        incoming=[],
        incoming_by_port=None,
        run_input={"message": "Investigate the outage"},
        execution_state={"runtime": {"use_memory": False}},
        mem_store={},
    )

    assert "Activated skills" in str(captured["user_prompt"])
    assert "/incident-triage" in str(captured["user_prompt"])
    assert "/tenant-oncall" in str(captured["user_prompt"])
    assert result["tool_request"]["request"] == {
        "agent_id": "incident-agent",
        "message": "Investigate the incident with /incident-triage first.",
        "skills": ["/incident-triage", "/tenant-oncall"],
    }
    assert result["state_delta"]["skills"] == ["/incident-triage", "/tenant-oncall"]


def test_tool_call_node_routes_unspecified_tool_via_request_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    integration_id = "int-skill-incident"
    original_integration = store.integrations.get(integration_id)
    original_enforce_egress = store.platform_settings.enforce_egress_allowlist

    def _fake_execute_native_tool_call(
        *,
        tool_id: str,
        tool_config: dict[str, object],
        request_payload: object,
        context_payload: object,
        skill_hints: list[str],
        call_index: int,
        endpoint_url: str,
        method: str,
    ) -> dict[str, object]:
        captured["tool_id"] = tool_id
        captured["request_payload"] = request_payload
        captured["context_payload"] = context_payload
        captured["skill_hints"] = list(skill_hints)
        captured["endpoint_url"] = endpoint_url
        captured["method"] = method
        return {
            "ok": True,
            "tool_id": tool_id,
            "request": request_payload,
            "context": context_payload,
            "skill_hints": list(skill_hints),
            "integration_id": tool_id,
        }

    monkeypatch.setattr(main_module, "_execute_native_tool_call", _fake_execute_native_tool_call)
    store.integrations[integration_id] = main_module.IntegrationDefinition(
        id=integration_id,
        name="Incident Triage Webhook",
        type="http",
        status="configured",
        base_url="http://localhost:9101/incident-triage",
        capabilities=["incident-triage", "ops"],
    )
    store.platform_settings.enforce_egress_allowlist = False

    try:
        node = main_module.GraphNode(
            id="tool-1",
            type="frontier/tool-call",
            title="Skill Routed Tool",
            config={"tool_id": "tool/unspecified", "method": "POST"},
        )

        result = main_module._execute_node(
            node=node,
            incoming=[{"message": "Triaging the incident", "skills": ["incident-triage"]}],
            incoming_by_port={
                "request": [{"message": "Triaging the incident", "skills": ["incident-triage"]}]
            },
            run_input={"message": "fallback"},
            execution_state={
                "assembly_policy": {
                    "allowed_integrations": [integration_id],
                    "allowed_network_hosts": ["localhost"],
                    "allowed_tool_column_kinds": ["tool"],
                }
            },
            mem_store={},
        )

        assert captured["tool_id"] == integration_id
        assert captured["skill_hints"] == ["/incident-triage"]
        assert captured["endpoint_url"] == "http://localhost:9101/incident-triage"
        assert captured["request_payload"] == {
            "message": "Triaging the incident",
            "skills": ["/incident-triage"],
        }
        assert result["policy"]["tool_selection"]["selected_integration_id"] == integration_id
    finally:
        store.platform_settings.enforce_egress_allowlist = original_enforce_egress
        if original_integration is None:
            store.integrations.pop(integration_id, None)
        else:
            store.integrations[integration_id] = original_integration


def test_tool_call_node_falls_back_to_model_request_skills_for_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    integration_id = "int-skill-oncall"
    original_integration = store.integrations.get(integration_id)
    original_enforce_egress = store.platform_settings.enforce_egress_allowlist

    def _fake_execute_native_tool_call(
        *,
        tool_id: str,
        tool_config: dict[str, object],
        request_payload: object,
        context_payload: object,
        skill_hints: list[str],
        call_index: int,
        endpoint_url: str,
        method: str,
    ) -> dict[str, object]:
        captured["tool_id"] = tool_id
        captured["request_payload"] = request_payload
        captured["context_payload"] = context_payload
        captured["skill_hints"] = list(skill_hints)
        return {
            "ok": True,
            "tool_id": tool_id,
            "request": request_payload,
            "context": context_payload,
            "skill_hints": list(skill_hints),
            "integration_id": tool_id,
        }

    monkeypatch.setattr(main_module, "_execute_native_tool_call", _fake_execute_native_tool_call)
    store.integrations[integration_id] = main_module.IntegrationDefinition(
        id=integration_id,
        name="Tenant Oncall Hook",
        type="http",
        status="configured",
        base_url="http://localhost:9102/oncall",
        capabilities=["tenant-oncall"],
    )
    store.platform_settings.enforce_egress_allowlist = False

    try:
        node = main_module.GraphNode(
            id="tool-2",
            type="frontier/tool-call",
            title="Context Routed Tool",
            config={"tool_id": "tool/unspecified", "method": "POST"},
        )

        result = main_module._execute_node(
            node=node,
            incoming=[{"message": "Notify the tenant on-call team"}],
            incoming_by_port={
                "request": [{"message": "Notify the tenant on-call team"}],
                "context": [{"model": {"request": {"skills": ["tenant-oncall"]}}}],
            },
            run_input={"message": "fallback"},
            execution_state={
                "assembly_policy": {
                    "allowed_integrations": [integration_id],
                    "allowed_network_hosts": ["localhost"],
                    "allowed_tool_column_kinds": ["tool"],
                }
            },
            mem_store={},
        )

        assert captured["tool_id"] == integration_id
        assert captured["skill_hints"] == ["/tenant-oncall"]
        assert captured["request_payload"] == {
            "message": "Notify the tenant on-call team",
            "skills": ["/tenant-oncall"],
        }
        assert result["policy"]["tool_selection"]["skills"] == ["/tenant-oncall"]
    finally:
        store.platform_settings.enforce_egress_allowlist = original_enforce_egress
        if original_integration is None:
            store.integrations.pop(integration_id, None)
        else:
            store.integrations[integration_id] = original_integration


def test_evidence_column_can_retrieve_approved_source() -> None:
    node = main_module.GraphNode(
        id="retrieval-1",
        type="frontier/retrieval",
        title="Evidence Retrieval",
        config={"column_kind": "evidence", "source_id": "kb://default", "top_k": 1},
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"query": "known context"}],
        incoming_by_port={"query": [{"query": "known context"}]},
        run_input={"message": "fallback"},
        execution_state={
            "session_id": "session:slice-8-retrieval",
            "assembly_policy": {"allowed_retrieval_sources": ["kb://default"]},
        },
        mem_store={},
    )

    assert result["out"]["state"] == "completed"
    assert result["policy"]["source_id"] == "kb://default"


def test_retrieval_source_url_requires_explicit_policy_grant() -> None:
    node = main_module.GraphNode(
        id="retrieval-url-1",
        type="frontier/retrieval",
        title="Evidence URL Retrieval",
        config={
            "column_kind": "evidence",
            "source_id": "kb://default",
            "source_url": "http://localhost:9301/context.txt",
            "top_k": 1,
        },
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"query": "known context"}],
        incoming_by_port={"query": [{"query": "known context"}]},
        run_input={"message": "fallback"},
        execution_state={"assembly_policy": {"allowed_retrieval_sources": ["kb://default"]}},
        mem_store={},
    )

    assert result["status"]["state"] == "policy_rejected"
    assert result["policy"]["control"] == "assembly_allowed_retrieval_source_urls"


def test_evidence_column_cannot_call_arbitrary_tool() -> None:
    node = main_module.GraphNode(
        id="tool-evidence-1",
        type="frontier/tool-call",
        title="Evidence Tool Attempt",
        config={
            "column_kind": "evidence",
            "tool_id": "tool/arbitrary",
            "endpoint_url": "http://localhost:9103/tool",
        },
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"message": "try tool"}],
        incoming_by_port={"request": [{"message": "try tool"}]},
        run_input={"message": "fallback"},
        execution_state={
            "assembly_policy": {
                "allowed_tools": ["tool/arbitrary"],
                "allowed_network_hosts": ["localhost"],
                "allowed_tool_column_kinds": ["tool"],
            }
        },
        mem_store={},
    )

    assert result["status"]["state"] == "policy_rejected"
    assert result["policy"]["control"] == "column_kind_tool_permission"


def test_tool_column_can_call_approved_integration_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []
    approved_id = "int-approved-tool"
    denied_id = "int-denied-tool"
    original_approved = store.integrations.get(approved_id)
    original_denied = store.integrations.get(denied_id)
    original_enforce_egress = store.platform_settings.enforce_egress_allowlist

    def _fake_execute_native_tool_call(**kwargs: object) -> dict[str, object]:
        captured.append(str(kwargs["tool_id"]))
        return {"ok": True, "integration_id": kwargs["tool_id"]}

    monkeypatch.setattr(main_module, "_execute_native_tool_call", _fake_execute_native_tool_call)
    store.platform_settings.enforce_egress_allowlist = False
    store.integrations[approved_id] = main_module.IntegrationDefinition(
        id=approved_id,
        name="Approved Tool",
        type="http",
        status="configured",
        base_url="http://localhost:9201/approved",
    )
    store.integrations[denied_id] = main_module.IntegrationDefinition(
        id=denied_id,
        name="Denied Tool",
        type="http",
        status="configured",
        base_url="http://localhost:9202/denied",
    )
    execution_state = {
        "assembly_policy": {
            "allowed_integrations": [approved_id],
            "allowed_network_hosts": ["localhost"],
            "allowed_tool_column_kinds": ["tool"],
        }
    }

    try:
        approved = main_module._execute_node(
            node=main_module.GraphNode(
                id="tool-approved",
                type="frontier/tool-call",
                title="Approved Tool",
                config={"column_kind": "tool", "integration_id": approved_id},
            ),
            incoming=[{"message": "call approved"}],
            incoming_by_port={"request": [{"message": "call approved"}]},
            run_input={"message": "fallback"},
            execution_state=dict(execution_state),
            mem_store={},
        )
        denied = main_module._execute_node(
            node=main_module.GraphNode(
                id="tool-denied",
                type="frontier/tool-call",
                title="Denied Tool",
                config={"column_kind": "tool", "integration_id": denied_id},
            ),
            incoming=[{"message": "call denied"}],
            incoming_by_port={"request": [{"message": "call denied"}]},
            run_input={"message": "fallback"},
            execution_state=dict(execution_state),
            mem_store={},
        )

        assert approved["status"]["state"] == "completed"
        assert captured == [approved_id]
        assert denied["status"]["state"] == "policy_rejected"
        assert denied["policy"]["control"] == "assembly_allowed_integrations"
    finally:
        store.platform_settings.enforce_egress_allowlist = original_enforce_egress
        if original_approved is None:
            store.integrations.pop(approved_id, None)
        else:
            store.integrations[approved_id] = original_approved
        if original_denied is None:
            store.integrations.pop(denied_id, None)
        else:
            store.integrations[denied_id] = original_denied


def test_skill_routed_integration_still_respects_tenant_and_egress_policies() -> None:
    integration_id = "int-skill-egress"
    original_integration = store.integrations.get(integration_id)
    original_enforce_egress = store.platform_settings.enforce_egress_allowlist
    original_allowed_hosts = list(store.platform_settings.allowed_egress_hosts)

    store.integrations[integration_id] = main_module.IntegrationDefinition(
        id=integration_id,
        name="Tenant Search Hook",
        type="http",
        status="configured",
        base_url="http://blocked.example.com/search",
        capabilities=["tenant-search"],
        egress_allowlist=["blocked.example.com"],
    )
    store.platform_settings.enforce_egress_allowlist = True
    store.platform_settings.allowed_egress_hosts = ["localhost"]

    try:
        result = main_module._execute_node(
            node=main_module.GraphNode(
                id="tool-skill-egress",
                type="frontier/tool-call",
                title="Skill Routed Egress",
                config={"column_kind": "tool", "tool_id": "tool/unspecified"},
            ),
            incoming=[{"message": "search", "skills": ["tenant-search"]}],
            incoming_by_port={"request": [{"message": "search", "skills": ["tenant-search"]}]},
            run_input={"message": "fallback"},
            execution_state={
                "assembly_policy": {
                    "allowed_integrations": [integration_id],
                    "allowed_network_hosts": ["blocked.example.com"],
                    "allowed_tool_column_kinds": ["tool"],
                }
            },
            mem_store={},
        )

        assert result["status"]["state"] == "policy_rejected"
        assert result["policy"]["control"] == "egress_allowlist"
    finally:
        store.platform_settings.enforce_egress_allowlist = original_enforce_egress
        store.platform_settings.allowed_egress_hosts = original_allowed_hosts
        if original_integration is None:
            store.integrations.pop(integration_id, None)
        else:
            store.integrations[integration_id] = original_integration


def test_tool_call_resolves_approved_mcp_connection_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    connection_id = "mcp-approved-1"
    original_connection = store.mcp_connections.get(connection_id)
    original_enforce_egress = store.platform_settings.enforce_egress_allowlist
    original_allowed_urls = list(store.platform_settings.allowed_mcp_server_urls)
    original_require_local = store.platform_settings.mcp_require_local_server

    def _fake_execute_native_tool_call(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "ok": True,
            "tool_id": kwargs["tool_id"],
            "endpoint_url": kwargs["endpoint_url"],
            "mcp_connection_id": (kwargs["tool_config"] or {}).get("mcp_connection_id"),
        }

    monkeypatch.setattr(main_module, "_execute_native_tool_call", _fake_execute_native_tool_call)
    store.platform_settings.enforce_egress_allowlist = False
    store.platform_settings.allowed_mcp_server_urls = ["http://localhost:7071/mcp/github"]
    store.platform_settings.mcp_require_local_server = True
    store.mcp_connections[connection_id] = main_module.MCPConnectionDefinition(
        id=connection_id,
        starter_id="github",
        wave=1,
        name="Approved GitHub MCP",
        status="approved",
        server_url="http://localhost:7071/mcp/github",
        transport="streamable_http",
        auth_type="bearer",
        secret_ref="secret/integrations/mcp/github/token",
        capabilities=["/repo-read"],
        permission_scopes=["repo:read"],
        data_access=["source-code-metadata"],
        egress_allowlist=["localhost"],
        execution_mode="local",
    )

    try:
        result = main_module._execute_node(
            node=main_module.GraphNode(
                id="tool-mcp-approved",
                type="frontier/tool-call",
                title="Approved MCP Tool",
                config={
                    "column_kind": "tool",
                    "tool_id": "tool/unspecified",
                    "mcp_connection_id": connection_id,
                },
            ),
            incoming=[{"message": "call staged mcp"}],
            incoming_by_port={"request": [{"message": "call staged mcp"}]},
            run_input={"message": "fallback"},
            execution_state={
                "assembly_policy": {
                    "allowed_mcp_server_urls": ["http://localhost:7071/mcp/github"],
                    "allowed_network_hosts": ["localhost"],
                    "allowed_tool_column_kinds": ["tool"],
                }
            },
            mem_store={},
        )

        assert result["status"]["state"] == "completed"
        assert captured["tool_id"] == "tool/mcp"
        assert captured["endpoint_url"] == "http://localhost:7071/mcp/github"
        assert result["tool_output"]["mcp_connection_id"] == connection_id
        assert result["policy"]["mcp_connection_id"] == connection_id
    finally:
        store.platform_settings.enforce_egress_allowlist = original_enforce_egress
        store.platform_settings.allowed_mcp_server_urls = original_allowed_urls
        store.platform_settings.mcp_require_local_server = original_require_local
        if original_connection is None:
            store.mcp_connections.pop(connection_id, None)
        else:
            store.mcp_connections[connection_id] = original_connection


def test_tool_call_rejects_unapproved_mcp_connection_id() -> None:
    connection_id = "mcp-draft-1"
    original_connection = store.mcp_connections.get(connection_id)

    store.mcp_connections[connection_id] = main_module.MCPConnectionDefinition(
        id=connection_id,
        starter_id="github",
        wave=1,
        name="Draft GitHub MCP",
        status="draft",
        server_url="http://localhost:7071/mcp/github",
        transport="streamable_http",
        auth_type="bearer",
        secret_ref="secret/integrations/mcp/github/token",
    )

    try:
        result = main_module._execute_node(
            node=main_module.GraphNode(
                id="tool-mcp-draft",
                type="frontier/tool-call",
                title="Draft MCP Tool",
                config={
                    "column_kind": "tool",
                    "tool_id": "tool/unspecified",
                    "mcp_connection_id": connection_id,
                },
            ),
            incoming=[{"message": "call staged mcp"}],
            incoming_by_port={"request": [{"message": "call staged mcp"}]},
            run_input={"message": "fallback"},
            execution_state={
                "assembly_policy": {
                    "allowed_mcp_server_urls": ["http://localhost:7071/mcp/github"],
                    "allowed_network_hosts": ["localhost"],
                    "allowed_tool_column_kinds": ["tool"],
                }
            },
            mem_store={},
        )

        assert result["status"]["state"] == "policy_rejected"
        assert result["policy"]["control"] == "mcp_connection_approval_required"
        assert result["policy"]["requested_connection_id"] == connection_id
    finally:
        if original_connection is None:
            store.mcp_connections.pop(connection_id, None)
        else:
            store.mcp_connections[connection_id] = original_connection


def test_gemini_stream_publishes_delta_chunks_to_subscribers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStreamResponse:
        def __enter__(self) -> "_FakeStreamResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self) -> list[str]:
            return [
                'data: {"candidates":[{"content":{"parts":[{"text":"hello "}]}}]}',
                'data: {"candidates":[{"content":{"parts":[{"text":"world"}]}}]}',
            ]

    class _FakeClient:
        def __init__(self, *_: object, **__: object) -> None:
            return None

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def stream(self, *_: object, **__: object) -> _FakeStreamResponse:
            return _FakeStreamResponse()

    observed_chunks: list[str] = []

    monkeypatch.setattr(main_module.httpx, "Client", _FakeClient)

    chunks, metadata = main_module._stream_gemini_chat(
        runtime={
            "provider": "gemini",
            "model": "gemini-2.0-flash",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "api_key": "test-key",
            "source": "test",
        },
        system_prompt="Be helpful.",
        user_prompt="Say hello.",
        temperature=0.2,
        on_chunk=observed_chunks.append,
    )

    assert chunks == ["hello ", "world"]
    assert observed_chunks == ["hello ", "world"]
    assert metadata["provider"] == "gemini"
    assert metadata["transport"] == "sse"


def test_error_handler_node_recovers_failed_payload() -> None:
    node = main_module.GraphNode(
        id="error-handler-1",
        type="frontier/error-handler",
        title="Recover Tool Failure",
        config={
            "handler_mode": "fallback",
            "fallback_value": '{"status":"degraded"}',
            "fallback_message": "Used degraded fallback.",
            "retryable": True,
        },
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"ok": False, "message": "tool timed out"}],
        incoming_by_port={"error": [{"ok": False, "message": "tool timed out"}]},
        run_input={"message": "handle tool failure"},
        execution_state={},
        mem_store={},
    )

    assert result["status"]["state"] == "recovered"
    assert result["handled"]["fallback"] == {"status": "degraded"}
    assert result["handled"]["message"] == "tool timed out"


def test_wait_node_emits_timeout_branch_when_delay_exceeds_timeout() -> None:
    node = main_module.GraphNode(
        id="wait-1",
        type="frontier/wait",
        title="Wait For Approval",
        config={"wait_mode": "delay", "delay_ms": 2000, "timeout_ms": 500, "simulate_wait": True},
    )

    result = main_module._execute_node(
        node=node,
        incoming=[{"ticket": "INC-42"}],
        incoming_by_port={"resume_payload": [{"ticket": "INC-42"}]},
        run_input={"message": "wait for approval"},
        execution_state={},
        mem_store={},
    )

    assert result["wait"]["branch"] == "timeout"
    assert result["result"]["payload"] == {"ticket": "INC-42"}


def test_graph_run_skips_inactive_router_branches() -> None:
    graph = {
        "schema_version": "frontier-graph/1.0",
        "nodes": [
            {
                "id": "trigger",
                "title": "Trigger",
                "type": "frontier/trigger",
                "config": {"trigger_mode": "manual"},
            },
            {
                "id": "router",
                "title": "Priority Router",
                "type": "frontier/router",
                "config": {
                    "router_mode": "rules",
                    "route_match_a": "priority",
                    "route_match_b": "standard",
                    "default_route": "default",
                    "rules_json": json.dumps(
                        [
                            {
                                "route": "priority",
                                "key": "message",
                                "operator": "contains",
                                "value": "urgent",
                            }
                        ]
                    ),
                },
            },
            {
                "id": "priority-transform",
                "title": "Priority Transform",
                "type": "frontier/transform",
                "config": {
                    "transform_mode": "map",
                    "mapping_json": json.dumps(
                        {"lane": "priority", "message": "{{var.source.message}}"}
                    ),
                },
            },
            {
                "id": "default-transform",
                "title": "Default Transform",
                "type": "frontier/transform",
                "config": {
                    "transform_mode": "map",
                    "mapping_json": json.dumps(
                        {"lane": "default", "message": "{{var.source.message}}"}
                    ),
                },
            },
            {
                "id": "output",
                "title": "Output",
                "type": "frontier/output",
                "config": {"destination": "artifact_store", "format": "json"},
            },
        ],
        "links": [
            {"from": "trigger", "to": "router", "from_port": "out", "to_port": "in"},
            {"from": "trigger", "to": "router", "from_port": "payload", "to_port": "candidate"},
            {"from": "router", "to": "priority-transform", "from_port": "match_a", "to_port": "in"},
            {
                "from": "router",
                "to": "priority-transform",
                "from_port": "matched_payload",
                "to_port": "source",
            },
            {"from": "router", "to": "default-transform", "from_port": "default", "to_port": "in"},
            {
                "from": "router",
                "to": "default-transform",
                "from_port": "matched_payload",
                "to_port": "source",
            },
            {"from": "priority-transform", "to": "output", "from_port": "out", "to_port": "in"},
            {
                "from": "priority-transform",
                "to": "output",
                "from_port": "result",
                "to_port": "result",
            },
            {"from": "default-transform", "to": "output", "from_port": "out", "to_port": "in"},
            {
                "from": "default-transform",
                "to": "output",
                "from_port": "result",
                "to_port": "result",
            },
        ],
        "input": {"message": "urgent incident"},
    }

    response = client.post("/graph/runs", json=graph, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["node_results"]["priority-transform"]["result"]["lane"] == "priority"
    assert body["node_results"]["default-transform"]["skipped"] is True
    assert body["node_results"]["output"]["published"]["payload"] == {
        "lane": "priority",
        "message": "urgent incident",
    }


def test_observability_trace_requires_builder_access_and_reports_saved_run_metrics() -> None:
    run_id = str(uuid4())
    graph = _sample_graph()
    original_require_auth = store.platform_settings.require_authenticated_requests

    store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id,
        title="Observability Coverage Run",
        status="Done",
        updatedAt="2026-04-06T00:00:00Z",
        progressLabel="Completed",
        kind="workflow",
    )
    store.run_details[run_id] = {
        "status": "Done",
        "response_text": "Summarized the workflow outcome for observability.",
        "graph": graph,
    }
    store.run_events[run_id] = [
        main_module.WorkflowRunEvent(
            id=f"{run_id}-user",
            type="user_message",
            title="You",
            summary="Start the workflow.",
            createdAt="2026-04-06T00:00:00Z",
        ),
        main_module.WorkflowRunEvent(
            id=f"{run_id}-agent",
            type="agent_message",
            title="Assistant",
            summary="Workflow complete.",
            createdAt="2026-04-06T00:00:10Z",
        ),
        main_module.WorkflowRunEvent(
            id=f"{run_id}-guardrail",
            type="guardrail_result",
            title="Guardrail",
            summary="Output approved.",
            createdAt="2026-04-06T00:00:11Z",
        ),
        main_module.WorkflowRunEvent(
            id=f"{run_id}-artifact",
            type="artifact_created",
            title="Artifact",
            summary="Saved summary artifact.",
            createdAt="2026-04-06T00:00:12Z",
        ),
    ]

    try:
        store.platform_settings.require_authenticated_requests = True

        unauthorized = client.get(f"/observability/runs/{run_id}/trace")
        assert unauthorized.status_code == 401

        header_only = client.get(
            f"/observability/runs/{run_id}/trace",
            headers={"x-frontier-actor": "tester"},
        )
        assert header_only.status_code == 401

        authorized = client.get(f"/observability/runs/{run_id}/trace", headers=ADMIN_HEADERS)
        assert authorized.status_code == 200

        body = authorized.json()
        assert body["run_id"] == run_id
        assert body["status"] == "Done"
        assert body["event_count"] == 4
        assert body["node_count"] == len(graph["nodes"])
        assert body["edge_count"] == len(graph["links"])
        assert body["duration_ms"] > 0
        assert body["token_estimate"] > 0
        assert body["cost_estimate_usd"] > 0
        assert set(body["latency_by_stage_ms"].keys()) == {
            "ingest",
            "model",
            "guardrail",
            "artifact",
        }
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.runs.pop(run_id, None)
        store.run_details.pop(run_id, None)
        store.run_events.pop(run_id, None)


def test_workflow_run_stream_returns_events_in_order() -> None:
    run_id = str(uuid4())

    try:
        _seed_streamable_run(run_id)
        store.run_streams[run_id] = [
            {"event": "started", "sequence": 1},
            {"event": "agent_message", "sequence": 2},
            {"event": "completed", "sequence": 3},
        ]
        store.run_stream_complete[run_id] = True

        payloads = _stream_payloads(run_id, headers=AUTH_HEADERS)

        assert [payload["sequence"] for payload in payloads] == [1, 2, 3]
        assert [payload["event"] for payload in payloads] == [
            "started",
            "agent_message",
            "completed",
        ]
    finally:
        _clear_streamable_run(run_id)


def test_workflow_run_events_return_full_chat_content_for_truncated_agent_messages() -> None:
    run_id = str(uuid4())
    full_response = "## Core idea\n\n- Ask for evidence\n- Keep the full markdown response visible\n\n```text\nshow the full response\n```"

    try:
        _seed_streamable_run(run_id)
        store.run_details[run_id]["response_text"] = full_response
        store.run_events[run_id] = [
            main_module.WorkflowRunEvent(
                id=f"{run_id}-user",
                type="user_message",
                title="You",
                summary="Need the complete answer.",
                content="Need the complete answer.",
                createdAt="2026-04-06T00:00:00Z",
            ),
            main_module.WorkflowRunEvent(
                id=f"{run_id}-agent",
                type="agent_message",
                title="Assistant",
                summary="## Core idea\n\n- Ask for evidence…",
                createdAt="2026-04-06T00:00:10Z",
                metadata={"summary_truncated": True},
            ),
        ]

        response = client.get(f"/workflow-runs/{run_id}/events", headers=AUTH_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body[0]["content"] == "Need the complete answer."
        assert body[1]["summary"] == "## Core idea\n\n- Ask for evidence…"
        assert body[1]["content"] == full_response
    finally:
        _clear_streamable_run(run_id)


def test_workflow_run_events_reconstruct_legacy_chat_history_when_messages_are_missing() -> None:
    run_id = str(uuid4())
    prompt_text = "Review the outstanding blockers for the incident."
    response_text = "## Incident summary\n\n- Database latency remains elevated\n- Customer impact is limited to one region"

    try:
        _seed_streamable_run(run_id)
        store.run_events[run_id] = [
            main_module.WorkflowRunEvent(
                id=f"{run_id}-started",
                type="step_started",
                title="Run started",
                summary="Workflow execution started.",
                createdAt="2026-04-06T00:00:00Z",
                metadata={"payload": {"prompt": prompt_text}},
            )
        ]
        store.run_details[run_id] = {
            **store.run_details[run_id],
            "response_text": response_text,
            "agent_traces": [
                {
                    "agent": "Incident Responder",
                    "reasoningSummary": "Recovered from legacy detail payload.",
                    "actions": ["Parsed kickoff payload"],
                    "output": response_text,
                }
            ],
        }

        response = client.get(f"/workflow-runs/{run_id}/events", headers=AUTH_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body[0]["type"] == "user_message"
        assert body[0]["content"] == prompt_text
        assert body[1]["type"] == "agent_message"
        assert body[1]["title"] == "Incident Responder"
        assert body[1]["content"] == response_text
        assert any(event.type == "user_message" for event in store.run_events[run_id])
        assert any(event.type == "agent_message" for event in store.run_events[run_id])
    finally:
        _clear_streamable_run(run_id)


def test_workflow_run_stream_replays_history_on_reconnect() -> None:
    run_id = str(uuid4())

    try:
        _seed_streamable_run(run_id)
        store.run_streams[run_id] = [{"event": "started", "sequence": 1}]
        store.run_stream_complete[run_id] = True

        first_payloads = _stream_payloads(run_id, headers=AUTH_HEADERS)

        store.run_streams[run_id].append({"event": "completed", "sequence": 2})

        second_payloads = _stream_payloads(run_id, headers=AUTH_HEADERS)

        assert [payload["sequence"] for payload in first_payloads] == [1]
        assert [payload["sequence"] for payload in second_payloads] == [1, 2]
    finally:
        _clear_streamable_run(run_id)


def test_workflow_run_stream_returns_empty_body_when_completed_without_events() -> None:
    run_id = str(uuid4())

    try:
        _seed_streamable_run(run_id)
        store.run_stream_complete[run_id] = True

        response = client.get(f"/workflow-runs/{run_id}/stream", headers=AUTH_HEADERS)

        assert response.status_code == 200
        assert response.text == ""
    finally:
        _clear_streamable_run(run_id)


def test_workflow_run_stream_requires_authenticated_access() -> None:
    run_id = str(uuid4())
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        _seed_streamable_run(run_id, owner="owner-user")
        store.run_stream_complete[run_id] = True
        store.platform_settings.require_authenticated_requests = True

        unauthorized = client.get(f"/workflow-runs/{run_id}/stream")
        assert unauthorized.status_code == 401

        header_only = client.get(
            f"/workflow-runs/{run_id}/stream",
            headers={"x-frontier-actor": "owner-user"},
        )
        assert header_only.status_code == 401

        foreign_actor = client.get(f"/workflow-runs/{run_id}/stream", headers=AUTH_HEADERS)
        assert foreign_actor.status_code == 403

        owner_response = client.get(f"/workflow-runs/{run_id}/stream", headers=OWNER_AUTH_HEADERS)
        assert owner_response.status_code == 200

        admin_response = client.get(f"/workflow-runs/{run_id}/stream", headers=ADMIN_HEADERS)
        assert admin_response.status_code == 200
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        _clear_streamable_run(run_id)
