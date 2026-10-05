from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
from threading import Lock
from typing import Any, Dict

try:
    from fastapi import FastAPI, Request, HTTPException
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore
    Request = Any  # type: ignore
    HTTPException = Exception  # type: ignore

from runtime.layer2.contracts import Envelope
from runtime.layer2.validation import validate_envelope_dict
from runtime.security.jwt import verify_token, JWTConfig
from locus_runtime.security import token_identity_from_claims


app = FastAPI(title=os.getenv("SERVICE_NAME", "agent-service")) if FastAPI else None
_SEEN_NONCES: dict[str, float] = {}
_SEEN_NONCES_LOCK = Lock()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_profile() -> str:
    value = str(os.getenv("LOCUS_RUNTIME_PROFILE", "local-lightweight") or "").strip().lower()
    return value or "local-lightweight"


def _strict_service_auth_required() -> bool:
    return _runtime_profile() in {"local-secure", "hosted"} or _env_flag(
        "LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", False
    )


def _placeholder_execution_allowed() -> bool:
    return not _strict_service_auth_required()


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
        raise HTTPException(status_code=500, detail="A2A_JWT_SECRET is required")
    return secret.encode("utf-8")


def _nonce_ttl_seconds() -> int:
    raw = str(
        os.getenv("LOCUS_A2A_NONCE_TTL_SECONDS") or os.getenv("A2A_REPLAY_TTL_SECONDS") or "600"
    ).strip()
    try:
        ttl = int(raw)
    except ValueError:
        ttl = 600
    return max(1, min(ttl, 86_400))


def _clock_skew_seconds() -> int:
    raw = str(os.getenv("A2A_CLOCK_SKEW_SECONDS") or "30").strip()
    try:
        skew = int(raw)
    except ValueError:
        skew = 30
    return max(1, min(skew, 300))


def _prune_seen_nonces_locked(now: float | None = None) -> None:
    current = time.time() if now is None else now
    expired = [nonce for nonce, expires_at in _SEEN_NONCES.items() if expires_at <= current]
    for nonce in expired:
        _SEEN_NONCES.pop(nonce, None)


def _prune_seen_nonces(now: float | None = None) -> None:
    with _SEEN_NONCES_LOCK:
        _prune_seen_nonces_locked(now)


def _register_seen_nonce_or_raise(nonce: str, *, now: float | None = None) -> None:
    current = time.time() if now is None else now
    with _SEEN_NONCES_LOCK:
        _prune_seen_nonces_locked(current)
        if nonce in _SEEN_NONCES:
            raise HTTPException(status_code=409, detail="locus nonce replay detected")
        _SEEN_NONCES[nonce] = current + _nonce_ttl_seconds()
        _prune_seen_nonces_locked(current)


def _verify_runtime_headers(request: Request, body: bytes) -> str:
    if not _strict_service_auth_required():
        return ""

    subject = str(
        request.headers.get("X-Locus-Subject") or request.headers.get("x-locus-subject") or ""
    ).strip()
    nonce = str(
        request.headers.get("X-Locus-Nonce") or request.headers.get("x-locus-nonce") or ""
    ).strip()
    signature = str(
        request.headers.get("X-Locus-Signature")
        or request.headers.get("x-locus-signature")
        or ""
    ).strip()
    timestamp = str(
        request.headers.get("X-Locus-Timestamp")
        or request.headers.get("x-locus-timestamp")
        or ""
    ).strip()
    correlation_id = str(
        request.headers.get("X-Correlation-ID") or request.headers.get("x-correlation-id") or ""
    ).strip()

    if not subject or subject not in _trusted_subjects():
        raise HTTPException(status_code=401, detail="untrusted or missing locus subject")
    if not nonce:
        raise HTTPException(status_code=401, detail="missing locus nonce")
    if not signature:
        raise HTTPException(status_code=401, detail="missing locus signature")
    if not timestamp:
        raise HTTPException(status_code=401, detail="missing locus timestamp")
    if not correlation_id:
        raise HTTPException(
            status_code=401, detail="missing correlation id header for signed A2A request"
        )
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid locus timestamp") from exc
    if abs(int(time.time()) - timestamp_value) > _clock_skew_seconds():
        raise HTTPException(status_code=401, detail="stale locus timestamp")

    digest = hashlib.sha256(body).hexdigest()
    message = f"{subject}:{nonce}:{correlation_id}:{timestamp}:{digest}".encode("utf-8")
    expected = hmac.new(_signing_secret(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid locus signature")

    _register_seen_nonce_or_raise(nonce)
    return subject


def _health_payload() -> Dict[str, str]:
    return {
        "status": "ok",
        "service": str(os.getenv("SERVICE_NAME", "agent-service")).strip() or "agent-service",
        "mode": _runtime_profile(),
        "auth": "required" if _strict_service_auth_required() else "optional",
    }


def _authz(request: Request) -> Dict[str, Any]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth.split(" ", 1)[1]
    claims = verify_token(token, JWTConfig())
    identity = token_identity_from_claims(claims)
    if _strict_service_auth_required() and not identity.internal_service:
        raise HTTPException(
            status_code=403, detail="strict runtime profiles require internal service identity"
        )
    request.state.auth_claims = claims
    request.state.auth_identity = identity
    return claims


if app:

    @app.get("/healthz")
    def healthz() -> Dict[str, str]:
        payload = _health_payload()
        if _strict_service_auth_required():
            return {
                "status": payload["status"],
                "mode": payload["mode"],
            }
        return payload

    @app.get("/healthz/details")
    def healthz_details(req: Request) -> Dict[str, str]:
        _authz(req)
        return _health_payload()

    @app.get("/readyz")
    def readyz(req: Request) -> Dict[str, str]:
        if _strict_service_auth_required():
            _authz(req)
        payload = _health_payload()
        return {
            "status": "ready",
            "service": payload["service"],
            "mode": payload["mode"],
        }

    @app.post("/v1/envelope")
    async def handle_envelope(req: Request) -> Dict[str, Any]:
        raw_body = await req.body()
        verified_subject = _verify_runtime_headers(req, raw_body)
        claims = _authz(req)
        identity = getattr(req.state, "auth_identity", None)
        authenticated_subject = getattr(identity, "subject", str(claims.get("sub") or ""))
        if verified_subject and authenticated_subject and verified_subject != authenticated_subject:
            raise HTTPException(
                status_code=401,
                detail="locus subject header does not match bearer token subject",
            )
        data = json.loads(raw_body.decode("utf-8"))
        # Basic validation
        errs = validate_envelope_dict(data)
        if errs:
            raise HTTPException(status_code=400, detail={"errors": errs})
        env = Envelope.from_json(json.dumps(data))
        # Correlation consistency (if header provided)
        corr_hdr = req.headers.get("X-Correlation-ID") or req.headers.get("x-correlation-id")
        if corr_hdr and corr_hdr != env.correlation_id:
            raise HTTPException(status_code=400, detail={"errors": ["correlation_id mismatch"]})
        if not _placeholder_execution_allowed():
            raise HTTPException(status_code=501, detail="agent runtime handler is not configured")
        # TODO: call agent runtime handler or enqueue work
        corr = data.get("correlation_id")
        return {
            "accepted": True,
            "envelope_id": env.id,
            "correlation_id": corr,
            "locus_subject": verified_subject,
            "authenticated_subject": getattr(identity, "subject", str(claims.get("sub") or "")),
            "authenticated_actor": getattr(
                identity, "actor", str(claims.get("actor") or claims.get("sub") or "")
            ),
        }
