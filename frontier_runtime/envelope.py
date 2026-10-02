from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from frontier_runtime.cognition import ColumnMessage, MessageType


class EnvelopeStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    BLOCKED = "blocked"


@dataclass
class Envelope:
    source_agent: str
    action: str
    payload: dict[str, Any]
    target_agent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    capability_token: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    status: EnvelopeStatus = EnvelopeStatus.PENDING


def is_cognitive_envelope(envelope: Envelope) -> bool:
    try:
        MessageType(str(envelope.action or "").strip())
    except ValueError:
        return False
    return str(envelope.metadata.get("transport_kind") or "").strip() == "cognitive"


def envelope_from_column_message(
    message: ColumnMessage,
    *,
    source_agent: str = "cognitive-runtime",
    target_agent: str | None = None,
) -> Envelope:
    return Envelope(
        source_agent=source_agent,
        target_agent=target_agent,
        action=message.message_type.value,
        payload={"payload_ref": message.payload_ref},
        metadata={
            "transport_kind": "cognitive",
            "assembly_id": message.assembly_id,
            "source_column": message.source_column,
            "target_column": message.target_column,
            "confidence": message.confidence,
            "created_at": message.created_at,
            **message.metadata,
        },
    )


def envelope_to_column_message(envelope: Envelope) -> ColumnMessage:
    if not is_cognitive_envelope(envelope):
        raise ValueError("Envelope does not carry a cognitive message")

    payload_ref = str(envelope.payload.get("payload_ref") or "").strip()
    assembly_id = str(envelope.metadata.get("assembly_id") or "").strip()
    source_column = str(envelope.metadata.get("source_column") or "").strip()
    target_column = str(envelope.metadata.get("target_column") or "").strip() or None
    confidence = float(envelope.metadata.get("confidence") or 0.0)
    created_at = float(envelope.metadata.get("created_at") or 0.0)

    if not payload_ref:
        raise ValueError("Cognitive envelope payload_ref is required")
    if not assembly_id:
        raise ValueError("Cognitive envelope assembly_id is required")
    if not source_column:
        raise ValueError("Cognitive envelope source_column is required")

    cognitive_metadata = {
        key: value
        for key, value in envelope.metadata.items()
        if key
        not in {
            "transport_kind",
            "assembly_id",
            "source_column",
            "target_column",
            "confidence",
            "created_at",
        }
    }

    return ColumnMessage(
        message_type=MessageType(envelope.action),
        assembly_id=assembly_id,
        source_column=source_column,
        target_column=target_column,
        payload_ref=payload_ref,
        confidence=confidence,
        metadata=cognitive_metadata,
        created_at=created_at,
    )


def envelope_to_json(envelope: Envelope) -> str:
    payload = asdict(envelope)
    payload["status"] = envelope.status.value
    return json.dumps(payload)


def envelope_from_json(text: str) -> Envelope:
    payload = json.loads(text)
    status = EnvelopeStatus(str(payload.get("status", EnvelopeStatus.PENDING.value)))
    payload["status"] = status
    return Envelope(**payload)
