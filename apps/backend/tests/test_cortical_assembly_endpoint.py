from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from frontier_runtime.cognition import Commitment
from frontier_runtime.persistence import (
    load_assembly_causal_state,
    record_assembly_outcome,
    reset_shared_state_backend,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_generated_artifacts import (  # noqa: E402
    AUTH_HEADERS,
    _signed_internal_headers,
    client,
    main_module,
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


def _post_internal_assembly(payload: dict[str, object], *, nonce: str) -> object:
    raw = json.dumps(payload).encode("utf-8")
    return client.post(
        "/internal/cognition/assemblies/run",
        content=raw,
        headers=_signed_internal_headers(payload=raw, nonce=nonce),
    )


def _post_signed_cognitive_message(
    *,
    assembly_id: str,
    tenant_id: str,
    nonce: str,
    payload_ref: str,
    source_column: str = "evidence",
    target_column: str = "synthesis",
    message_type: str = "evidence_claim",
) -> object:
    timestamp = str(int(time.time()))
    payload = {
        "event_type": message_type,
        "source": "assembly-runtime",
        "payload": {
            "transport_kind": "cognitive",
            "assembly_id": assembly_id,
            "source_column": source_column,
            "target_column": target_column,
            "payload_ref": payload_ref,
            "confidence": 0.88,
            "created_at": 123.0,
            "tenant_id": tenant_id,
            "nonce": nonce,
            "timestamp": timestamp,
            "trusted_subject": "backend",
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    return client.post(
        "/internal/cognition/messages/admit",
        content=raw,
        headers=_signed_internal_headers(payload=raw, nonce=nonce, timestamp=timestamp),
    )


def _runtime_gate_events(assembly_id: str) -> list[object]:
    return [
        event
        for event in main_module.store.audit_events
        if event.action == "cognition.column.runtime_gate"
        and event.metadata.get("assembly_id") == assembly_id
    ]


def _audit_events_for_assembly(assembly_id: str) -> list[object]:
    return [
        event
        for event in reversed(main_module.store.audit_events)
        if event.metadata.get("assembly_id") == assembly_id
    ]


def _first_action_index(events: list[object], action: str) -> int:
    for index, event in enumerate(events):
        if event.action == action:
            return index
    raise AssertionError(f"missing audit action {action}")


def _payload_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def test_internal_cortical_assembly_endpoint_runs_and_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-1",
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="cortical-assembly-run-nonce",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assembly_id"] == "assembly:endpoint-1"
    assert body["outcome"] == "committed"
    assert body["projected"] == 1
    assert body["projection_status"] == "processed"
    assert body["idempotent_replay"] is False
    projection = main_module._NEO4J_GRAPH.causal_projections["assembly:endpoint-1"]
    assert projection["outcomes"][0]["outcome"] == "committed"


def test_cortical_audit_events_redact_api_keys() -> None:
    reset_shared_state_backend()
    main_module.store.audit_events = []

    main_module._append_audit_event(
        "cognition.slice11.audit",
        "tester",
        "blocked",
        {
            "reason": "policy_denied",
            "api_key": "sk-test-slice11-audit-secret",
            "authorization": "Bearer slice11-audit-token",
            "nested": {"refresh_token": "slice11-refresh-token"},
        },
    )

    audit_payload = main_module.store.audit_events[0].model_dump()
    audit_text = _payload_text(audit_payload)
    assert "sk-test-slice11-audit-secret" not in audit_text
    assert "slice11-audit-token" not in audit_text
    assert "slice11-refresh-token" not in audit_text
    assert audit_payload["metadata"]["api_key"] == "[redacted]"
    assert audit_payload["metadata"]["redacted"] is True


def test_internal_cortical_assembly_endpoint_redacts_auth_headers_from_belief_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:slice11-persist-redaction",
            "tenant_id": "tenant-1",
            "task": "Summarize header Authorization: Bearer slice11-persist-token",
            "context": {
                "Authorization": "Bearer slice11-context-token",
                "api_key": "sk-test-slice11-context-secret",
            },
            "project_graph": False,
        },
        nonce="slice11-persist-redaction",
    )

    assert response.status_code == 200
    restored = load_assembly_causal_state("assembly:slice11-persist-redaction")
    restored_text = _payload_text(restored)
    assert "slice11-persist-token" not in restored_text
    assert "slice11-context-token" not in restored_text
    assert "sk-test-slice11-context-secret" not in restored_text
    assert "context:Authorization" not in restored_text
    goal_belief = restored["columns"]["goal"]["belief_set"][0]
    evidence_belief = restored["columns"]["evidence"]["belief_set"][0]
    assert goal_belief["metadata"]["redacted"] is True
    assert evidence_belief["evidence_refs"] == ["redacted:evidence_ref"]


def test_internal_cortical_assembly_endpoint_redacts_sensitive_payload_from_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:slice11-projection-redaction",
            "tenant_id": "tenant-1",
            "task": "Summarize model output api_key=sk-test-slice11-projection-secret",
            "context": {"Authorization": "Bearer slice11-projection-token"},
        },
        nonce="slice11-projection-redaction",
    )

    assert response.status_code == 200
    projection = main_module._NEO4J_GRAPH.causal_projections[
        "assembly:slice11-projection-redaction"
    ]
    projection_text = _payload_text(projection)
    assert "sk-test-slice11-projection-secret" not in projection_text
    assert "slice11-projection-token" not in projection_text
    assert "context:Authorization" not in projection_text
    assert any(belief.get("redacted") is True for belief in projection["beliefs"])


def test_internal_cortical_assembly_error_response_redacts_raw_sensitive_payload() -> None:
    payload = {
        "task": "Summarize operational risk",
        "confidence_threshold": "api_key=sk-test-slice11-error-secret",
    }
    raw = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/internal/cognition/assemblies/run",
        content=raw,
        headers=_signed_internal_headers(payload=raw, nonce="slice11-error-redaction"),
    )

    assert response.status_code == 400
    response_text = _payload_text(response.json())
    assert "sk-test-slice11-error-secret" not in response_text
    assert "api_key" not in response_text


def test_internal_cortical_assembly_endpoint_runtime_gate_allows_valid_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    main_module.store.audit_events = []

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-runtime-gate-allowed",
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
            "require_tenant_context": True,
        },
        nonce="cortical-assembly-runtime-gate-allowed",
    )

    assert response.status_code == 200
    events = _runtime_gate_events("assembly:endpoint-runtime-gate-allowed")
    assert len(events) == 5
    assert {event.outcome for event in events} == {"allowed"}
    assert {event.metadata["capability"] for event in events} == {
        "read_input",
        "emit_goal_belief",
        "retrieve",
        "score",
        "propose_commitment",
    }


def test_internal_cortical_assembly_endpoint_allowed_execution_emits_audit_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    main_module.store.audit_events = []
    assembly_id = "assembly:slice12-audit-allowed"

    response = _post_internal_assembly(
        {
            "assembly_id": assembly_id,
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
            "require_tenant_context": True,
        },
        nonce="slice12-audit-allowed",
    )

    assert response.status_code == 200
    events = _audit_events_for_assembly(assembly_id)
    actions = [event.action for event in events]
    assert "cognition.assembly.admission" in actions
    assert "cognition.policy.decision" in actions
    assert "cognition.column.started" in actions
    assert "cognition.column.completed" in actions
    assert "cognition.commitment.finalized" in actions
    assert "cognition.causal_graph.project" in actions
    assert "cognition.assembly.run" in actions
    assert _first_action_index(events, "cognition.assembly.admission") < _first_action_index(
        events, "cognition.policy.decision"
    )
    assert _first_action_index(events, "cognition.commitment.finalized") < _first_action_index(
        events, "cognition.assembly.run"
    )

    structured_actions = {
        "cognition.assembly.admission",
        "cognition.policy.decision",
        "cognition.column.started",
        "cognition.column.completed",
        "cognition.commitment.gate",
        "cognition.commitment.finalized",
        "cognition.causal_graph.project",
        "cognition.assembly.run",
    }
    structured_events = [event for event in events if event.action in structured_actions]
    assert structured_events
    for event in structured_events:
        assert event.metadata["tenant_id"] == "tenant-1"
        assert event.metadata["assembly_id"] == assembly_id
        assert event.metadata["actor"] == event.actor
        assert "decision" in event.metadata
        assert event.metadata["reason_code"]

    policy_events = [event for event in events if event.action == "cognition.policy.decision"]
    assert {event.metadata["reason_code"] for event in policy_events} == {"ok"}


def test_internal_cortical_assembly_endpoint_rejected_execution_emits_blocked_audit_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    main_module.store.audit_events = []
    assembly_id = "assembly:slice12-audit-rejected"

    response = _post_internal_assembly(
        {
            "assembly_id": assembly_id,
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
            "assembly_definition": {
                "assembly_id": assembly_id,
                "columns": ["goal", "evidence", "evaluation", "synthesis"],
                "budget": {"max_messages": 3},
            },
        },
        nonce="slice12-audit-rejected",
    )

    assert response.status_code == 403
    events = _audit_events_for_assembly(assembly_id)
    blocked_columns = [event for event in events if event.action == "cognition.column.blocked"]
    assert len(blocked_columns) == 1
    assert blocked_columns[0].metadata["reason_code"] == "column_runtime_budget_exceeded"
    assert blocked_columns[0].metadata["reason"] == "column runtime budget exceeded"
    blocked_admissions = [
        event
        for event in events
        if event.action == "cognition.assembly.admission" and event.outcome == "blocked"
    ]
    assert len(blocked_admissions) == 1
    assert blocked_admissions[0].metadata["decision"] == "rejected"
    assert blocked_admissions[0].metadata["reason_code"] == "column_runtime_budget_exceeded"


def test_internal_cortical_assembly_endpoint_audit_events_do_not_expose_sensitive_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    main_module.store.audit_events = []
    assembly_id = "assembly:slice12-audit-redaction"

    response = _post_internal_assembly(
        {
            "assembly_id": assembly_id,
            "tenant_id": "tenant-1",
            "task": "Summarize Authorization: Bearer slice12-audit-token",
            "context": {"api_key": "sk-test-slice12-audit-secret"},
        },
        nonce="slice12-audit-redaction",
    )

    assert response.status_code == 200
    audit_text = _payload_text([event.model_dump() for event in main_module.store.audit_events])
    assert "slice12-audit-token" not in audit_text
    assert "sk-test-slice12-audit-secret" not in audit_text
    assert "Authorization: Bearer" not in audit_text


def test_internal_cortical_assembly_endpoint_runtime_gate_blocks_over_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    main_module.store.audit_events = []

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-runtime-gate-budget",
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
            "assembly_definition": {
                "assembly_id": "assembly:endpoint-runtime-gate-budget",
                "columns": ["goal", "evidence", "evaluation", "synthesis"],
                "budget": {"max_messages": 3},
            },
        },
        nonce="cortical-assembly-runtime-gate-budget",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "column runtime budget exceeded"
    events = _runtime_gate_events("assembly:endpoint-runtime-gate-budget")
    assert [event.outcome for event in events].count("blocked") == 1
    assert [event.outcome for event in events].count("allowed") == 3
    assert events[0].metadata["reason"] == "column runtime budget exceeded"


def test_internal_cortical_assembly_endpoint_runtime_gate_blocks_missing_tenant_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    main_module.store.audit_events = []

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-runtime-gate-tenant",
            "task": "Summarize operational risk",
            "require_tenant_context": True,
        },
        nonce="cortical-assembly-runtime-gate-tenant",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "tenant context required"
    events = _runtime_gate_events("assembly:endpoint-runtime-gate-tenant")
    assert len(events) == 1
    assert events[0].outcome == "blocked"
    assert events[0].metadata["reason"] == "tenant context required"


def test_internal_cortical_assembly_endpoint_escalates_low_confidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-2",
            "task": "Approve risky deployment",
            "confidence_threshold": 0.8,
        },
        nonce="cortical-assembly-escalate-nonce",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "escalated"
    assert "confidence_below_threshold" in body["commitment"]["blockers"]
    assert body["projected"] == 1
    assert body["projection_status"] == "processed"


def test_internal_cortical_assembly_endpoint_can_skip_graph_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-skip-projection",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
            "project_graph": False,
        },
        nonce="cortical-assembly-skip-projection-nonce",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "committed"
    assert body["projected"] == 0
    assert body["projection_status"] == "skipped"
    assert "assembly:endpoint-skip-projection" not in main_module._NEO4J_GRAPH.causal_projections
    assert (
        load_assembly_causal_state("assembly:endpoint-skip-projection")["outcomes"][-1]["outcome"]
        == "committed"
    )


def test_internal_cortical_assembly_endpoint_parses_string_false_for_graph_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-string-false-projection",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
            "project_graph": "false",
        },
        nonce="cortical-assembly-string-false-projection-nonce",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "committed"
    assert body["projection_status"] == "skipped"
    assert (
        "assembly:endpoint-string-false-projection"
        not in main_module._NEO4J_GRAPH.causal_projections
    )


def test_internal_cortical_assembly_endpoint_persists_when_projection_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    original_enabled = main_module._NEO4J_GRAPH.enabled

    try:
        main_module._NEO4J_GRAPH.enabled = False
        response = _post_internal_assembly(
            {
                "assembly_id": "assembly:endpoint-unavailable-projection",
                "task": "Summarize operational risk",
                "context": {"document": "SOC2 evidence"},
            },
            nonce="cortical-assembly-unavailable-projection-nonce",
        )
    finally:
        main_module._NEO4J_GRAPH.enabled = original_enabled

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "committed"
    assert body["projected"] == 0
    assert body["projection_status"] == "unavailable"
    assert (
        "assembly:endpoint-unavailable-projection"
        not in main_module._NEO4J_GRAPH.causal_projections
    )
    assert (
        load_assembly_causal_state("assembly:endpoint-unavailable-projection")["outcomes"][-1][
            "outcome"
        ]
        == "committed"
    )


def test_internal_cortical_assembly_endpoint_reports_projection_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    def _failed_projection(*, projection: dict[str, object]) -> bool:
        return False

    monkeypatch.setattr(main_module._NEO4J_GRAPH, "project_causal_assembly", _failed_projection)

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-write-failure",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="cortical-assembly-write-failure-nonce",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "committed"
    assert body["projected"] == 0
    assert body["projection_status"] == "write_failed"
    assert "assembly:endpoint-write-failure" not in main_module._NEO4J_GRAPH.causal_projections
    assert (
        load_assembly_causal_state("assembly:endpoint-write-failure")["outcomes"][-1]["outcome"]
        == "committed"
    )


def test_internal_cortical_assembly_endpoint_replays_existing_assembly_id_idempotently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    payload = {
        "assembly_id": "assembly:endpoint-idempotent",
        "tenant_id": "tenant-1",
        "task": "Summarize operational risk",
        "context": {"document": "SOC2 evidence"},
    }

    first_response = _post_internal_assembly(
        payload,
        nonce="cortical-assembly-idempotent-first",
    )
    second_response = _post_internal_assembly(
        payload,
        nonce="cortical-assembly-idempotent-second",
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_body = first_response.json()
    second_body = second_response.json()
    assert first_body["idempotent_replay"] is False
    assert second_body["idempotent_replay"] is True
    assert first_body["replay_status"] == "created"
    assert second_body["replay_status"] == "replayed"
    assert second_body["outcome"] == first_body["outcome"] == "committed"
    restored = load_assembly_causal_state("assembly:endpoint-idempotent")
    assert restored["tenant_id"] == "tenant-1"
    assert restored["task"] == "Summarize operational risk"
    assert len(restored["outcomes"]) == 1


def test_signed_cognitive_message_replay_marker_blocks_duplicate_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    assembly_id = "assembly:message-replay"

    create_response = _post_internal_assembly(
        {
            "assembly_id": assembly_id,
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="message-replay-create",
    )
    assert create_response.status_code == 200
    before = load_assembly_causal_state(assembly_id)
    before_evidence_history_count = len(before["belief_history"]["evidence"])

    accepted = _post_signed_cognitive_message(
        assembly_id=assembly_id,
        tenant_id="tenant-1",
        nonce="message-replay-first",
        payload_ref="belief:evidence:duplicate-safe",
    )
    duplicate = _post_signed_cognitive_message(
        assembly_id=assembly_id,
        tenant_id="tenant-1",
        nonce="message-replay-second",
        payload_ref="belief:evidence:duplicate-safe",
    )

    assert accepted.status_code == 200
    assert accepted.json()["replay_status"] == "accepted"
    assert accepted.json()["replay_marker"].startswith("message:")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Duplicate cognitive message mutation"

    restored = load_assembly_causal_state(assembly_id)
    message_markers = restored["replay_markers"]["cognitive_messages"]
    assert len(message_markers) == 1
    assert len(restored["belief_history"]["evidence"]) == before_evidence_history_count


def test_duplicate_commitment_outcome_replay_marker_prevents_second_append(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    commitment = Commitment(
        decision="approve",
        confidence=0.91,
        supporting_columns=("evidence", "evaluation", "synthesis"),
        dissenting_columns=(),
        blockers=(),
        next_actions=("publish",),
    )

    first = record_assembly_outcome(
        "assembly:commitment-replay",
        outcome="committed",
        commitment=commitment,
        metadata={"replay_key": "commitment:assembly:commitment-replay:final"},
        recorded_at=1000.0,
        tenant_id="tenant-1",
        actor="tester",
        task="Approve deployment",
    )
    second = record_assembly_outcome(
        "assembly:commitment-replay",
        outcome="committed",
        commitment=commitment,
        metadata={"replay_key": "commitment:assembly:commitment-replay:final"},
        recorded_at=1001.0,
        tenant_id="tenant-1",
        actor="tester",
        task="Approve deployment",
    )

    assert len(first["outcomes"]) == 1
    assert len(second["outcomes"]) == 1
    assert second["outcomes"][0]["at"] == 1000.0
    assert len(second["replay_markers"]["commitment_outcomes"]) == 1


def test_internal_cortical_assembly_endpoint_rejects_conflicting_assembly_id_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    first_response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-conflict",
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="cortical-assembly-conflict-first",
    )
    conflict_response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-conflict",
            "tenant_id": "tenant-1",
            "task": "Approve risky deployment",
        },
        nonce="cortical-assembly-conflict-second",
    )

    assert first_response.status_code == 200
    assert conflict_response.status_code == 409
    assert conflict_response.json()["detail"] == "assembly_id already exists for a different task"
    restored = load_assembly_causal_state("assembly:endpoint-conflict")
    assert len(restored["outcomes"]) == 1


def test_internal_cortical_assembly_endpoint_rejects_cross_tenant_assembly_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    first_response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-tenant-conflict",
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="cortical-assembly-tenant-conflict-first",
    )
    conflict_response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-tenant-conflict",
            "tenant_id": "tenant-2",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="cortical-assembly-tenant-conflict-second",
    )

    assert first_response.status_code == 200
    assert conflict_response.status_code == 409
    assert conflict_response.json()["detail"] == "assembly_id already exists for a different tenant"
    restored = load_assembly_causal_state("assembly:endpoint-tenant-conflict")
    assert restored["tenant_id"] == "tenant-1"
    assert len(restored["outcomes"]) == 1


def test_causal_assembly_graph_projection_denies_cross_tenant_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:endpoint-projection-tenant",
            "tenant_id": "tenant-1",
            "task": "Summarize operational risk",
            "context": {"document": "SOC2 evidence"},
        },
        nonce="cortical-assembly-projection-tenant",
    )
    assert response.status_code == 200

    with pytest.raises(HTTPException) as exc_info:
        main_module._run_causal_assembly_graph_projection(
            actor="tester",
            assembly_id="assembly:endpoint-projection-tenant",
            tenant_id="tenant-2",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Assembly tenant access denied"


def test_internal_cortical_assembly_endpoint_requires_signed_internal_access() -> None:
    response = client.post(
        "/internal/cognition/assemblies/run",
        json={"task": "Summarize operational risk"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 401


@pytest.mark.parametrize("profile", ["local-secure", "hosted"])
def test_internal_cortical_assembly_endpoint_requires_signed_access_in_secure_profiles(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setenv("FRONTIER_RUNTIME_PROFILE", profile)

    response = client.post(
        "/internal/cognition/assemblies/run",
        json={"task": "Summarize operational risk"},
        headers={"x-frontier-actor": "tester"},
    )

    assert response.status_code == 401


def test_internal_cortical_assembly_endpoint_rejects_missing_task() -> None:
    response = _post_internal_assembly(
        {"assembly_id": "assembly:missing-task"},
        nonce="cortical-assembly-missing-task",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "task is required"


def test_internal_cortical_assembly_endpoint_rejects_oversized_task() -> None:
    response = _post_internal_assembly(
        {"assembly_id": "assembly:oversized-task", "task": "x" * 8193},
        nonce="cortical-assembly-oversized-task",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "task exceeds max length"


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_detail"),
    [
        ("task", {"nested": "not text"}, "task must be a string"),
        ("assembly_id", ["assembly:bad"], "assembly_id must be a string"),
        ("tenant_id", {"tenant": "bad"}, "tenant_id must be a string"),
    ],
)
def test_internal_cortical_assembly_endpoint_rejects_non_string_text_fields(
    field_name: str, field_value: object, expected_detail: str
) -> None:
    payload: dict[str, object] = {
        "assembly_id": "assembly:typed-text-field",
        "task": "Summarize operational risk",
    }
    payload[field_name] = field_value

    response = _post_internal_assembly(
        payload,
        nonce=f"cortical-assembly-non-string-{field_name}",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_internal_cortical_assembly_endpoint_rejects_oversized_assembly_id() -> None:
    response = _post_internal_assembly(
        {"assembly_id": "a" * 161, "task": "Summarize operational risk"},
        nonce="cortical-assembly-oversized-id",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "assembly_id exceeds max length"


def test_internal_cortical_assembly_endpoint_rejects_non_object_context() -> None:
    response = _post_internal_assembly(
        {"assembly_id": "assembly:bad-context", "task": "Summarize risk", "context": ["bad"]},
        nonce="cortical-assembly-bad-context",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "context must be an object"


def test_internal_cortical_assembly_endpoint_rejects_large_context() -> None:
    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:large-context",
            "task": "Summarize risk",
            "context": {"document": "x" * 65536},
        },
        nonce="cortical-assembly-large-context",
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "context payload too large"


@pytest.mark.parametrize(
    ("confidence_threshold", "expected_detail"),
    [
        ("not-a-number", "confidence_threshold must be a number"),
        (True, "confidence_threshold must be a number"),
        (1.1, "confidence_threshold must be between 0 and 1"),
        ("nan", "confidence_threshold must be between 0 and 1"),
    ],
)
def test_internal_cortical_assembly_endpoint_rejects_invalid_confidence_threshold(
    confidence_threshold: object, expected_detail: str
) -> None:
    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:invalid-confidence-threshold",
            "task": "Summarize risk",
            "confidence_threshold": confidence_threshold,
        },
        nonce=f"cortical-assembly-invalid-confidence-{confidence_threshold}",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_internal_cortical_assembly_endpoint_rejects_invalid_project_graph() -> None:
    response = _post_internal_assembly(
        {
            "assembly_id": "assembly:invalid-project-graph",
            "task": "Summarize risk",
            "project_graph": "sometimes",
        },
        nonce="cortical-assembly-invalid-project-graph",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "project_graph must be a boolean"
