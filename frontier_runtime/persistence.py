from __future__ import annotations

import json
import os
import hashlib
import re
import time
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any

from frontier_runtime.cognition import (
    AssemblyState,
    BeliefRecord,
    ColumnState,
    Commitment,
    validate_belief_record,
)


_REDACTED_VALUE = "[redacted]"
_REDACTED_REF = "redacted:evidence_ref"
_SENSITIVE_KEY_TOKENS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "bearer",
    "client_secret",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "session_id",
    "token",
)
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)?\s*[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret|client_secret)\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
)


_DEFAULT_STATE: dict[str, Any] = {
    "approvals": [],
    "causal_state": {"assemblies": {}},
    "events": [],
    "replay_tokens": [],
}

_STATE_LOCK = RLock()


def _default_state() -> dict[str, Any]:
    return deepcopy(_DEFAULT_STATE)


def _history_limit(env_key: str, default: int) -> int:
    try:
        value = int(os.getenv(env_key, str(default)))
    except (TypeError, ValueError):
        return default
    return max(1, value)


def _normalize_assembly_id(assembly_id: str) -> str:
    normalized = str(assembly_id or "").strip()
    if not normalized:
        raise ValueError("assembly_id is required")
    return normalized


def _normalize_column_id(column_id: str) -> str:
    normalized = str(column_id or "").strip()
    if not normalized:
        raise ValueError("column_id is required")
    return normalized


def _normalize_outcome(outcome: str) -> str:
    normalized = str(outcome or "").strip()
    if not normalized:
        raise ValueError("outcome is required")
    return normalized


def _normalize_optional_text(value: Any) -> str:
    return str(value or "").strip()


def _key_is_sensitive(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def _redact_sensitive_text(value: str) -> tuple[str, bool]:
    redacted = value
    changed = False
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        updated = pattern.sub(
            lambda match: (
                f"{match.group(1)}{_REDACTED_VALUE}" if match.lastindex else _REDACTED_VALUE
            ),
            redacted,
        )
        if updated != redacted:
            changed = True
            redacted = updated
    return redacted, changed


def _redact_sensitive_value(value: Any, *, parent_key: str = "") -> tuple[Any, bool]:
    if parent_key and _key_is_sensitive(parent_key):
        return _REDACTED_VALUE, True
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        redacted_any = False
        for key, item in value.items():
            key_text = str(key)
            redacted_item, item_redacted = _redact_sensitive_value(item, parent_key=key_text)
            redacted[key_text] = redacted_item
            redacted_any = redacted_any or item_redacted
        if redacted_any:
            redacted["redacted"] = True
        return redacted, redacted_any
    if isinstance(value, list):
        redacted_items: list[Any] = []
        redacted_any = False
        for item in value:
            redacted_item, item_redacted = _redact_sensitive_value(item, parent_key=parent_key)
            redacted_items.append(redacted_item)
            redacted_any = redacted_any or item_redacted
        return redacted_items, redacted_any
    if isinstance(value, tuple):
        redacted_items = []
        redacted_any = False
        for item in value:
            redacted_item, item_redacted = _redact_sensitive_value(item, parent_key=parent_key)
            redacted_items.append(redacted_item)
            redacted_any = redacted_any or item_redacted
        return tuple(redacted_items), redacted_any
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value, False


def redact_sensitive_payload(value: Any) -> Any:
    redacted, _redacted_any = _redact_sensitive_value(value)
    return redacted


def _redact_evidence_refs(values: list[Any]) -> tuple[list[str], bool]:
    refs: list[str] = []
    redacted_any = False
    for item in values:
        ref = str(item or "").strip()
        if not ref:
            continue
        redacted_ref, ref_redacted = _redact_sensitive_text(ref)
        if _key_is_sensitive(ref) or ref_redacted:
            redacted_ref = _REDACTED_REF
            ref_redacted = True
        if redacted_ref not in refs:
            refs.append(redacted_ref)
        redacted_any = redacted_any or ref_redacted
    return refs, redacted_any


def redact_sensitive_ref(value: Any) -> str:
    refs, _redacted_any = _redact_evidence_refs([value])
    return refs[0] if refs else ""


def _redact_metadata(metadata: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    redacted, redacted_any = _redact_sensitive_value(metadata)
    redacted_metadata = dict(redacted) if isinstance(redacted, dict) else {}
    if redacted_any:
        redacted_metadata["redacted"] = True
    return redacted_metadata, redacted_any


def _serialize_belief_record(belief: BeliefRecord) -> dict[str, Any]:
    validate_belief_record(belief)
    value, value_redacted = _redact_sensitive_value(belief.value)
    evidence_refs, refs_redacted = _redact_evidence_refs(list(belief.evidence_refs))
    metadata, metadata_redacted = _redact_metadata(dict(belief.metadata))
    if value_redacted or refs_redacted or metadata_redacted:
        metadata["redacted"] = True
    return {
        "key": belief.key,
        "value": value,
        "confidence": belief.confidence,
        "evidence_refs": evidence_refs,
        "rationale": belief.rationale,
        "metadata": metadata,
    }


def _serialize_column_state(state: ColumnState) -> dict[str, Any]:
    belief_set = [_serialize_belief_record(belief) for belief in state.belief_set]
    evidence_refs, _refs_redacted = _redact_evidence_refs(list(state.evidence_refs))
    adaptation_metrics, _metrics_redacted = _redact_metadata(dict(state.adaptation_metrics))
    return {
        "column_id": state.column_id,
        "assembly_id": state.assembly_id,
        "kind": state.kind.value,
        "belief_set": belief_set,
        "evidence_refs": evidence_refs,
        "confidence": state.confidence,
        "last_updated": state.last_updated,
        "adaptation_metrics": adaptation_metrics,
    }


def _serialize_commitment(commitment: Commitment) -> dict[str, Any]:
    return {
        "decision": commitment.decision,
        "confidence": commitment.confidence,
        "supporting_columns": list(commitment.supporting_columns),
        "dissenting_columns": list(commitment.dissenting_columns),
        "blockers": list(commitment.blockers),
        "next_actions": list(commitment.next_actions),
        "is_ready": commitment.is_ready,
    }


def _normalize_assembly_causal_state(
    assembly_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    assembly_key = _normalize_assembly_id(assembly_id)
    data = payload if isinstance(payload, dict) else {}

    raw_columns_value = data.get("columns")
    raw_columns = raw_columns_value if isinstance(raw_columns_value, dict) else {}
    columns = {
        str(column_id): dict(column_payload)
        for column_id, column_payload in raw_columns.items()
        if str(column_id).strip() and isinstance(column_payload, dict)
    }

    def _normalize_history(raw_history: Any) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(raw_history, dict):
            return {}
        return {
            str(column_id): [dict(item) for item in entries if isinstance(item, dict)]
            for column_id, entries in raw_history.items()
            if str(column_id).strip() and isinstance(entries, list)
        }

    updated_at = data.get("updated_at", 0.0)
    try:
        normalized_updated_at = float(updated_at)
    except (TypeError, ValueError):
        normalized_updated_at = 0.0

    raw_replay_markers_value = data.get("replay_markers")
    raw_replay_markers = (
        raw_replay_markers_value if isinstance(raw_replay_markers_value, dict) else {}
    )
    replay_markers = {
        category: {
            str(marker): dict(marker_payload)
            for marker, marker_payload in raw_markers.items()
            if str(marker).strip() and isinstance(marker_payload, dict)
        }
        for category, raw_markers in raw_replay_markers.items()
        if str(category).strip() and isinstance(raw_markers, dict)
    }

    return {
        "assembly_id": assembly_key,
        "tenant_id": _normalize_optional_text(data.get("tenant_id")),
        "actor": _normalize_optional_text(data.get("actor")),
        "task": _normalize_optional_text(data.get("task")),
        "columns": columns,
        "belief_history": _normalize_history(data.get("belief_history")),
        "confidence_history": _normalize_history(data.get("confidence_history")),
        "outcomes": [dict(item) for item in data.get("outcomes", []) if isinstance(item, dict)],
        "replay_markers": replay_markers,
        "updated_at": normalized_updated_at,
    }


def _normalize_causal_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"assemblies": {}}

    raw_assemblies_value = payload.get("assemblies")
    raw_assemblies = raw_assemblies_value if isinstance(raw_assemblies_value, dict) else {}
    assemblies = {
        str(assembly_id): _normalize_assembly_causal_state(str(assembly_id), assembly_payload)
        for assembly_id, assembly_payload in raw_assemblies.items()
        if str(assembly_id).strip()
    }
    return {"assemblies": assemblies}


def _ensure_causal_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    causal_state = _normalize_causal_state(snapshot.get("causal_state"))
    snapshot["causal_state"] = causal_state
    return causal_state


def _ensure_assembly_bucket(causal_state: dict[str, Any], assembly_id: str) -> dict[str, Any]:
    assembly_key = _normalize_assembly_id(assembly_id)
    assemblies = causal_state.setdefault("assemblies", {})
    if not isinstance(assemblies, dict):
        assemblies = {}
        causal_state["assemblies"] = assemblies
    bucket = _normalize_assembly_causal_state(assembly_key, assemblies.get(assembly_key))
    assemblies[assembly_key] = bucket
    return bucket


def _ensure_history_bucket(
    history_by_column: dict[str, Any], column_id: str
) -> list[dict[str, Any]]:
    column_key = _normalize_column_id(column_id)
    history = history_by_column.get(column_key)
    if not isinstance(history, list):
        history = []
        history_by_column[column_key] = history
    return history


def _append_bounded(history: list[dict[str, Any]], entry: dict[str, Any], *, limit: int) -> None:
    history.append(entry)
    overflow = len(history) - max(1, limit)
    if overflow > 0:
        del history[:overflow]


def _stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replay_marker_bucket(assembly_bucket: dict[str, Any], category: str) -> dict[str, Any]:
    markers = assembly_bucket.get("replay_markers")
    if not isinstance(markers, dict):
        markers = {}
        assembly_bucket["replay_markers"] = markers
    bucket = markers.get(category)
    if not isinstance(bucket, dict):
        bucket = {}
        markers[category] = bucket
    return bucket


def _outcome_replay_key(
    *,
    assembly_id: str,
    outcome: str,
    commitment: Commitment | None,
    metadata: dict[str, Any] | None,
) -> str:
    payload: dict[str, Any] = {
        "assembly_id": _normalize_assembly_id(assembly_id),
        "outcome": _normalize_outcome(outcome),
        "metadata": redact_sensitive_payload(dict(metadata or {})),
    }
    if commitment is not None:
        payload["commitment"] = _serialize_commitment(commitment)
    explicit = str(
        payload["metadata"].get("replay_key") or payload["metadata"].get("idempotency_key") or ""
    ).strip()
    return explicit or f"outcome:{_stable_payload_hash(payload)}"


def _upsert_column_causal_state(
    assembly_bucket: dict[str, Any],
    state: ColumnState,
    *,
    cause: dict[str, Any] | None = None,
) -> None:
    serialized_state = _serialize_column_state(state)
    cause_payload = redact_sensitive_payload(dict(cause or {}))
    column_id = _normalize_column_id(state.column_id)

    assembly_bucket["columns"][column_id] = serialized_state
    _append_bounded(
        _ensure_history_bucket(assembly_bucket["belief_history"], column_id),
        {
            "at": state.last_updated,
            "confidence": state.confidence,
            "belief_set": serialized_state["belief_set"],
            "evidence_refs": list(serialized_state["evidence_refs"]),
            "cause": cause_payload,
        },
        limit=_history_limit("FRONTIER_CAUSAL_BELIEF_HISTORY_LIMIT", 128),
    )
    _append_bounded(
        _ensure_history_bucket(assembly_bucket["confidence_history"], column_id),
        {
            "at": state.last_updated,
            "confidence": state.confidence,
            "adaptation_metrics": dict(serialized_state["adaptation_metrics"]),
            "cause": cause_payload,
        },
        limit=_history_limit("FRONTIER_CAUSAL_CONFIDENCE_HISTORY_LIMIT", 256),
    )
    assembly_bucket["updated_at"] = max(
        float(assembly_bucket.get("updated_at") or 0.0), float(state.last_updated)
    )


def _append_assembly_outcome(
    assembly_bucket: dict[str, Any],
    *,
    assembly_id: str,
    outcome: str,
    commitment: Commitment | None = None,
    metadata: dict[str, Any] | None = None,
    recorded_at: float | None = None,
) -> bool:
    marker_key = _outcome_replay_key(
        assembly_id=assembly_id,
        outcome=outcome,
        commitment=commitment,
        metadata=metadata,
    )
    outcome_markers = _replay_marker_bucket(assembly_bucket, "commitment_outcomes")
    if marker_key in outcome_markers:
        return False

    outcome_payload: dict[str, Any] = {
        "outcome": _normalize_outcome(outcome),
        "metadata": redact_sensitive_payload(dict(metadata or {})),
    }
    if recorded_at is not None:
        outcome_payload["at"] = float(recorded_at)
    if commitment is not None:
        outcome_payload["commitment"] = _serialize_commitment(commitment)

    _append_bounded(
        assembly_bucket["outcomes"],
        outcome_payload,
        limit=_history_limit("FRONTIER_CAUSAL_OUTCOME_HISTORY_LIMIT", 64),
    )
    outcome_markers[marker_key] = {
        "assembly_id": _normalize_assembly_id(assembly_id),
        "outcome": _normalize_outcome(outcome),
        "recorded_at": float(recorded_at if recorded_at is not None else time.time()),
        "metadata": redact_sensitive_payload(dict(metadata or {})),
    }

    if recorded_at is not None:
        assembly_bucket["updated_at"] = max(
            float(assembly_bucket.get("updated_at") or 0.0), float(recorded_at)
        )
    return True


def _set_assembly_owner_fields(
    assembly_bucket: dict[str, Any],
    *,
    tenant_id: str | None = None,
    actor: str | None = None,
    task: str | None = None,
) -> None:
    for key, value in (
        ("tenant_id", tenant_id),
        ("actor", actor),
        ("task", task),
    ):
        normalized = _normalize_optional_text(redact_sensitive_payload(value))
        if normalized:
            assembly_bucket[key] = normalized


def state_path() -> Path:
    configured = str(os.getenv("FRONTIER_STATE_STORE", ".frontier/runtime-state.json")).strip()
    return Path(configured)


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return _default_state()
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(raw_data, dict):
        return _default_state()
    data: dict[str, Any] = dict(raw_data)
    for key, default in _DEFAULT_STATE.items():
        if key not in data:
            data[key] = deepcopy(default)
    return data


def load_causal_state() -> dict[str, Any]:
    return _normalize_causal_state(load_state().get("causal_state"))


def load_assembly_causal_state(assembly_id: str) -> dict[str, Any]:
    assembly_key = _normalize_assembly_id(assembly_id)
    causal_state = load_causal_state()
    raw_assembly = causal_state.get("assemblies", {}).get(assembly_key)
    return _normalize_assembly_causal_state(assembly_key, raw_assembly)


def save_state(state: dict[str, Any]) -> None:
    with _STATE_LOCK:
        _save_state_unlocked(state)


def _save_state_unlocked(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, indent=2, sort_keys=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = handle.name
        os.replace(temp_path, path)
    finally:
        if temp_path:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass


def mutate_state(mutator: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        state = load_state()
        mutator(state)
        _save_state_unlocked(state)
        return state


def persist_column_state(
    state: ColumnState,
    *,
    cause: dict[str, Any] | None = None,
    tenant_id: str | None = None,
    actor: str | None = None,
    task: str | None = None,
) -> dict[str, Any]:
    assembly_snapshot: dict[str, Any] = {}

    def _mutate(snapshot: dict[str, Any]) -> None:
        nonlocal assembly_snapshot
        causal_state = _ensure_causal_state(snapshot)
        assembly_bucket = _ensure_assembly_bucket(causal_state, state.assembly_id)
        _set_assembly_owner_fields(
            assembly_bucket,
            tenant_id=tenant_id,
            actor=actor,
            task=task,
        )
        _upsert_column_causal_state(assembly_bucket, state, cause=cause)
        assembly_snapshot = _normalize_assembly_causal_state(state.assembly_id, assembly_bucket)

    mutate_state(_mutate)
    return assembly_snapshot


def record_assembly_outcome(
    assembly_id: str,
    *,
    outcome: str,
    commitment: Commitment | None = None,
    metadata: dict[str, Any] | None = None,
    recorded_at: float | None = None,
    tenant_id: str | None = None,
    actor: str | None = None,
    task: str | None = None,
) -> dict[str, Any]:
    assembly_key = _normalize_assembly_id(assembly_id)
    assembly_snapshot: dict[str, Any] = {}

    def _mutate(snapshot: dict[str, Any]) -> None:
        nonlocal assembly_snapshot
        causal_state = _ensure_causal_state(snapshot)
        assembly_bucket = _ensure_assembly_bucket(causal_state, assembly_key)
        _set_assembly_owner_fields(
            assembly_bucket,
            tenant_id=tenant_id,
            actor=actor,
            task=task,
        )
        _append_assembly_outcome(
            assembly_bucket,
            assembly_id=assembly_key,
            outcome=outcome,
            commitment=commitment,
            metadata=metadata,
            recorded_at=recorded_at,
        )
        assembly_snapshot = _normalize_assembly_causal_state(assembly_key, assembly_bucket)

    mutate_state(_mutate)
    return assembly_snapshot


def persist_assembly_state(
    assembly_state: AssemblyState,
    *,
    cause: dict[str, Any] | None = None,
    outcome: str | None = None,
    outcome_metadata: dict[str, Any] | None = None,
    recorded_at: float | None = None,
    tenant_id: str | None = None,
    actor: str | None = None,
    task: str | None = None,
) -> dict[str, Any]:
    assembly_id = assembly_state.definition.assembly_id
    assembly_snapshot: dict[str, Any] = {}

    def _mutate(snapshot: dict[str, Any]) -> None:
        nonlocal assembly_snapshot
        causal_state = _ensure_causal_state(snapshot)
        assembly_bucket = _ensure_assembly_bucket(causal_state, assembly_id)
        _set_assembly_owner_fields(
            assembly_bucket,
            tenant_id=tenant_id,
            actor=actor,
            task=task,
        )
        for column_state in assembly_state.column_states.values():
            _upsert_column_causal_state(assembly_bucket, column_state, cause=cause)
        if assembly_state.commitment is not None or outcome is not None:
            _append_assembly_outcome(
                assembly_bucket,
                assembly_id=assembly_id,
                outcome=outcome or "commitment_ready",
                commitment=assembly_state.commitment,
                metadata=outcome_metadata,
                recorded_at=recorded_at,
            )
        assembly_snapshot = _normalize_assembly_causal_state(assembly_id, assembly_bucket)

    mutate_state(_mutate)
    return assembly_snapshot


def record_cognitive_message_replay_marker(
    assembly_id: str,
    *,
    tenant_id: str,
    source_column: str,
    target_column: str,
    message_type: str,
    payload_ref: str,
    nonce: str,
    timestamp: str,
) -> dict[str, Any]:
    assembly_key = _normalize_assembly_id(assembly_id)
    source_key = _normalize_column_id(source_column)
    target_key = _normalize_column_id(target_column)
    marker_payload = {
        "assembly_id": assembly_key,
        "tenant_id": _normalize_optional_text(tenant_id),
        "source_column": source_key,
        "target_column": target_key,
        "message_type": _normalize_optional_text(message_type),
        "payload_ref": _normalize_optional_text(payload_ref),
        "nonce": _normalize_optional_text(nonce),
        "timestamp": _normalize_optional_text(timestamp),
    }
    semantic_marker_payload = {
        key: marker_payload[key]
        for key in [
            "assembly_id",
            "tenant_id",
            "source_column",
            "target_column",
            "message_type",
            "payload_ref",
        ]
    }
    marker_key = f"message:{_stable_payload_hash(semantic_marker_payload)}"
    result: dict[str, Any] = {}

    def _mutate(snapshot: dict[str, Any]) -> None:
        nonlocal result
        causal_state = _ensure_causal_state(snapshot)
        assembly_bucket = _ensure_assembly_bucket(causal_state, assembly_key)
        markers = _replay_marker_bucket(assembly_bucket, "cognitive_messages")
        if marker_key in markers:
            result = {
                "accepted": False,
                "replay_status": "duplicate",
                "marker": marker_key,
                "assembly": _normalize_assembly_causal_state(assembly_key, assembly_bucket),
            }
            return
        markers[marker_key] = {**marker_payload, "recorded_at": time.time()}
        result = {
            "accepted": True,
            "replay_status": "accepted",
            "marker": marker_key,
            "assembly": _normalize_assembly_causal_state(assembly_key, assembly_bucket),
        }

    mutate_state(_mutate)
    return result


def reset_shared_state_backend() -> None:
    """Reset in-memory handles while preserving persisted state."""
    return None
