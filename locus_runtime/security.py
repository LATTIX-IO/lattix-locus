from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from urllib import parse as urlparse
from dataclasses import dataclass, field
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


def build_default_keypair() -> bytes:
    return _secret_bytes()


@dataclass(frozen=True)
class CapabilityClaims:
    agent_id: str
    allowed_tools: list[str]
    allowed_read_paths: list[str]
    allowed_write_paths: list[str]
    max_tool_calls: int


@dataclass(frozen=True)
class CapabilityEvaluationRequest:
    action: str
    agent_id: str
    tool_call_count: int | None = None
    resource_path: str | None = None


class CapabilityMinter:
    def __init__(self, keypair: bytes | str) -> None:
        self._key = _secret_bytes(keypair)

    def mint_agent_token(
        self,
        agent_id: str,
        allowed_tools: list[str],
        allowed_read_paths: list[str],
        allowed_write_paths: list[str],
        max_tool_calls: int,
        ttl_seconds: int = 300,
    ) -> bytes:
        now = int(time.time())
        payload = {
            "agent_id": agent_id,
            "allowed_tools": allowed_tools,
            "allowed_read_paths": allowed_read_paths,
            "allowed_write_paths": allowed_write_paths,
            "max_tool_calls": max_tool_calls,
            "iat": now,
            "exp": now + ttl_seconds,
        }
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._key, payload_bytes, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload_bytes) + b"." + base64.urlsafe_b64encode(signature)


class CapabilityVerifier:
    def __init__(self, keypair: bytes | str) -> None:
        self._key = _secret_bytes(keypair)

    def verify(
        self,
        token: bytes | str,
        action: str,
        agent_id: str,
        *,
        tool_call_count: int | None = None,
        resource_path: str | None = None,
    ) -> bool:
        raw = token.encode("utf-8") if isinstance(token, str) else token
        try:
            encoded_payload, encoded_sig = raw.split(b".", 1)
            payload_bytes = base64.urlsafe_b64decode(encoded_payload)
            expected_sig = hmac.new(self._key, payload_bytes, hashlib.sha256).digest()
            actual_sig = base64.urlsafe_b64decode(encoded_sig)
            if not hmac.compare_digest(expected_sig, actual_sig):
                return False
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            return False
        if payload.get("agent_id") != agent_id or action not in (
            payload.get("allowed_tools") or []
        ):
            return False

        # Enforce token expiration
        exp = payload.get("exp")
        if exp is not None:
            try:
                if int(time.time()) > int(exp):
                    return False
            except (TypeError, ValueError):
                return False

        if tool_call_count is not None:
            try:
                max_tool_calls = int(payload.get("max_tool_calls", 0))
            except (TypeError, ValueError):
                return False
            if max_tool_calls > 0 and int(tool_call_count) > max_tool_calls:
                return False

        if resource_path:
            normalized_action = str(action or "").strip().lower()
            if normalized_action.startswith("read"):
                allowed_paths = [str(item) for item in payload.get("allowed_read_paths", [])]
                return OPAClient._path_allowed(resource_path, allowed_paths)
            if normalized_action.startswith("write"):
                allowed_paths = [str(item) for item in payload.get("allowed_write_paths", [])]
                return OPAClient._path_allowed(resource_path, allowed_paths)

        return True

    def verify_request(self, token: bytes | str, request: CapabilityEvaluationRequest) -> bool:
        return self.verify(
            token,
            request.action,
            request.agent_id,
            tool_call_count=request.tool_call_count,
            resource_path=request.resource_path,
        )


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyEvaluationRequest:
    policy_name: str
    agent_id: str = ""
    tool: str = ""
    resource: str = ""
    action: str = ""
    classification: str = "internal"
    provider: str = "local"
    target: str = ""
    allowed_tools: tuple[str, ...] = ()
    allowed_targets: tuple[str, ...] = ()
    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    tool_calls_used: int = 0
    max_tool_calls: int = 0
    budget_tokens_used: int = 0
    budget_max_tokens: int = 0
    readonly_rootfs: bool = False
    run_as_user: str = ""
    require_egress_mediation: bool = False
    allow_network: bool = False
    command: tuple[str, ...] = ()
    allowed_executables: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    requested_hosts: tuple[str, ...] = ()

    @classmethod
    def from_payload(
        cls, policy_name: str, payload: dict[str, Any] | None
    ) -> "PolicyEvaluationRequest":
        data = payload if isinstance(payload, dict) else {}
        raw_budget = data.get("budget")
        budget_data = raw_budget if isinstance(raw_budget, dict) else {}
        resource = str(
            data.get("resource")
            or data.get("target_path")
            or data.get("resource_path")
            or data.get("path")
            or ""
        ).strip()
        target = str(
            data.get("target")
            or data.get("target_path")
            or data.get("resource_path")
            or data.get("path")
            or ""
        ).strip()

        def _tuple(key: str) -> tuple[str, ...]:
            value = data.get(key)
            if isinstance(value, (list, tuple, set)):
                return tuple(str(item).strip() for item in value if str(item).strip())
            return ()

        return cls(
            policy_name=str(policy_name or "").strip(),
            agent_id=str(data.get("agent_id") or "").strip(),
            tool=str(data.get("tool") or data.get("action") or "").strip(),
            resource=resource,
            action=str(data.get("action") or data.get("tool") or "").strip(),
            classification=str(data.get("classification") or "internal").strip() or "internal",
            provider=str(data.get("provider") or "local").strip() or "local",
            target=target,
            allowed_tools=_tuple("allowed_tools"),
            allowed_targets=_tuple("allowed_targets"),
            allowed_read_paths=_tuple("allowed_read_paths"),
            allowed_write_paths=_tuple("allowed_write_paths"),
            allowed_paths=_tuple("allowed_paths"),
            tool_calls_used=OPAClient._safe_int(
                data.get("tool_calls_used", data.get("tool_calls", 0))
            ),
            max_tool_calls=OPAClient._safe_int(data.get("max_tool_calls", 0)),
            budget_tokens_used=OPAClient._safe_int(budget_data.get("tokens_used", 0)),
            budget_max_tokens=OPAClient._safe_int(budget_data.get("max_tokens", 0)),
            readonly_rootfs=bool(data.get("readonly_rootfs")),
            run_as_user=str(data.get("run_as_user") or "").strip(),
            require_egress_mediation=bool(data.get("require_egress_mediation")),
            allow_network=bool(data.get("allow_network")),
            command=_tuple("command"),
            allowed_executables=_tuple("allowed_executables"),
            allowed_hosts=_tuple("allowed_hosts"),
            requested_hosts=_tuple("requested_hosts"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tool": self.tool,
            "resource": self.resource,
            "action": self.action,
            "classification": self.classification,
            "provider": self.provider,
            "target": self.target,
            "allowed_tools": list(self.allowed_tools),
            "allowed_targets": list(self.allowed_targets),
            "allowed_read_paths": list(self.allowed_read_paths),
            "allowed_write_paths": list(self.allowed_write_paths),
            "allowed_paths": list(self.allowed_paths),
            "tool_calls_used": self.tool_calls_used,
            "max_tool_calls": self.max_tool_calls,
            "budget": {
                "tokens_used": self.budget_tokens_used,
                "max_tokens": self.budget_max_tokens,
            },
            "readonly_rootfs": self.readonly_rootfs,
            "run_as_user": self.run_as_user,
            "require_egress_mediation": self.require_egress_mediation,
            "allow_network": self.allow_network,
            "command": list(self.command),
            "allowed_executables": list(self.allowed_executables),
            "allowed_hosts": list(self.allowed_hosts),
            "requested_hosts": list(self.requested_hosts),
        }


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


def _normalize_policy_operation(tool: Any, action: Any) -> str:
    tool_text = str(tool or "").strip()
    if tool_text:
        return tool_text
    return str(action or "").strip()


def _parse_run_as_user_uid(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    user_part = raw.split(":", 1)[0].strip()
    if not user_part or not user_part.isdigit():
        return None
    try:
        uid = int(user_part)
    except ValueError:
        return None
    if uid < 0:
        return None
    return uid


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


class OPAClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    @staticmethod
    def _as_text_set(value: Any) -> set[str]:
        if isinstance(value, (list, tuple, set)):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _path_allowed(candidate: str, allowed_paths: list[str]) -> bool:
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
            if resolved_candidate == resolved_root or resolved_candidate.is_relative_to(
                resolved_root
            ):
                return True
        return False

    @staticmethod
    def _decision(
        allowed: bool,
        reason: str,
        *,
        request: PolicyEvaluationRequest,
        control: str,
        extra: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        details = {
            "policy_name": request.policy_name,
            "control": control,
            "agent_id": request.agent_id,
            "tool": request.tool,
            "action": request.action,
        }
        if isinstance(extra, dict):
            details.update(extra)
        return PolicyDecision(allowed=allowed, reason=reason, details=details)

    @staticmethod
    def _looks_like_sensitive_path(value: str) -> bool:
        candidate = str(value or "").strip().replace("\\", "/").lower()
        if not candidate:
            return False
        name = Path(candidate).name.lower()
        path_parts = [part for part in candidate.split("/") if part]
        sensitive_names = {
            ".env",
            "credentials",
            "config.json",
            ".keystore",
            ".jks",
            "id_rsa",
            "id_dsa",
            "id_ed25519",
            "authorized_keys",
            "known_hosts",
            "secrets.json",
            "service-account.json",
            "service_account.json",
            "token.json",
            ".npmrc",
            ".pypirc",
            ".netrc",
        }
        sensitive_suffixes = (
            ".env",
            ".env.local",
            ".env.production",
            ".pem",
            ".key",
            ".p12",
            ".pfx",
            ".kdbx",
            ".asc",
            ".p8",
            ".csr",
            ".keystore",
            ".jks",
        )
        sensitive_fragments = (
            "apikey",
            "api-key",
            "access_token",
            "access-token",
            "auth_token",
            "auth-token",
            "bearer",
            "client_secret",
            "client-secret",
            "oauth",
            "refresh_token",
            "refresh-token",
            "secret",
            "credential",
            "private",
            "private_key",
            "private-key",
            "passwd",
            "password",
            "token",
            "service-account",
            "service_account",
        )
        if name in sensitive_names or name.endswith(sensitive_suffixes):
            return True
        if any(fragment in name for fragment in sensitive_fragments):
            return True
        if any(part in {".ssh", ".gnupg", ".aws", ".azure", ".kube"} for part in path_parts):
            return True
        if "/.config/gcloud/" in f"/{candidate}/":
            return True
        return False

    async def evaluate_request(self, request: PolicyEvaluationRequest) -> PolicyDecision:
        await asyncio.sleep(0)
        if request.policy_name == "agent_policy":
            operation = _normalize_policy_operation(request.tool, request.action)
            if request.budget_tokens_used > request.budget_max_tokens:
                return self._decision(
                    False,
                    "budget overrun",
                    request=request,
                    control="token_budget",
                    extra={
                        "observed": request.budget_tokens_used,
                        "limit": request.budget_max_tokens,
                    },
                )
            allowed_tools = set(request.allowed_tools)

            if operation == "read_file" and self._looks_like_sensitive_path(request.resource):
                return self._decision(
                    False,
                    "credential-like file access denied",
                    request=request,
                    control="credential_file",
                )
            if operation == "network_egress":
                allowed_targets = set(request.allowed_targets)
                if not allowed_targets or request.target not in allowed_targets:
                    return self._decision(
                        False,
                        "network target denied",
                        request=request,
                        control="network_egress",
                        extra={"target": request.target, "allowed_targets": list(allowed_targets)},
                    )
            if (
                operation == "llm_call"
                and request.classification == "restricted"
                and request.provider != "local"
            ):
                return self._decision(
                    False,
                    "restricted data requires local provider",
                    request=request,
                    control="restricted_provider",
                )
            if request.max_tool_calls > 0 and request.tool_calls_used > request.max_tool_calls:
                return self._decision(
                    False,
                    "tool call budget exceeded",
                    request=request,
                    control="tool_budget",
                    extra={"observed": request.tool_calls_used, "limit": request.max_tool_calls},
                )
            if not allowed_tools:
                return self._decision(
                    False,
                    "allowed_tools must be supplied explicitly",
                    request=request,
                    control="tool_allowlist_missing",
                )
            if operation in allowed_tools:
                return self._decision(
                    True,
                    "allowed by policy",
                    request=request,
                    control="allowlisted_tool",
                    extra={"allowed_tools": sorted(allowed_tools), "operation": operation},
                )
            return self._decision(
                False,
                "agent/tool not allowlisted",
                request=request,
                control="tool_allowlist",
                extra={"allowed_tools": sorted(allowed_tools), "operation": operation},
            )
        if request.policy_name == "network_egress":
            allowed_targets = set(request.allowed_targets)
            allowed = request.target in allowed_targets
            return self._decision(
                allowed,
                "target allowed" if allowed else "target denied",
                request=request,
                control="network_egress",
                extra={"target": request.target, "allowed_targets": list(allowed_targets)},
            )
        if request.policy_name == "tool_jail":
            network_safe = (
                request.allow_network is not True
            ) or request.require_egress_mediation is True
            executable = (
                request.command[0]
                if request.command
                else _normalize_policy_operation(request.tool, request.action)
            )
            if not request.readonly_rootfs:
                return self._decision(
                    False, "readonly rootfs required", request=request, control="readonly_rootfs"
                )
            uid = _parse_run_as_user_uid(request.run_as_user)
            if uid is None:
                return self._decision(
                    False, "invalid run_as_user value", request=request, control="run_as_user"
                )
            if uid == 0:
                return self._decision(
                    False, "root execution denied", request=request, control="run_as_user"
                )
            if not network_safe:
                return self._decision(
                    False,
                    "network egress mediation required",
                    request=request,
                    control="egress_mediation",
                )
            allowed_executables = set(request.allowed_executables)
            if not allowed_executables:
                return self._decision(
                    False,
                    "sandbox execution requires explicit allowed_executables",
                    request=request,
                    control="allowed_executables",
                )
            if executable not in allowed_executables:
                return self._decision(
                    False,
                    "sandbox executable denied",
                    request=request,
                    control="allowed_executables",
                    extra={
                        "executable": executable,
                        "allowed_executables": sorted(allowed_executables),
                    },
                )
            if request.allow_network:
                allowed_hosts = set(request.allowed_hosts)
                requested_hosts = set(request.requested_hosts)
                if not allowed_hosts:
                    return self._decision(
                        False,
                        "network access requires explicit allowed_hosts",
                        request=request,
                        control="allowed_hosts",
                    )
                if not requested_hosts:
                    return self._decision(
                        False,
                        "network access requires explicit requested_hosts",
                        request=request,
                        control="requested_hosts",
                    )
                if not requested_hosts.issubset(allowed_hosts):
                    return self._decision(
                        False,
                        "requested hosts not allowlisted",
                        request=request,
                        control="requested_hosts",
                        extra={
                            "requested_hosts": sorted(requested_hosts),
                            "allowed_hosts": sorted(allowed_hosts),
                        },
                    )
            elif request.requested_hosts:
                return self._decision(
                    False,
                    "requested_hosts require allow_network=true",
                    request=request,
                    control="requested_hosts",
                    extra={"requested_hosts": list(request.requested_hosts)},
                )
            return self._decision(
                True, "tool jail policy passed", request=request, control="tool_jail"
            )
        if request.policy_name == "filesystem_path":
            action = request.action.lower() or "read"
            target = request.target or request.resource
            if action.startswith("write"):
                allowed_paths = list(request.allowed_write_paths)
            elif action.startswith("read"):
                allowed_paths = list(request.allowed_read_paths)
            else:
                allowed_paths = list(request.allowed_paths)
            allowed = self._path_allowed(target, allowed_paths)
            return self._decision(
                allowed,
                "path allowed" if allowed else "path denied",
                request=request,
                control="filesystem_path",
                extra={"target": target, "allowed_paths": allowed_paths},
            )
        return self._decision(False, "unknown policy", request=request, control="unknown_policy")

    async def evaluate(self, policy_name: str, payload: dict[str, Any]) -> PolicyDecision:
        request = PolicyEvaluationRequest.from_payload(policy_name, payload)
        return await self.evaluate_request(request)


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
