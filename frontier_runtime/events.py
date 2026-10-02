from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from frontier_runtime.cognition import ColumnMessage, MessageType
from frontier_runtime.persistence import load_state, mutate_state
from frontier_runtime.security import sign_event


@dataclass
class AgentEvent:
    event_type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    previous_hash: str | None = None
    event_hash: str | None = None
    signature: str | None = None
    signer: str | None = None


def is_cognitive_event(event: AgentEvent) -> bool:
    try:
        MessageType(str(event.event_type or "").strip())
    except ValueError:
        return False
    return str(event.payload.get("transport_kind") or "").strip() == "cognitive"


def event_from_column_message(
    message: ColumnMessage,
    *,
    source: str = "cognitive-runtime",
) -> AgentEvent:
    return AgentEvent(
        event_type=message.message_type.value,
        source=source,
        payload={
            "transport_kind": "cognitive",
            "assembly_id": message.assembly_id,
            "source_column": message.source_column,
            "target_column": message.target_column,
            "payload_ref": message.payload_ref,
            "confidence": message.confidence,
            "created_at": message.created_at,
            **message.metadata,
        },
    )


def event_to_column_message(event: AgentEvent) -> ColumnMessage:
    if not is_cognitive_event(event):
        raise ValueError("Event does not carry a cognitive message")

    payload_ref = str(event.payload.get("payload_ref") or "").strip()
    assembly_id = str(event.payload.get("assembly_id") or "").strip()
    source_column = str(event.payload.get("source_column") or "").strip()
    target_column = str(event.payload.get("target_column") or "").strip() or None
    confidence = float(event.payload.get("confidence") or 0.0)
    created_at = float(event.payload.get("created_at") or 0.0)

    if not payload_ref:
        raise ValueError("Cognitive event payload_ref is required")
    if not assembly_id:
        raise ValueError("Cognitive event assembly_id is required")
    if not source_column:
        raise ValueError("Cognitive event source_column is required")

    cognitive_metadata = {
        key: value
        for key, value in event.payload.items()
        if key
        not in {
            "transport_kind",
            "assembly_id",
            "source_column",
            "target_column",
            "payload_ref",
            "confidence",
            "created_at",
        }
    }

    return ColumnMessage(
        message_type=MessageType(event.event_type),
        assembly_id=assembly_id,
        source_column=source_column,
        target_column=target_column,
        payload_ref=payload_ref,
        confidence=confidence,
        metadata=cognitive_metadata,
        created_at=created_at,
    )


def cognitive_event_replay_identity(event: AgentEvent) -> dict[str, str]:
    message = event_to_column_message(event)
    tenant_id = str(
        event.payload.get("tenant_id") or message.metadata.get("tenant_id") or ""
    ).strip()
    return {
        "assembly_id": message.assembly_id,
        "tenant_id": tenant_id,
        "source_column": message.source_column,
        "target_column": str(message.target_column or ""),
        "message_type": message.message_type.value,
        "payload_ref": message.payload_ref,
    }


def cognitive_event_replay_key(event: AgentEvent) -> str:
    identity = cognitive_event_replay_identity(event)
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"message:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


class HashChain:
    def __init__(self) -> None:
        self._previous_hash: str | None = None

    def append(self, event: AgentEvent) -> AgentEvent:
        event.previous_hash = self._previous_hash
        event.signer = event.signer or event.source
        event.event_hash = _hash_event(event)
        self._previous_hash = event.event_hash
        return event

    def verify(self, events: list[AgentEvent]) -> tuple[bool, int | None]:
        previous_hash: str | None = None
        for index, event in enumerate(events):
            if event.previous_hash != previous_hash:
                return False, index
            if event.event_hash != _hash_event(event):
                return False, index
            previous_hash = event.event_hash
        return True, None


class FallbackEventStore:
    def list_events(self) -> list[AgentEvent]:
        state = load_state()
        return [AgentEvent(**payload) for payload in state.get("events", [])]


class EventBus:
    def __init__(self) -> None:
        self.fallback = FallbackEventStore()

    async def publish(self, event: AgentEvent) -> AgentEvent:
        await asyncio.sleep(0)
        now = time.time()
        max_events, window_seconds = _event_bus_rate_limit_config()
        rate_limit_details: dict[str, Any] = {}

        def _mutate(snapshot: dict[str, Any]) -> None:
            nonlocal rate_limit_details
            metrics = _event_bus_metrics(snapshot)
            if max_events > 0:
                source = str(event.source or "anonymous").strip() or "anonymous"
                state = snapshot.get("event_rate_limits")
                raw_rate_limits = state if isinstance(state, dict) else {}
                source_timestamps = _normalized_rate_limit_timestamps(
                    raw_rate_limits.get(source),
                    now=now,
                    window_seconds=window_seconds,
                )
                if len(source_timestamps) >= max_events:
                    metrics["rate_limited"] = int(metrics.get("rate_limited", 0)) + 1
                    metrics["last_rate_limited_source"] = source
                    metrics["last_rate_limited_at"] = now
                    snapshot["event_bus_metrics"] = metrics
                    raw_rate_limits[source] = source_timestamps[-max_events:]
                    snapshot["event_rate_limits"] = raw_rate_limits
                    rate_limit_details = {
                        "source": source,
                        "observed": len(source_timestamps),
                        "limit": max_events,
                        "window_seconds": window_seconds,
                    }
                    return
                source_timestamps.append(now)
                raw_rate_limits[source] = source_timestamps[-max_events:]
                snapshot["event_rate_limits"] = raw_rate_limits

            events = list(snapshot.get("events", []))
            event.previous_hash = (
                str(events[-1].get("event_hash") or "") or None if events else None
            )
            event.signer = event.signer or event.source
            event.event_hash = _hash_event(event)
            event.signature = sign_event(event)
            events.append(asdict(event))
            dropped = max(0, len(events) - 5000)
            snapshot["events"] = events[-5000:]
            metrics["published"] = int(metrics.get("published", 0)) + 1
            if dropped:
                metrics["dropped"] = int(metrics.get("dropped", 0)) + dropped
                metrics["last_truncated_at"] = now
            snapshot["event_bus_metrics"] = metrics

        mutate_state(_mutate)
        if rate_limit_details:
            source = str(rate_limit_details.get("source") or "anonymous")
            raise PermissionError(
                f"Event bus publish rate limit exceeded for source '{source}' "
                f"({rate_limit_details['limit']} events/{rate_limit_details['window_seconds']}s)"
            )
        return event


def _event_bus_rate_limit_config() -> tuple[int, int]:
    raw_limit = str(os.getenv("FRONTIER_EVENT_BUS_RATE_LIMIT_COUNT") or "120").strip()
    raw_window = str(os.getenv("FRONTIER_EVENT_BUS_RATE_LIMIT_WINDOW_SECONDS") or "60").strip()
    try:
        max_events = int(raw_limit)
    except (TypeError, ValueError):
        max_events = 120
    try:
        window_seconds = int(raw_window)
    except (TypeError, ValueError):
        window_seconds = 60
    return max(0, max_events), max(1, window_seconds)


def _normalized_rate_limit_timestamps(
    value: Any, *, now: float, window_seconds: int
) -> list[float]:
    if not isinstance(value, list):
        return []
    cutoff = now - max(1, window_seconds)
    normalized: list[float] = []
    for item in value:
        try:
            timestamp = float(item)
        except (TypeError, ValueError):
            continue
        if timestamp >= cutoff:
            normalized.append(timestamp)
    return normalized


def _event_bus_metrics(snapshot: dict[str, Any]) -> dict[str, Any]:
    metrics = snapshot.get("event_bus_metrics")
    return dict(metrics) if isinstance(metrics, dict) else {}


def _hash_event(event: AgentEvent) -> str:
    hasher = hashlib.sha256()
    hasher.update(str(event.previous_hash or "").encode("utf-8"))
    hasher.update(str(event.event_type).encode("utf-8"))
    hasher.update(str(event.source).encode("utf-8"))
    hasher.update(str(event.signer or event.source).encode("utf-8"))
    hasher.update(str(event.payload).encode("utf-8"))
    hasher.update(str(event.created_at).encode("utf-8"))
    return hasher.hexdigest()


_EVENT_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    global _EVENT_BUS
    if _EVENT_BUS is None:
        _EVENT_BUS = EventBus()
    return _EVENT_BUS


def reset_event_bus() -> None:
    global _EVENT_BUS
    _EVENT_BUS = None
