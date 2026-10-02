from __future__ import annotations
import hashlib
import hmac
import json
import os
import ssl
import time
from uuid import uuid4
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from ..layer2.contracts import Envelope
from ..security.jwt import issue_token


def _runtime_profile() -> str:
    value = str(os.getenv("LOCUS_RUNTIME_PROFILE", "local-lightweight") or "").strip().lower()
    return value or "local-lightweight"


def _strict_transport_required() -> bool:
    return _runtime_profile() in {"local-secure", "hosted"} or _env_flag(
        "LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", False
    )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _trusted_subjects() -> set[str]:
    baseline = {"backend", "orchestrator", "research", "code", "review", "coordinator"}
    configured = {
        str(item).strip()
        for item in str(os.getenv("A2A_TRUSTED_SUBJECTS") or "").split(",")
        if str(item).strip()
    }
    return baseline | configured


def _signing_secret() -> bytes:
    secret = str(os.getenv("A2A_JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("A2A_JWT_SECRET is required for strict runtime transport")
    return secret.encode("utf-8")


def _build_runtime_signature(
    subject: str,
    nonce: str,
    correlation_id: str,
    body: bytes,
    timestamp: str = "",
) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = f"{subject}:{nonce}:{correlation_id}:{timestamp}:{digest}".encode("utf-8")
    return hmac.new(_signing_secret(), message, hashlib.sha256).hexdigest()


def _validated_a2a_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("A2A transport requires an HTTP or HTTPS endpoint")
    if not parsed.hostname:
        raise ValueError("A2A transport requires a host")
    if parsed.username or parsed.password:
        raise ValueError("A2A transport URLs must not contain embedded credentials")
    if parsed.fragment:
        raise ValueError("A2A transport URLs must not contain fragments")
    return parsed.geturl()


def _enforce_transport_policy(
    url: str, *, sub: str, internal_service: bool, explicit_token: bool
) -> None:
    if _strict_transport_required():
        if not sub:
            raise ValueError("Strict runtime transport requires a non-empty subject")
        if sub not in _trusted_subjects():
            raise ValueError(f"Strict runtime transport requires trusted subject '{sub}'")
        if not internal_service:
            raise ValueError("Strict runtime transport requires internal service identity")

    if _runtime_profile() != "hosted":
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Hosted runtime profile requires HTTPS A2A endpoints")


def post_envelope(
    url: str,
    env: Envelope,
    sub: str = "orchestrator",
    token: Optional[str] = None,
    ca_bundle: Optional[str] = None,
    *,
    actor: Optional[str] = None,
    tenant_id: Optional[str] = None,
    internal_service: bool = False,
    additional_claims: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    validated_url = _validated_a2a_url(url)
    data = env.to_json().encode("utf-8")
    headers = {"Content-Type": "application/json"}
    claim_overrides: Dict[str, Any] = dict(additional_claims or {})
    if actor:
        claim_overrides.setdefault("actor", actor)
    if tenant_id:
        claim_overrides.setdefault("tenant_id", tenant_id)
    if internal_service:
        claim_overrides.setdefault("internal_service", True)
        claim_overrides.setdefault("subject", sub)
    _enforce_transport_policy(
        validated_url, sub=sub, internal_service=internal_service, explicit_token=bool(token)
    )
    tok = token or issue_token(sub=sub, additional_claims=claim_overrides or None)
    headers["Authorization"] = f"Bearer {tok}"
    # Propagate correlation id for cross-service tracing
    headers["X-Correlation-ID"] = env.correlation_id
    headers["X-Locus-Subject"] = sub
    if actor:
        headers["X-Locus-Actor"] = actor
    if tenant_id:
        headers["X-Locus-Tenant"] = tenant_id
    if _strict_transport_required():
        nonce = str(uuid4())
        timestamp = str(int(time.time()))
        headers["X-Locus-Nonce"] = nonce
        headers["X-Locus-Timestamp"] = timestamp
        headers["X-Locus-Signature"] = _build_runtime_signature(
            sub, nonce, env.correlation_id, data, timestamp=timestamp
        )

    parsed = urlparse(validated_url)
    ssl_context = None
    if parsed.scheme.lower() == "https":
        ssl_context = (
            ssl.create_default_context(cafile=ca_bundle)
            if ca_bundle
            else ssl.create_default_context()
        )
    elif _runtime_profile() == "hosted":
        raise ValueError("Hosted runtime profile requires HTTPS A2A endpoints")
    response = httpx.post(
        validated_url,
        content=data,
        headers=headers,
        timeout=10.0,
        verify=ssl_context if ssl_context is not None else True,
        follow_redirects=False,
    )
    body = response.text
    try:
        return json.loads(body)
    except Exception:
        return {"status": response.status_code, "body": body}
