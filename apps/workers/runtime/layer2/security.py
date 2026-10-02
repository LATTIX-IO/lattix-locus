from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from .contracts import Envelope
from .reporting import add_security_event


_MEMORY_SCOPE_PREFIXES = {
    "run": "run:",
    "session": "session:",
    "user": "user:",
    "tenant": "tenant:",
    "agent": "agent:",
    "workflow": "workflow:",
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_profile() -> str:
    value = str(os.getenv("LOCUS_RUNTIME_PROFILE", "local-lightweight") or "").strip().lower()
    return value or "local-lightweight"


def strict_runtime_profile() -> bool:
    return _runtime_profile() in {"local-secure", "hosted"} or _env_flag(
        "LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", False
    )


def trusted_runtime_subjects() -> set[str]:
    baseline = {"backend", "orchestrator", "research", "code", "review", "coordinator"}
    configured = {
        str(item).strip()
        for item in str(os.getenv("A2A_TRUSTED_SUBJECTS") or "").split(",")
        if str(item).strip()
    }
    return baseline | configured


def _claim_as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


@dataclass(frozen=True)
class RuntimeAuthContext:
    actor: str
    tenant_id: str = ""
    subject: str = ""
    internal_service: bool = False
    session_id: str = ""


def _security_events_count(env: Envelope) -> int:
    payload = env.payload if isinstance(env.payload, dict) else {}
    events = (
        payload.get("security_events") if isinstance(payload.get("security_events"), list) else []
    )
    return len(events)


def _mark_security_block(env: Envelope, *, reason: str, classification: str) -> None:
    if not isinstance(env.payload, dict):
        env.payload = {}
    env.payload["_security_blocked"] = True
    env.payload["_security_block_reason"] = str(reason or classification).strip() or classification
    env.payload["_security_block_classification"] = classification


def resolve_runtime_auth_context(env: Envelope) -> RuntimeAuthContext:
    payload = env.payload if isinstance(env.payload, dict) else {}
    auth_context = (
        payload.get("auth_context") if isinstance(payload.get("auth_context"), dict) else {}
    )

    actor = str(
        auth_context.get("actor")
        or payload.get("currentUser")
        or payload.get("current_user")
        or payload.get("user_id")
        or payload.get("actor")
        or ""
    ).strip()
    tenant_id = str(
        auth_context.get("tenant_id")
        or payload.get("currentTenant")
        or payload.get("current_tenant")
        or payload.get("tenant_id")
        or ""
    ).strip()
    subject = str(auth_context.get("subject") or env.sender or "").strip()
    session_id = str(
        auth_context.get("session_id")
        or payload.get("sessionId")
        or payload.get("session_id")
        or ""
    ).strip()
    internal_service = _claim_as_bool(auth_context.get("internal_service"))

    resolved = RuntimeAuthContext(
        actor=actor or subject or "anonymous",
        tenant_id=tenant_id,
        subject=subject or actor or "unknown",
        internal_service=internal_service,
        session_id=session_id,
    )
    payload["auth_context"] = asdict(resolved)
    return resolved


def _normalize_memory_scope_name(scope: str | None, *, allow_global: bool = True) -> str:
    normalized = str(scope or "session").strip().lower() or "session"
    allowed_scopes = set(_MEMORY_SCOPE_PREFIXES.keys())
    if allow_global:
        allowed_scopes.add("global")
    if normalized not in allowed_scopes:
        raise ValueError(f"unsupported memory scope '{normalized}'")
    return normalized


def _normalize_memory_bucket(scope: str, bucket_id: str | None) -> str:
    normalized_scope = _normalize_memory_scope_name(scope)
    bucket = str(bucket_id or "").strip()
    if not bucket:
        raise ValueError("memory bucket id is required")
    if normalized_scope == "global":
        return "global"
    prefix = _MEMORY_SCOPE_PREFIXES[normalized_scope]
    return bucket if bucket.startswith(prefix) else f"{prefix}{bucket}"


def _extract_memory_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("memory_request", "memory"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate.get("scope"):
            return candidate
    if payload.get("memory_scope") or payload.get("scope"):
        return {
            "action": payload.get("memory_action") or payload.get("action") or "read",
            "scope": payload.get("memory_scope") or payload.get("scope"),
            "bucket_id": payload.get("memory_bucket") or payload.get("bucket_id"),
            "session_id": payload.get("session_id") or payload.get("sessionId"),
            "user_id": payload.get("user_id") or payload.get("currentUser"),
            "tenant_id": payload.get("tenant_id") or payload.get("currentTenant"),
            "agent_id": payload.get("agent_id"),
            "workflow_id": payload.get("workflow_id"),
        }
    return None


def _extract_payload_tenant_context(payload: dict[str, Any]) -> set[str]:
    candidates = {
        str(payload.get("tenant_id") or "").strip(),
        str(payload.get("currentTenant") or "").strip(),
        str(payload.get("current_tenant") or "").strip(),
    }
    return {item for item in candidates if item}


def _bucket_from_memory_request(
    memory_request: dict[str, Any], auth: RuntimeAuthContext
) -> tuple[str, str]:
    scope = _normalize_memory_scope_name(memory_request.get("scope"))
    bucket_id = memory_request.get("bucket_id") or memory_request.get("memory_bucket")
    if not bucket_id:
        if scope == "session":
            bucket_id = auth.session_id or auth.actor
        elif scope == "user":
            bucket_id = auth.actor
        elif scope == "tenant":
            bucket_id = auth.tenant_id
        elif scope == "agent":
            bucket_id = memory_request.get("agent_id")
        elif scope == "workflow":
            bucket_id = memory_request.get("workflow_id")
        elif scope == "run":
            bucket_id = auth.session_id
        elif scope == "global":
            bucket_id = "global"
    return _normalize_memory_bucket(scope, bucket_id), scope


def authorize_memory_request(
    auth: RuntimeAuthContext, payload: dict[str, Any], *, env: Envelope | None = None
) -> None:
    memory_request = _extract_memory_request(payload)
    if not memory_request:
        return

    bucket_id, scope = _bucket_from_memory_request(memory_request, auth)

    def _deny(reason: str) -> None:
        if env is not None:
            add_security_event(
                env,
                "blocked",
                "memory_authorization",
                reason=reason,
                auth_context=asdict(auth),
                metadata={
                    "scope": scope,
                    "bucket_id": bucket_id,
                    "action": str(memory_request.get("action") or "read").strip() or "read",
                },
            )
        raise ValueError(reason)

    if scope == "session":
        expected = auth.session_id or auth.actor
        if not expected:
            _deny("session-scoped memory requires session or actor identity")
        allowed_buckets = {
            f"session:{expected}",
            expected if str(expected).startswith("session:") else "",
        }
        if bucket_id not in {item for item in allowed_buckets if item}:
            _deny("session-scoped memory access denied")
    elif scope == "user":
        if not auth.actor or bucket_id != f"user:{auth.actor}":
            _deny("user-scoped memory access denied")
    elif scope == "tenant":
        if not auth.tenant_id or bucket_id != f"tenant:{auth.tenant_id}":
            _deny("tenant-scoped memory access denied")
    elif scope in {"run", "global"}:
        if not auth.internal_service:
            _deny(f"{scope}-scoped memory requires internal service identity")
    elif scope in {"agent", "workflow"}:
        if not auth.internal_service and not auth.actor:
            _deny(
                f"{scope}-scoped memory requires authenticated actor or internal service identity"
            )

    memory_request["bucket_id"] = bucket_id
    payload.setdefault("memory_authorization", {})["bucket_id"] = bucket_id
    payload["memory_authorization"]["scope"] = scope
    if env is not None:
        add_security_event(
            env,
            "allowed",
            "memory_authorization",
            auth_context=asdict(auth),
            metadata={
                "scope": scope,
                "bucket_id": bucket_id,
                "action": str(memory_request.get("action") or "read").strip() or "read",
            },
        )


def enforce_payload_tenant_isolation(
    auth: RuntimeAuthContext, payload: dict[str, Any], *, env: Envelope | None = None
) -> None:
    asserted_tenants = _extract_payload_tenant_context(payload)
    if not asserted_tenants:
        return

    if len(asserted_tenants) > 1:
        reason = "conflicting tenant context detected in payload"
        if env is not None:
            add_security_event(
                env,
                "blocked",
                "tenant_isolation",
                reason=reason,
                auth_context=asdict(auth),
                metadata={"asserted_tenants": sorted(asserted_tenants)},
            )
        raise ValueError(reason)

    asserted_tenant = next(iter(asserted_tenants))
    if not auth.tenant_id:
        reason = "tenant-scoped runtime payload requires tenant identity"
        if env is not None:
            add_security_event(
                env,
                "blocked",
                "tenant_isolation",
                reason=reason,
                auth_context=asdict(auth),
                metadata={"asserted_tenant": asserted_tenant},
            )
        raise ValueError(reason)
    if asserted_tenant != auth.tenant_id:
        reason = "payload tenant context mismatch"
        if env is not None:
            add_security_event(
                env,
                "blocked",
                "tenant_isolation",
                reason=reason,
                auth_context=asdict(auth),
                metadata={
                    "asserted_tenant": asserted_tenant,
                    "authenticated_tenant": auth.tenant_id,
                },
            )
        raise ValueError(reason)
    if env is not None:
        add_security_event(
            env,
            "allowed",
            "tenant_isolation",
            auth_context=asdict(auth),
            metadata={"tenant_id": asserted_tenant},
        )


def enforce_runtime_envelope_security(env: Envelope) -> RuntimeAuthContext:
    payload = env.payload if isinstance(env.payload, dict) else {}
    auth = resolve_runtime_auth_context(env)

    if strict_runtime_profile():
        if not auth.internal_service:
            add_security_event(
                env,
                "blocked",
                "runtime_identity",
                reason="strict runtime profiles require internal service identity",
                auth_context=asdict(auth),
                metadata={"profile": _runtime_profile()},
            )
            raise ValueError("strict runtime profiles require internal service identity")
        if auth.subject not in trusted_runtime_subjects():
            add_security_event(
                env,
                "blocked",
                "runtime_identity",
                reason=f"untrusted runtime subject '{auth.subject}'",
                auth_context=asdict(auth),
                metadata={"profile": _runtime_profile()},
            )
            raise ValueError(f"untrusted runtime subject '{auth.subject}'")
        add_security_event(
            env,
            "allowed",
            "runtime_identity",
            auth_context=asdict(auth),
            metadata={"profile": _runtime_profile()},
        )

    enforce_payload_tenant_isolation(auth, payload, env=env)
    authorize_memory_request(auth, payload, env=env)
    return auth


def runtime_security_middleware() -> Any:
    def _mw(env: Envelope) -> None:
        before_events = _security_events_count(env)
        try:
            enforce_runtime_envelope_security(env)
        except ValueError as exc:
            if _security_events_count(env) == before_events:
                auth_context = resolve_runtime_auth_context(env)
                add_security_event(
                    env,
                    "blocked",
                    "runtime_security_middleware",
                    reason=str(exc),
                    auth_context=asdict(auth_context),
                    metadata={"classification": "policy_denied", "profile": _runtime_profile()},
                )
            _mark_security_block(env, reason=str(exc), classification="policy_denied")
            env.errors.append(f"security policy: {exc}")
        except Exception as exc:  # noqa: BLE001
            auth_context = resolve_runtime_auth_context(env)
            add_security_event(
                env,
                "error",
                "runtime_security_middleware",
                reason="unexpected security middleware failure",
                auth_context=asdict(auth_context),
                metadata={
                    "classification": "security_error",
                    "exception_type": exc.__class__.__name__,
                    "error": str(exc),
                    "profile": _runtime_profile(),
                },
            )
            _mark_security_block(env, reason=str(exc), classification="security_error")
            env.errors.append(f"security middleware error: {exc}")

    return _mw
