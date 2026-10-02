from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from apps.workers.runtime.layer2.contracts import Envelope
from apps.workers.runtime.layer2.event_bus import EventBus
from apps.workers.runtime.layer2 import security as runtime_security_module
from apps.workers.runtime.layer1.orchestrator import Orchestrator
from apps.workers.runtime.network.dispatcher import TopicDispatcher
from apps.workers.runtime.network import a2a
from apps.workers.runtime.security.jwt import JWTConfig
from apps.workers.runtime.security.jwt import extract_identity, verify_token
from apps.workers.runtime.security.jwt import issue_token


def test_worker_issue_token_supports_shared_identity_claims(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("A2A_JWT_ALG", "HS256")
    monkeypatch.setenv("A2A_JWT_ISS", "lattix-locus")
    monkeypatch.setenv("A2A_JWT_AUD", "locus-runtime")

    token = issue_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "actor": "tenant-user",
            "tenant_id": "acme",
            "subject": "backend",
            "internal_service": True,
        },
    )

    claims = verify_token(token)
    identity = extract_identity(token)

    assert claims["actor"] == "tenant-user"
    assert claims["tenant_id"] == "acme"
    assert identity.actor == "tenant-user"
    assert identity.subject == "backend"
    assert identity.tenant_id == "acme"
    assert identity.internal_service is True


def test_worker_post_envelope_mints_identity_claims(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("A2A_JWT_ALG", "HS256")
    monkeypatch.setenv("A2A_JWT_ISS", "lattix-locus")
    monkeypatch.setenv("A2A_JWT_AUD", "locus-runtime")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")

    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = json.dumps({"accepted": True})

    def _fake_post(
        url, content=None, headers=None, timeout=None, verify=None, follow_redirects=None
    ):
        captured["url"] = url
        captured["authorization"] = headers.get("Authorization") if headers else None
        captured["correlation_id"] = headers.get("X-Correlation-ID") if headers else None
        captured["locus_subject"] = headers.get("X-Locus-Subject") if headers else None
        captured["locus_nonce"] = headers.get("X-Locus-Nonce") if headers else None
        captured["locus_signature"] = headers.get("X-Locus-Signature") if headers else None
        captured["timeout"] = timeout
        captured["verify"] = verify
        captured["follow_redirects"] = follow_redirects
        return _FakeResponse()

    monkeypatch.setattr(a2a.httpx, "post", _fake_post)

    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})
    response = a2a.post_envelope(
        "https://worker.example.test/v1/envelope",
        env,
        sub="backend",
        actor="owner-user",
        tenant_id="acme",
        internal_service=True,
    )

    assert response == {"accepted": True}
    auth_header = str(captured["authorization"])
    assert auth_header.startswith("Bearer ")

    token = auth_header.split(" ", 1)[1]
    identity = extract_identity(token)

    assert identity.subject == "backend"
    assert identity.actor == "owner-user"
    assert identity.tenant_id == "acme"
    assert identity.internal_service is True
    assert captured["url"] == "https://worker.example.test/v1/envelope"
    assert captured["correlation_id"] == env.correlation_id
    assert captured["locus_subject"] == "backend"
    assert captured["locus_nonce"]
    assert captured["locus_signature"]


def test_worker_post_envelope_rejects_non_http_scheme(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")

    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})

    with pytest.raises(ValueError, match="HTTP or HTTPS endpoint"):
        a2a.post_envelope("file:///tmp/payload.json", env, sub="backend", internal_service=False)


def test_worker_post_envelope_rejects_plain_http_in_hosted_profile(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")

    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})

    with pytest.raises(ValueError, match="HTTPS A2A endpoints"):
        a2a.post_envelope(
            "http://worker.example.test/v1/envelope",
            env,
            sub="backend",
            actor="owner-user",
            tenant_id="acme",
            internal_service=True,
        )


def test_worker_jwt_defaults_match_shared_runtime_contract(monkeypatch) -> None:
    monkeypatch.delenv("A2A_JWT_ALG", raising=False)
    monkeypatch.delenv("A2A_JWT_ISS", raising=False)
    monkeypatch.delenv("A2A_JWT_AUD", raising=False)

    cfg = JWTConfig()

    assert cfg.algorithm == "HS256"
    assert cfg.issuer == "lattix-locus"
    assert cfg.audience == "locus-runtime"


def _load_agent_service_template_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "workers"
        / "services"
        / "AGENT_SERVICE_TEMPLATE"
        / "app.py"
    )
    workers_root = str(module_path.parents[2])
    if workers_root not in sys.path:
        sys.path.insert(0, workers_root)
    module_name = "test_agent_service_template_app"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_service_template_hosted_profile_limits_public_surfaces(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")
    monkeypatch.setenv("SERVICE_NAME", "security-agent")

    module = _load_agent_service_template_module()
    client = TestClient(module.app)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "mode": "hosted"}

    ready = client.get("/readyz")
    assert ready.status_code == 401

    details = client.get("/healthz/details")
    assert details.status_code == 401


def test_worker_service_template_hosted_profile_requires_internal_service_identity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")
    monkeypatch.setenv("SERVICE_NAME", "security-agent")

    module = _load_agent_service_template_module()
    client = TestClient(module.app)
    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})

    user_token = issue_token(
        "frontend-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "frontend-user",
            "tenant_id": "acme",
            "internal_service": False,
        },
    )
    denied = client.post(
        "/v1/envelope",
        content=env.to_json(),
        headers={"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"},
    )
    assert denied.status_code == 401

    service_token = issue_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "actor": "service-backend",
            "tenant_id": "acme",
            "subject": "backend",
            "internal_service": True,
        },
    )
    timestamp = str(int(time.time()))
    allowed = client.post(
        "/v1/envelope",
        content=env.to_json(),
        headers={
            "Authorization": f"Bearer {service_token}",
            "Content-Type": "application/json",
            "X-Correlation-ID": env.correlation_id,
            "X-Locus-Subject": "backend",
            "X-Locus-Nonce": "nonce-1",
            "X-Locus-Timestamp": timestamp,
            "X-Locus-Signature": a2a._build_runtime_signature(
                "backend",
                "nonce-1",
                env.correlation_id,
                env.to_json().encode("utf-8"),
                timestamp=timestamp,
            ),
        },
    )
    assert allowed.status_code == 501
    assert allowed.json()["detail"] == "agent runtime handler is not configured"

    ready = client.get("/readyz", headers={"Authorization": f"Bearer {service_token}"})
    assert ready.status_code == 200
    assert ready.json()["mode"] == "hosted"

    details = client.get("/healthz/details", headers={"Authorization": f"Bearer {service_token}"})
    assert details.status_code == 200
    assert details.json()["auth"] == "required"


def test_worker_service_template_lightweight_profile_keeps_placeholder_ack(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-lightweight")
    monkeypatch.delenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", raising=False)

    module = _load_agent_service_template_module()
    client = TestClient(module.app)
    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})
    token = issue_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "actor": "service-backend",
            "tenant_id": "acme",
            "subject": "backend",
            "internal_service": False,
        },
    )

    allowed = client.post(
        "/v1/envelope",
        content=env.to_json(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    assert allowed.status_code == 200
    assert allowed.json()["accepted"] is True
    assert allowed.json()["authenticated_subject"] == "backend"
    assert allowed.json()["authenticated_actor"] == "service-backend"


def test_worker_service_template_rejects_runtime_header_replay(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    module = _load_agent_service_template_module()
    client = TestClient(module.app)
    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})
    token = issue_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "actor": "service-backend",
            "tenant_id": "acme",
            "subject": "backend",
            "internal_service": True,
        },
    )
    nonce = "nonce-replay"
    timestamp = str(int(time.time()))
    signature = a2a._build_runtime_signature(
        "backend", nonce, env.correlation_id, env.to_json().encode("utf-8"), timestamp=timestamp
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Correlation-ID": env.correlation_id,
        "X-Locus-Subject": "backend",
        "X-Locus-Nonce": nonce,
        "X-Locus-Timestamp": timestamp,
        "X-Locus-Signature": signature,
    }

    first = client.post("/v1/envelope", content=env.to_json(), headers=headers)
    assert first.status_code == 501
    assert first.json()["detail"] == "agent runtime handler is not configured"
    second = client.post("/v1/envelope", content=env.to_json(), headers=headers)
    assert second.status_code == 409


def test_worker_service_template_requires_correlation_id_for_signed_runtime_headers(
    monkeypatch,
) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    module = _load_agent_service_template_module()
    client = TestClient(module.app)
    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})
    token = issue_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "actor": "service-backend",
            "tenant_id": "acme",
            "subject": "backend",
            "internal_service": True,
        },
    )
    nonce = "nonce-missing-correlation"
    timestamp = str(int(time.time()))
    signature = a2a._build_runtime_signature(
        "backend", nonce, "", env.to_json().encode("utf-8"), timestamp=timestamp
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Locus-Subject": "backend",
        "X-Locus-Nonce": nonce,
        "X-Locus-Timestamp": timestamp,
        "X-Locus-Signature": signature,
    }

    denied = client.post("/v1/envelope", content=env.to_json(), headers=headers)
    assert denied.status_code == 401
    assert denied.json()["detail"] == "missing correlation id header for signed A2A request"


def test_worker_service_template_rejects_header_subject_mismatch_with_bearer_identity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    module = _load_agent_service_template_module()
    client = TestClient(module.app)
    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})
    token = issue_token(
        "research",
        ttl_seconds=60,
        additional_claims={
            "actor": "service-research",
            "tenant_id": "acme",
            "subject": "research",
            "internal_service": True,
        },
    )
    nonce = "nonce-subject-mismatch"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Correlation-ID": env.correlation_id,
        "X-Locus-Subject": "backend",
        "X-Locus-Nonce": nonce,
        "X-Locus-Timestamp": timestamp,
        "X-Locus-Signature": a2a._build_runtime_signature(
            "backend", nonce, env.correlation_id, env.to_json().encode("utf-8"), timestamp=timestamp
        ),
    }

    denied = client.post("/v1/envelope", content=env.to_json(), headers=headers)
    assert denied.status_code == 401
    assert denied.json()["detail"] == "locus subject header does not match bearer token subject"


def test_worker_service_template_prunes_expired_seen_nonces_before_accepting_reuse(
    monkeypatch,
) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")
    monkeypatch.setenv("LOCUS_A2A_NONCE_TTL_SECONDS", "5")

    module = _load_agent_service_template_module()
    module._SEEN_NONCES.clear()
    module._SEEN_NONCES["expired-nonce"] = 1.0

    module._register_seen_nonce_or_raise("expired-nonce", now=10.0)

    assert module._SEEN_NONCES["expired-nonce"] == 15.0


def test_worker_service_template_requires_fresh_runtime_timestamp(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")
    monkeypatch.setenv("A2A_CLOCK_SKEW_SECONDS", "30")

    module = _load_agent_service_template_module()
    client = TestClient(module.app)
    env = Envelope(topic="security.compliance", sender="orchestrator", payload={"task": "review"})
    token = issue_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "actor": "service-backend",
            "tenant_id": "acme",
            "subject": "backend",
            "internal_service": True,
        },
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Correlation-ID": env.correlation_id,
        "X-Locus-Subject": "backend",
        "X-Locus-Nonce": "nonce-stale-ts",
        "X-Locus-Timestamp": "1",
        "X-Locus-Signature": a2a._build_runtime_signature(
            "backend",
            "nonce-stale-ts",
            env.correlation_id,
            env.to_json().encode("utf-8"),
            timestamp="1",
        ),
    }

    denied = client.post("/v1/envelope", content=env.to_json(), headers=headers)
    assert denied.status_code == 401
    assert denied.json()["detail"] == "stale locus timestamp"


def test_worker_service_template_nonce_registration_is_race_safe(monkeypatch) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")
    monkeypatch.setenv("LOCUS_A2A_NONCE_TTL_SECONDS", "60")

    module = _load_agent_service_template_module()
    module._SEEN_NONCES.clear()

    results: list[str] = []

    def _worker() -> None:
        try:
            module._register_seen_nonce_or_raise("race-nonce", now=100.0)
            results.append("accepted")
        except module.HTTPException as exc:
            results.append(f"error:{exc.status_code}")

    threads = [Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("accepted") == 1
    assert results.count("error:409") == 1


def test_topic_dispatcher_propagates_auth_context_in_strict_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    mapping_path = tmp_path / "topic-map.json"
    mapping_path.write_text(
        '{"security.compliance": ["https://worker.example.test/v1/envelope"]}', encoding="utf-8"
    )
    dispatcher = TopicDispatcher(mapping_path)

    captured: dict[str, object] = {}

    def _fake_post(url, env, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return {"accepted": True}

    monkeypatch.setattr("apps.workers.runtime.network.dispatcher.post_envelope", _fake_post)

    env = Envelope(
        topic="security.compliance",
        sender="orchestrator",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            }
        },
    )

    response = dispatcher.dispatch("security.compliance", env)

    assert response == {"accepted": True}
    assert captured["sub"] == "orchestrator"
    assert captured["actor"] == "tenant-user"
    assert captured["tenant_id"] == "acme"
    assert captured["internal_service"] is True
    assert env.payload["metrics"]["remote_dispatch_attempts"] == 1
    assert env.payload["metrics"]["remote_dispatch_successes"] == 1
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "network.dispatch" and item["outcome"] == "attempt"
        for item in trace_events
    )
    assert any(
        item["stage"] == "network.dispatch" and item["outcome"] == "delivered"
        for item in trace_events
    )


def test_topic_dispatcher_fails_closed_without_registered_url_in_strict_profile(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    mapping_path = tmp_path / "topic-map.json"
    mapping_path.write_text("{}", encoding="utf-8")
    dispatcher = TopicDispatcher(mapping_path)
    env = Envelope(
        topic="security.compliance",
        sender="orchestrator",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            }
        },
    )

    with pytest.raises(ValueError, match="No registered endpoint"):
        dispatcher.dispatch("security.compliance", env)

    assert any("remote dispatch blocked: no registered endpoint" in err for err in env.errors)
    assert env.payload["metrics"]["remote_dispatch_failures"] == 1
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "network.dispatch"
        and item["outcome"] == "error"
        and item.get("metadata", {}).get("reason") == "no_registered_url"
        for item in trace_events
    )


def test_topic_dispatcher_keeps_skip_semantics_in_lightweight_profile(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-lightweight")
    monkeypatch.delenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", raising=False)

    mapping_path = tmp_path / "topic-map.json"
    mapping_path.write_text("{}", encoding="utf-8")
    dispatcher = TopicDispatcher(mapping_path)
    env = Envelope(topic="security.compliance", sender="orchestrator")

    response = dispatcher.dispatch("security.compliance", env)

    assert response is None
    assert not env.errors
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "network.dispatch"
        and item["outcome"] == "skipped"
        and item.get("metadata", {}).get("reason") == "no_registered_url"
        for item in trace_events
    )


def test_orchestrator_remote_dispatch_records_failure_on_envelope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    mapping_path = tmp_path / "topic-map.json"
    mapping_path.write_text(
        '{"security.compliance": ["https://worker.example.test/v1/envelope"]}', encoding="utf-8"
    )
    orchestrator = Orchestrator(tmp_path / "registry.json")

    def _boom(*args, **kwargs):
        raise RuntimeError("dispatch exploded")

    monkeypatch.setattr("apps.workers.runtime.network.dispatcher.post_envelope", _boom)

    env = orchestrator.run_stage(
        name="remote-failure",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            },
        },
        dispatch_mode="remote",
        remote_map_path=mapping_path,
    )

    assert any("remote dispatch failed: dispatch exploded" in err for err in env.errors)
    assert env.payload["metrics"]["remote_dispatch_attempts"] == 1
    assert env.payload["metrics"]["remote_dispatch_failures"] == 1
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "network.dispatch"
        and item["outcome"] == "error"
        and item.get("metadata", {}).get("reason") == "dispatch exploded"
        for item in trace_events
    )
    assert any(
        item["stage"] == "orchestrator.dispatch"
        and item["outcome"] == "error"
        and item.get("metadata", {}).get("reason") == "dispatch exploded"
        for item in trace_events
    )


def test_orchestrator_done_when_exception_no_longer_passes_as_success(tmp_path) -> None:
    orchestrator = Orchestrator(tmp_path / "registry.json")

    env = orchestrator.run_stage(
        name="done-when-error",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            },
        },
        done_when=lambda _env: (_ for _ in ()).throw(RuntimeError("done_when exploded")),
    )

    assert any("stage completion check failed: done_when exploded" in err for err in env.errors)
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "orchestrator.done_when"
        and item["outcome"] == "error"
        and item.get("metadata", {}).get("reason") == "done_when exploded"
        for item in trace_events
    )


def test_local_event_bus_blocks_unauthorized_tenant_memory_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    called = {"value": False}

    def _subscriber(env):
        called["value"] = True

    orchestrator.bus.subscribe("security.compliance", _subscriber)
    env = orchestrator.run_stage(
        name="tenant-memory",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "other",
                "subject": "orchestrator",
                "internal_service": True,
            },
            "memory": {"action": "read", "scope": "tenant", "bucket_id": "tenant:acme"},
        },
    )

    assert called["value"] is False
    assert any("tenant-scoped memory access denied" in err for err in env.errors)
    assert env.payload["metrics"]["security_blocked"] >= 1
    assert env.payload["metrics"]["event_bus_delivery_blocked"] >= 1
    assert any(
        event.get("control") == "memory_authorization" and event.get("outcome") == "blocked"
        for event in env.payload.get("security_events", [])
    )


def test_local_event_bus_allows_authorized_tenant_memory_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    called = {"value": False}

    def _subscriber(env):
        called["value"] = True

    orchestrator.bus.subscribe("security.compliance", _subscriber)
    env = orchestrator.run_stage(
        name="tenant-memory",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            },
            "memory": {"action": "read", "scope": "tenant", "bucket_id": "tenant:acme"},
        },
    )

    assert called["value"] is True
    assert not env.errors
    assert env.payload["metrics"]["security_allowed"] >= 1
    assert any(
        event.get("control") == "memory_authorization" and event.get("outcome") == "allowed"
        for event in env.payload.get("security_events", [])
    )


def test_local_event_bus_blocks_conflicting_payload_tenant_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    called = {"value": False}

    def _subscriber(env: Envelope) -> None:
        called["value"] = True

    orchestrator.bus.subscribe("security.compliance", _subscriber)
    env = orchestrator.run_stage(
        name="tenant-isolation-mismatch",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            },
            "currentTenant": "other",
        },
    )

    assert called["value"] is False
    assert any("payload tenant context mismatch" in err for err in env.errors)
    assert any(
        event.get("control") == "tenant_isolation" and event.get("outcome") == "blocked"
        for event in env.payload.get("security_events", [])
    )
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "event_bus.delivery"
        and item["outcome"] == "blocked"
        and item.get("metadata", {}).get("reason") == "policy_denied"
        for item in trace_events
    )


def test_local_event_bus_allows_matching_payload_tenant_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    called = {"value": False}

    def _subscriber(env: Envelope) -> None:
        called["value"] = True

    orchestrator.bus.subscribe("security.compliance", _subscriber)
    env = orchestrator.run_stage(
        name="tenant-isolation-match",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
            },
            "currentTenant": "acme",
        },
    )

    assert called["value"] is True
    assert not env.errors
    assert any(
        event.get("control") == "tenant_isolation" and event.get("outcome") == "allowed"
        for event in env.payload.get("security_events", [])
    )


def test_multi_tenant_runtime_messages_do_not_cross_contaminate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    observed: list[tuple[str, str]] = []

    def _subscriber(env: Envelope) -> None:
        auth_context = env.payload.get("auth_context") if isinstance(env.payload, dict) else {}
        memory_auth = (
            env.payload.get("memory_authorization") if isinstance(env.payload, dict) else {}
        )
        observed.append(
            (str(auth_context.get("tenant_id") or ""), str(memory_auth.get("bucket_id") or ""))
        )

    orchestrator.bus.subscribe("security.compliance", _subscriber)

    tenants = ["acme", "globex"] * 10
    for tenant in tenants:
        env = orchestrator.run_stage(
            name=f"tenant-{tenant}",
            topic="security.compliance",
            payload={
                "auth_context": {
                    "actor": f"{tenant}-user",
                    "tenant_id": tenant,
                    "subject": "orchestrator",
                    "internal_service": True,
                },
                "currentTenant": tenant,
                "memory": {"action": "read", "scope": "tenant", "bucket_id": f"tenant:{tenant}"},
            },
        )
        assert not env.errors

    assert len(observed) == len(tenants)
    assert observed.count(("acme", "tenant:acme")) == 10
    assert observed.count(("globex", "tenant:globex")) == 10


def test_session_memory_scope_ignores_payload_session_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    called = {"value": False}

    def _subscriber(env: Envelope) -> None:
        called["value"] = True

    orchestrator.bus.subscribe("security.compliance", _subscriber)

    env = orchestrator.run_stage(
        name="session-memory-override",
        topic="security.compliance",
        payload={
            "auth_context": {
                "actor": "tenant-user",
                "tenant_id": "acme",
                "subject": "orchestrator",
                "internal_service": True,
                "session_id": "session-authenticated",
            },
            "memory": {
                "action": "read",
                "scope": "session",
                "session_id": "session-attacker",
            },
        },
    )

    assert called["value"] is True
    memory_auth = env.payload.get("memory_authorization") if isinstance(env.payload, dict) else {}
    assert memory_auth.get("bucket_id") == "session:session-authenticated"


def test_event_bus_records_time_budget_failure_metrics() -> None:
    bus = EventBus()
    called = {"value": False}

    def _subscriber(env: Envelope) -> None:
        called["value"] = True

    bus.subscribe("security.compliance", _subscriber)
    env = Envelope(
        topic="security.compliance",
        sender="backend",
        created_at_ms=int(time.time() * 1000) - 2_000,
    )
    env.budget.time_limit_ms = 100

    bus.publish("security.compliance", env)

    assert called["value"] is False
    assert any("time budget exceeded" in err for err in env.errors)
    assert env.payload["metrics"]["event_bus_delivery_attempts"] == 1
    assert env.payload["metrics"]["event_bus_delivery_blocked"] == 1
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "event_bus.delivery"
        and item["outcome"] == "blocked"
        and item.get("metadata", {}).get("reason") == "time_budget_exceeded"
        for item in trace_events
    )


def test_event_bus_records_subscriber_failures_in_trace() -> None:
    bus = EventBus()

    def _subscriber(_env: Envelope) -> None:
        raise RuntimeError("subscriber blew up")

    bus.subscribe("security.compliance", _subscriber)
    env = Envelope(topic="security.compliance", sender="backend")

    bus.publish("security.compliance", env)

    assert any("subscriber blew up" in err for err in env.errors)
    assert env.payload["metrics"]["event_bus_delivery_attempts"] == 1
    assert env.payload["metrics"]["event_bus_delivery_failures"] == 1
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "event_bus.delivery"
        and item["outcome"] == "error"
        and item.get("metadata", {}).get("reason") == "subscriber blew up"
        for item in trace_events
    )


def test_runtime_security_middleware_marks_unexpected_errors_as_security_errors(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    orchestrator = Orchestrator(tmp_path / "registry.json")
    called = {"value": False}

    def _subscriber(env: Envelope) -> None:
        called["value"] = True

    orchestrator.bus.subscribe("security.compliance", _subscriber)
    original = runtime_security_module.enforce_runtime_envelope_security

    def _boom(env: Envelope):
        raise RuntimeError("middleware exploded")

    monkeypatch.setattr(runtime_security_module, "enforce_runtime_envelope_security", _boom)
    try:
        env = orchestrator.run_stage(
            name="security-error",
            topic="security.compliance",
            payload={
                "auth_context": {
                    "actor": "tenant-user",
                    "tenant_id": "acme",
                    "subject": "orchestrator",
                    "internal_service": True,
                },
            },
        )
    finally:
        monkeypatch.setattr(runtime_security_module, "enforce_runtime_envelope_security", original)

    assert called["value"] is False
    assert any("security middleware error: middleware exploded" in err for err in env.errors)
    assert env.payload["metrics"]["security_errors"] >= 1
    assert env.payload["metrics"]["security_blocked"] == 0
    assert env.payload["_security_block_classification"] == "security_error"
    assert any(
        event.get("control") == "runtime_security_middleware"
        and event.get("outcome") == "error"
        and event.get("metadata", {}).get("exception_type") == "RuntimeError"
        for event in env.payload.get("security_events", [])
    )
    trace_events = [
        item["trace"]
        for item in env.payload.get("logs", [])
        if isinstance(item, dict) and "trace" in item
    ]
    assert any(
        item["stage"] == "event_bus.delivery"
        and item["outcome"] == "blocked"
        and item.get("metadata", {}).get("reason") == "security_error"
        for item in trace_events
    )
