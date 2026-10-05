from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib import parse as urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt

from locus_runtime.persistence import mutate_state


def _secret_bytes(key: bytes | str | None = None) -> bytes:
    if isinstance(key, bytes):
        return key
    if isinstance(key, str) and key:
        return key.encode("utf-8")
    configured = str(os.getenv("A2A_JWT_SECRET") or "").strip()
    if configured:
        return configured.encode("utf-8")
    raise RuntimeError("A2A_JWT_SECRET is required")


# The custom HMAC JSON capability tokens (CapabilityMinter/CapabilityVerifier)
# were retired by LOCUS-334: capability grants are Biscuit tokens verified by
# the gateway (locus_runtime.grants). The HS256 shared secret below remains for
# A2A runtime tokens and event signing only (separate follow-up).


@dataclass(frozen=True)
class RuntimeTokenIdentity:
    subject: str
    actor: str
    tenant_id: str = ""
    subject_type: str = "user"
    internal_service: bool = False


def _first_claim_value(claims: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = claims.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _claim_as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _canonicalize_candidate_path(value: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    try:
        if candidate.exists():
            return candidate.resolve(strict=True)
        parent = candidate.parent.resolve(strict=True)
        return parent / candidate.name
    except Exception:
        return None


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        _secret_bytes(),
        algorithms=["HS256"],
        audience="locus-runtime",
        issuer="lattix-locus",
    )


def token_identity_from_claims(claims: dict[str, Any] | None) -> RuntimeTokenIdentity:
    payload = claims if isinstance(claims, dict) else {}
    subject = _first_claim_value(payload, "subject", "service", "sub")
    actor = _first_claim_value(
        payload,
        "actor",
        "actor_id",
        "user_id",
        "user",
        "preferred_username",
        "email",
        "name",
        "x-locus-actor",
    )
    tenant_id = _first_claim_value(
        payload, "tenant_id", "tenant", "currentTenant", "current_tenant"
    )
    subject_type = _first_claim_value(payload, "subject_type", "token_type")
    internal_service = _claim_as_bool(payload.get("internal_service")) or _claim_as_bool(
        payload.get("internal")
    )

    resolved_actor = actor or subject or "anonymous"
    resolved_subject = subject or resolved_actor
    resolved_subject_type = subject_type or ("service" if internal_service else "user")

    return RuntimeTokenIdentity(
        subject=resolved_subject,
        actor=resolved_actor,
        tenant_id=tenant_id,
        subject_type=resolved_subject_type,
        internal_service=internal_service,
    )


def path_within_allowed_roots(candidate: str, allowed_paths: list[str]) -> bool:
    """Canonical (symlink-resolved) containment check for capability path scopes.

    Not a policy rule: policy decisions come from Rego via
    ``locus_runtime.policy_engine``. This guards the read/write path scopes
    carried inside a capability token.
    """
    resolved_candidate = _canonicalize_candidate_path(candidate)
    if resolved_candidate is None:
        return False

    for item in allowed_paths:
        root = str(item or "").strip()
        if not root:
            continue
        try:
            resolved_root = Path(root).expanduser().resolve(strict=True)
        except Exception:
            continue
        if resolved_candidate == resolved_root or resolved_candidate.is_relative_to(resolved_root):
            return True
    return False


class VaultClient:
    def __init__(
        self, *, addr: str | None = None, token: str | None = None, timeout_seconds: int = 5
    ) -> None:
        self.addr = (
            str(addr if addr is not None else os.getenv("VAULT_ADDR") or "").strip().rstrip("/")
        )
        self.token = str(token if token is not None else os.getenv("VAULT_TOKEN") or "").strip()
        self.timeout_seconds = max(1, int(timeout_seconds))

    @staticmethod
    def _validated_addr(addr: str) -> str:
        parsed = urlparse.urlparse(str(addr or "").strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            raise RuntimeError("Vault client requires an HTTP or HTTPS address")
        if not parsed.hostname:
            raise RuntimeError("Vault client requires a host")
        if parsed.username or parsed.password:
            raise RuntimeError("Vault client does not allow credentials in VAULT_ADDR")
        if parsed.fragment:
            raise RuntimeError("Vault client address must not include fragments")
        return parsed.geturl().rstrip("/")

    @staticmethod
    def _normalized_path(path: str) -> str:
        normalized_path = str(path or "").strip().strip("/")
        if not normalized_path:
            raise ValueError("Vault secret path is required")
        return normalized_path

    def _require_configured(self) -> str:
        if not self.addr or not self.token:
            raise RuntimeError("Vault client is not configured; set VAULT_ADDR and VAULT_TOKEN")
        return self._validated_addr(self.addr)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        include_token: bool = True,
    ) -> dict[str, Any]:
        base_url = self._validated_addr(self.addr) if self.addr else self._require_configured()
        url = f"{base_url}/v1/{urlparse.quote(self._normalized_path(path), safe='/')}"
        headers = {"Accept": "application/json"}
        if include_token:
            if not self.token:
                raise RuntimeError("Vault client is not configured; set VAULT_ADDR and VAULT_TOKEN")
            headers["X-Vault-Token"] = self.token
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=float(self.timeout_seconds),
                follow_redirects=False,
            )
            response.raise_for_status()
            parsed_payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Vault {method.upper()} failed for '{path}': {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Vault {method.upper()} failed for '{path}': {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Vault {method.upper()} failed for '{path}': invalid JSON response"
            ) from exc

        if not isinstance(parsed_payload, dict):
            raise RuntimeError(
                f"Vault {method.upper()} failed for '{path}': unexpected response shape"
            )
        return parsed_payload

    def health_status(self) -> dict[str, Any]:
        if not self.addr:
            raise RuntimeError("Vault client requires VAULT_ADDR to query health")
        url = (
            f"{self._validated_addr(self.addr)}/v1/sys/health"
            "?standbyok=true&perfstandbyok=true&sealedcode=200&uninitcode=200"
        )
        try:
            response = httpx.get(
                url,
                headers={"Accept": "application/json"},
                timeout=float(self.timeout_seconds),
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Vault health query failed: {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Vault health query failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Vault health query failed: invalid JSON response") from exc
        return payload if isinstance(payload, dict) else {}

    def initialize(self, *, secret_shares: int = 1, secret_threshold: int = 1) -> dict[str, Any]:
        payload = self._request_json(
            "POST",
            "sys/init",
            payload={
                "secret_shares": max(1, int(secret_shares)),
                "secret_threshold": max(1, int(secret_threshold)),
            },
            include_token=False,
        )
        return payload

    def unseal(self, key: str) -> dict[str, Any]:
        key_text = str(key or "").strip()
        if not key_text:
            raise ValueError("Vault unseal key is required")
        return self._request_json(
            "POST", "sys/unseal", payload={"key": key_text}, include_token=False
        )

    def write_secret(self, path: str, secret: dict[str, Any]) -> dict[str, Any]:
        normalized_path = self._normalized_path(path)
        secret_payload = secret if isinstance(secret, dict) else {}
        if "/data/" in normalized_path:
            payload = {"data": secret_payload}
        else:
            payload = secret_payload
        return self._request_json("POST", normalized_path, payload=payload)

    def read_secret(self, path: str) -> dict[str, Any]:
        normalized_path = self._normalized_path(path)
        payload = self._request_json("GET", normalized_path)

        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return dict(data.get("data") or {})
        if isinstance(data, dict):
            return data
        return payload


def _runtime_replay_ttl_seconds() -> int:
    raw = (
        os.getenv("LOCUS_RUNTIME_REPLAY_TTL_SECONDS")
        or os.getenv("A2A_REPLAY_TTL_SECONDS")
        or "900"
    )
    try:
        ttl = int(str(raw).strip())
    except (TypeError, ValueError):
        ttl = 900
    return max(1, min(ttl, 86_400))


def _parse_replay_expiry(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _normalize_replay_tokens(
    raw_entries: Any, *, now: float, ttl_seconds: int
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_entries, list):
        return normalized
    fallback_expiry = now + ttl_seconds
    for entry in raw_entries:
        token_hash = ""
        expires_at = None
        if isinstance(entry, dict):
            token_hash = str(entry.get("token_hash") or entry.get("hash") or "").strip()
            expires_at = _parse_replay_expiry(entry.get("expires_at"))
        elif isinstance(entry, str):
            token_hash = entry.strip()
        if not token_hash:
            continue
        resolved_expiry = expires_at if expires_at is not None else fallback_expiry
        if resolved_expiry <= now:
            continue
        normalized.append({"token_hash": token_hash, "expires_at": resolved_expiry})
    return normalized[-5000:]


def mint_token(
    sub: str, ttl_seconds: int = 600, additional_claims: dict[str, Any] | None = None
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": now,
        "exp": now + ttl_seconds,
        "iss": "lattix-locus",
        "aud": "locus-runtime",
        "jti": str(uuid4()),
    }
    if additional_claims:
        payload.update(additional_claims)
    return str(jwt.encode(payload, _secret_bytes(), algorithm="HS256"))


def verify_token(token: str, require_nonce: bool = True) -> dict[str, Any]:
    claims = decode_token(token)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    replay_detected = False
    now = time.time()
    ttl_seconds = _runtime_replay_ttl_seconds()

    def _mutate(snapshot: dict[str, Any]) -> None:
        nonlocal replay_detected
        tokens = _normalize_replay_tokens(
            snapshot.get("replay_tokens", []), now=now, ttl_seconds=ttl_seconds
        )
        if any(str(item.get("token_hash") or "") == token_hash for item in tokens):
            replay_detected = True
            snapshot["replay_tokens"] = tokens
            return
        tokens.append({"token_hash": token_hash, "expires_at": now + ttl_seconds})
        snapshot["replay_tokens"] = tokens[-5000:]

    mutate_state(_mutate)
    if replay_detected:
        raise ValueError("replay detected")
    return claims


def reset_token_caches() -> None:
    return None


def sign_event(event: Any) -> str:
    signer = str(getattr(event, "signer", "") or getattr(event, "source", ""))
    message = f"{getattr(event, 'event_hash', '')}:{signer}"
    return hmac.new(_secret_bytes(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_event_signature(event: Any) -> bool:
    expected = sign_event(event)
    actual = str(getattr(event, "signature", ""))
    return bool(actual) and hmac.compare_digest(actual, expected)
