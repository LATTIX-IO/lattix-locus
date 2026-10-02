from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"

from app import main as main_module
from frontier_runtime.cognition import (
    AssemblyDefinition,
    AssemblyState,
    ColumnKind,
    ColumnMessage,
    ColumnState,
    MessageType,
)
from frontier_runtime.envelope import (
    Envelope,
    envelope_from_column_message,
    envelope_to_column_message,
    is_cognitive_envelope,
)
from frontier_runtime.events import (
    AgentEvent,
    event_from_column_message,
    event_to_column_message,
    get_event_bus,
    is_cognitive_event,
)
from frontier_runtime.persistence import persist_assembly_state, reset_shared_state_backend
from frontier_runtime.security import verify_event_signature


client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def _reset_cognitive_admission_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    reset_shared_state_backend()
    main_module.store.a2a_seen_nonces = {}


def _seed_cognitive_assembly(
    *, assembly_id: str = "assembly-admit-1", tenant_id: str = "tenant-a"
) -> None:
    assembly = AssemblyState(
        definition=AssemblyDefinition(
            assembly_id=assembly_id,
            columns=("evidence-1", "synthesis-1"),
        ),
        column_states={
            "evidence-1": ColumnState(
                column_id="evidence-1",
                assembly_id=assembly_id,
                kind=ColumnKind.EVIDENCE,
            ),
            "synthesis-1": ColumnState(
                column_id="synthesis-1",
                assembly_id=assembly_id,
                kind=ColumnKind.SYNTHESIS,
            ),
        },
    )
    persist_assembly_state(assembly, tenant_id=tenant_id, actor="tester", task="task")


def _signed_cognitive_admission_request(
    *,
    nonce: str,
    assembly_id: str = "assembly-admit-1",
    tenant_id: str = "tenant-a",
    source_column: str = "evidence-1",
    target_column: str = "synthesis-1",
) -> tuple[bytes, dict[str, str]]:
    timestamp = str(int(time.time()))
    message = ColumnMessage(
        message_type=MessageType.BELIEF_UPDATE,
        assembly_id=assembly_id,
        source_column=source_column,
        target_column=target_column,
        payload_ref="belief:evidence-1:admit",
        confidence=0.88,
        metadata={
            "tenant_id": tenant_id,
            "nonce": nonce,
            "timestamp": timestamp,
            "trusted_subject": "backend",
        },
    )
    event = event_from_column_message(message, source="assembly-runtime")
    payload = asdict(event)
    raw = json.dumps(payload).encode("utf-8")
    correlation_id = f"corr-{nonce}"
    headers = {
        "x-frontier-actor": "tester",
        "x-correlation-id": correlation_id,
        "x-frontier-subject": "backend",
        "x-frontier-nonce": nonce,
        "x-frontier-timestamp": timestamp,
        "x-frontier-signature": main_module._build_runtime_signature(
            "backend", nonce, correlation_id, raw, timestamp=timestamp
        ),
        "content-type": "application/json",
    }
    return raw, headers


def test_cognitive_envelope_round_trip() -> None:
    message = ColumnMessage(
        message_type=MessageType.BELIEF_UPDATE,
        assembly_id="assembly-1",
        source_column="evidence-1",
        target_column="synthesis-1",
        payload_ref="belief:evidence-1:42",
        confidence=0.72,
        metadata={"topic": "operational_risk"},
        created_at=123.0,
    )

    envelope = envelope_from_column_message(message, source_agent="assembly-runtime")
    restored = envelope_to_column_message(envelope)

    assert is_cognitive_envelope(envelope) is True
    assert envelope.action == "belief_update"
    assert envelope.payload == {"payload_ref": "belief:evidence-1:42"}
    assert restored == message


def test_cognitive_envelope_rejects_missing_required_fields() -> None:
    envelope = Envelope(
        source_agent="assembly-runtime",
        action="belief_update",
        payload={},
        metadata={
            "transport_kind": "cognitive",
            "assembly_id": "assembly-1",
            "source_column": "goal-1",
        },
    )

    with pytest.raises(ValueError, match="payload_ref"):
        envelope_to_column_message(envelope)


def test_cognitive_event_round_trip() -> None:
    message = ColumnMessage(
        message_type=MessageType.DISSENT,
        assembly_id="assembly-2",
        source_column="evaluation-1",
        target_column="synthesis-1",
        payload_ref="dissent:evaluation-1:7",
        confidence=0.41,
        metadata={"reason": "policy_mismatch"},
        created_at=456.0,
    )

    event = event_from_column_message(message, source="assembly-runtime")
    restored = event_to_column_message(event)

    assert is_cognitive_event(event) is True
    assert event.event_type == "dissent"
    assert restored == message


def test_cognitive_event_rejects_missing_required_fields() -> None:
    event = AgentEvent(
        event_type="commitment",
        source="assembly-runtime",
        payload={
            "transport_kind": "cognitive",
            "assembly_id": "assembly-1",
            "source_column": "synthesis-1",
        },
    )

    with pytest.raises(ValueError, match="payload_ref"):
        event_to_column_message(event)


def test_event_bus_signs_cognitive_events() -> None:
    bus = get_event_bus()
    message = ColumnMessage(
        message_type=MessageType.COMMITMENT,
        assembly_id="assembly-3",
        source_column="synthesis-1",
        payload_ref="commitment:assembly-3:final",
        confidence=0.93,
    )

    event = asyncio.run(bus.publish(event_from_column_message(message)))

    assert is_cognitive_event(event) is True
    assert event.signature is not None
    assert verify_event_signature(event) is True
    assert event_to_column_message(event).message_type == MessageType.COMMITMENT


def test_signed_cognitive_message_admission_accepts_known_column() -> None:
    _seed_cognitive_assembly()
    raw, headers = _signed_cognitive_admission_request(nonce="admit-nonce-1")

    response = client.post("/internal/cognition/messages/admit", content=raw, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "accepted": True,
        "assembly_id": "assembly-admit-1",
        "tenant_id": "tenant-a",
        "source_column": "evidence-1",
        "target_column": "synthesis-1",
        "message_type": "belief_update",
        "payload_ref": "belief:evidence-1:admit",
        "replay_status": "accepted",
        "replay_marker": body["replay_marker"],
    }
    assert body["replay_marker"].startswith("message:")


def test_signed_cognitive_message_admission_rejects_unsigned_message() -> None:
    _seed_cognitive_assembly()
    raw, _headers = _signed_cognitive_admission_request(nonce="admit-nonce-unsigned")

    response = client.post(
        "/internal/cognition/messages/admit",
        content=raw,
        headers={"content-type": "application/json", "x-frontier-actor": "tester"},
    )

    assert response.status_code == 401


def test_signed_cognitive_message_admission_rejects_replayed_nonce() -> None:
    _seed_cognitive_assembly()
    raw, headers = _signed_cognitive_admission_request(nonce="admit-nonce-replay")

    first_response = client.post("/internal/cognition/messages/admit", content=raw, headers=headers)
    replay_response = client.post(
        "/internal/cognition/messages/admit", content=raw, headers=headers
    )

    assert first_response.status_code == 200
    assert replay_response.status_code == 409


def test_signed_cognitive_message_admission_rejects_wrong_tenant() -> None:
    _seed_cognitive_assembly(tenant_id="tenant-a")
    raw, headers = _signed_cognitive_admission_request(
        nonce="admit-nonce-wrong-tenant", tenant_id="tenant-b"
    )

    response = client.post("/internal/cognition/messages/admit", content=raw, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Cognitive message tenant access denied"


def test_signed_cognitive_message_admission_rejects_unknown_source_column() -> None:
    _seed_cognitive_assembly()
    raw, headers = _signed_cognitive_admission_request(
        nonce="admit-nonce-unknown-source", source_column="unknown-evidence"
    )

    response = client.post("/internal/cognition/messages/admit", content=raw, headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown cognitive source column"


def test_signed_cognitive_message_admission_rejects_unknown_target_column() -> None:
    _seed_cognitive_assembly()
    raw, headers = _signed_cognitive_admission_request(
        nonce="admit-nonce-unknown-target", target_column="unknown-synthesis"
    )

    response = client.post("/internal/cognition/messages/admit", content=raw, headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown cognitive target column"
