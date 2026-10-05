"""LOCUS-336: backend model calls through the unified, gated client.

An in-process mock OpenAI-compatible server (``httpx.MockTransport``) stands in
for NIM and Ollama; the keychain is the in-memory double from conftest.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.control_status import PostureFacts, evaluate_controls
from app.graph_compiler import AgentResolution
from app.main import app
from locus_runtime import model_client as mc
from tests.gateway_support import FixedAuthorizer, installed

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}
NIM_KEY = "nvapi-backend-test-key-424242"
SECRET_PROMPT = "BACKEND-SECRET-PROMPT-5150"
NIM_MODEL = "meta/llama-3.3-70b-instruct"


class MockServer:
    """Answers chat completions for every host; scripted replies per call."""

    def __init__(self, replies: list[dict[str, Any]] | None = None) -> None:
        self.replies = list(replies or [])
        self.requests: list[httpx.Request] = []
        self.fail_hosts: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host in self.fail_hosts:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        payload = json.loads(request.content or b"{}")
        if payload.get("stream"):
            events = [
                {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                for piece in ("str", "eam")
            ]
            body = "".join(
                f"data: {json.dumps({'id': 'c', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'm', **event})}\n\n"
                for event in events
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(body + "data: [DONE]\n\n").encode(),
            )
        message = self.replies.pop(0) if self.replies else {"role": "assistant", "content": "done"}
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": payload.get("model"),
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            },
        )

    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> MockServer:
    mock = MockServer()
    original = mc.ModelClient.__init__

    def init(self: mc.ModelClient, endpoint: mc.ModelEndpoint, **kwargs: Any) -> None:
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(mock.handler))
        kwargs["max_retries"] = 0
        original(self, endpoint, **kwargs)

    monkeypatch.setattr(mc.ModelClient, "__init__", init)
    monkeypatch.setattr(main_module.local_models, "_BASE_URL_OVERRIDE", "", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.delenv(mc.AGENT_CHAIN_ENV, raising=False)
    main_module._PROVIDER_CLIENTS.clear()
    yield mock
    main_module._PROVIDER_CLIENTS.clear()


def _audit(action: str) -> list[dict[str, Any]]:
    return [
        dict(event.metadata or {})
        for event in main_module.store.audit_events
        if event.action == action
    ]


def _resolution(provider: str, model: str, source: str = "agent") -> AgentResolution:
    return AgentResolution(
        agent_id="a",
        system_prompt="s",
        model=model,
        provider=provider,
        base_url="",
        model_source=source,
    )


# --------------------------------------------------------------------------- #
# Harness client
# --------------------------------------------------------------------------- #
def test_harness_client_runs_on_nim_when_configured(server: MockServer, permissive_gateway) -> None:
    main_module._MODEL_KEYS.set("nim", NIM_KEY)
    harness = main_module._make_harness_chat_client(
        _resolution("nim", NIM_MODEL), run_id="run-h1", principal="alice"
    )
    response = harness.complete([{"role": "user", "content": SECRET_PROMPT}])
    assert response.text == "done"
    assert (harness.provider, harness.model) == ("nim", NIM_MODEL)
    request = server.requests[0]
    assert request.url.host == "integrate.api.nvidia.com"
    assert request.headers["authorization"] == f"Bearer {NIM_KEY}"
    action = permissive_gateway.actions[-1]
    assert action.kind == "model_call" and action.tool == "model:nim"
    usage = [item for item in _audit("model.call") if item.get("run_id") == "run-h1"]
    assert usage and usage[-1]["input_count"] == 12 and usage[-1]["output_count"] == 4
    assert usage[-1]["provider"] == "nim" and usage[-1]["est_cost_usd"] == 0.0
    assert SECRET_PROMPT not in json.dumps(_audit("model.call"))
    assert NIM_KEY not in json.dumps([e.model_dump() for e in main_module.store.audit_events])


def test_harness_fallback_nim_to_ollama_is_recorded(server: MockServer) -> None:
    server.fail_hosts.add("integrate.api.nvidia.com")
    main_module._MODEL_KEYS.set("nim", NIM_KEY)
    run_id = "run-fallback-1"
    harness = main_module._make_harness_chat_client(
        _resolution("nim", NIM_MODEL), run_id=run_id, principal="alice"
    )
    assert harness.complete([{"role": "user", "content": "hi"}]).text == "done"
    assert server.hosts() == ["integrate.api.nvidia.com", "127.0.0.1"]
    assert harness.provider == "ollama"
    events = [
        e
        for e in main_module.store.run_events.get(run_id, [])
        if (e.metadata or {}).get("phase") == "model_fallback"
    ]
    assert len(events) == 1
    meta = events[0].metadata or {}
    assert (meta["from_provider"], meta["to_provider"]) == ("nim", "ollama")
    assert meta["reason_code"] == "provider_call_failed" and "503" in meta["reason"]
    assert any(item.get("run_id") == run_id for item in _audit("model.fallback"))


def test_agent_naming_no_engine_uses_the_d21_chain(server: MockServer) -> None:
    resolution = main_module._agent_resolution_for_node({})
    assert (resolution.provider, resolution.model_source) == ("nim", "default")
    tiers = main_module._harness_model_chain(resolution)
    assert [tier.provider for tier in tiers] == ["nim", "ollama"]
    # No NIM key: the call is served by Ollama and the fallback says why.
    harness = main_module._make_harness_chat_client(resolution, run_id="run-default")
    harness.complete([{"role": "user", "content": "hi"}])
    assert server.hosts() == ["127.0.0.1"]
    reasons = [e.reason_code for e in harness.fallbacks]
    assert reasons == ["provider_not_configured"]
    # An explicit local engine is honoured without hosted escalation.
    local = _resolution("ollama", "gpt-oss:20b")
    assert [t.provider for t in main_module._harness_model_chain(local)] == ["ollama"]


# --------------------------------------------------------------------------- #
# Backend chat
# --------------------------------------------------------------------------- #
def test_backend_tool_loop_runs_on_nim(server: MockServer) -> None:
    main_module._MODEL_KEYS.set("nim", NIM_KEY)
    server.replies = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q": "x"}'},
                }
            ],
        },
        {"role": "assistant", "content": "final answer"},
    ]
    calls: list[tuple[str, dict[str, Any]]] = []
    text, meta = main_module._run_openai_chat(
        system_prompt="sys",
        user_prompt=SECRET_PROMPT,
        model=f"nim/{NIM_MODEL}",
        temperature=0.1,
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        tool_executor=lambda name, args: calls.append((name, args)) or "result",
    )
    assert text == "final answer"
    assert meta["provider"] == "nim" and meta["tool_calls_made"] == 1
    assert calls == [("lookup", {"q": "x"})]
    assert set(server.hosts()) == {"integrate.api.nvidia.com"}


def test_backend_chat_denied_by_gateway_sends_nothing(server: MockServer) -> None:
    main_module._MODEL_KEYS.set("nim", NIM_KEY)
    with installed(FixedAuthorizer("deny")):
        with pytest.raises(main_module.ProviderUnavailableError) as denied:
            main_module._run_openai_chat(
                system_prompt="", user_prompt="hi", model=f"nim/{NIM_MODEL}", temperature=0.1
            )
    assert denied.value.code == "model_call_denied" and denied.value.status_code == 403
    assert server.requests == []


def test_model_call_session_egress_is_registry_plus_operator_hosts() -> None:
    hosts = main_module._model_call_egress_hosts("nim")
    assert "integrate.api.nvidia.com" in hosts
    assert "evil.example.com" not in hosts
    assert {"localhost", "127.0.0.1"} <= set(main_module._model_call_egress_hosts("ollama"))


def test_user_runtime_streaming_goes_through_the_unified_client(
    server: MockServer, permissive_gateway
) -> None:
    pieces: list[str] = []
    chunks, meta = main_module._collect_chat_response_chunks(
        system_prompt="s",
        user_prompt="u",
        model="custom-model",
        temperature=0.0,
        runtime={
            "provider": "openai-compatible",
            "model": "custom-model",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key": "sk-user-runtime-key-123456",
        },
        on_chunk=pieces.append,
    )
    assert chunks == pieces == ["str", "eam"]
    assert meta["transport"] == "sse"
    assert permissive_gateway.actions[-1].kind == "model_call"
    assert server.requests[0].url.host == "127.0.0.1"


def test_skill_test_without_model_uses_the_d21_chain(server: MockServer) -> None:
    response = client.post(
        "/skills/skill-commit/test",
        json={"prompt": "Commit the current changes."},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "ollama"  # NIM has no key here: explicit fallback
    assert server.hosts() == ["127.0.0.1"]
    assert any(item.get("from_provider") == "nim" for item in _audit("model.fallback"))


# --------------------------------------------------------------------------- #
# Keys: endpoint and migration
# --------------------------------------------------------------------------- #
def test_provider_key_endpoint_sets_and_clears_without_echo(in_memory_keychain) -> None:
    secret = "nvapi-endpoint-secret-777777"
    response = client.put(
        "/models/providers/nim/key", json={"api_key": secret}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert response.json()["configured"] is True and response.json()["storage"] == "keychain"
    assert in_memory_keychain.store[("lattix-locus", "NVIDIA_API_KEY")] == secret
    assert main_module.store.platform_settings.nim_api_key == ""

    settings_view = client.get("/platform/settings", headers=ADMIN_HEADERS)
    assert settings_view.json()["nim_api_key_configured"] is True
    assert secret not in settings_view.text
    assert secret not in json.dumps([e.model_dump() for e in main_module.store.audit_events])

    cleared = client.delete("/models/providers/nim/key", headers=ADMIN_HEADERS)
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert ("lattix-locus", "NVIDIA_API_KEY") not in in_memory_keychain.store


def test_provider_key_endpoint_access_and_validation() -> None:
    reader = {"x-locus-actor": "tester"}
    denied = client.put("/models/providers/nim/key", json={"api_key": "x" * 10}, headers=reader)
    assert denied.status_code in {401, 403}
    assert (
        client.put(
            "/models/providers/nope/key", json={"api_key": "x"}, headers=ADMIN_HEADERS
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/models/providers/ollama/key", json={"api_key": "x"}, headers=ADMIN_HEADERS
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/models/providers/nim/key", json={"api_key": "  "}, headers=ADMIN_HEADERS
        ).status_code
        == 400
    )


def test_startup_migrates_stored_keys_into_the_keychain(in_memory_keychain) -> None:
    settings = main_module.store.platform_settings
    original_nim, original_providers = settings.nim_api_key, dict(settings.ai_providers)
    try:
        settings.nim_api_key = "nvapi-legacy-stored-123456"
        settings.ai_providers = {
            "mistral": {"api_key": "mistral-legacy-key-1", "default_model": ""}
        }
        assert main_module._migrate_provider_keys_to_keychain() == 2
        assert settings.nim_api_key == ""
        assert settings.ai_providers["mistral"]["api_key"] == ""
        assert main_module._provider_api_key("nim") == "nvapi-legacy-stored-123456"
        assert main_module._provider_api_key("mistral") == "mistral-legacy-key-1"
        assert (
            in_memory_keychain.store[("lattix-locus", "MISTRAL_API_KEY")] == "mistral-legacy-key-1"
        )
        assert main_module._migrate_provider_keys_to_keychain() == 0  # idempotent
    finally:
        settings.nim_api_key = original_nim
        settings.ai_providers = original_providers
        main_module._apply_provider_settings_side_effects()


def test_posture_reports_gated_model_calls_control() -> None:
    facts = PostureFacts(
        auth_required=True,
        a2a_signed_messages=True,
        a2a_trusted_subject_count=1,
        a2a_replay_protection=True,
        egress_allowlist=True,
        guardrail_signals_enabled=True,
        guardrail_signal_enforcement="block_high",
        presidio_flag=False,
        presidio_state="not_loaded",
        audit_durable=False,
        sandbox_requested=True,
        sandbox_strategy="kernel-bwrap",
        policy_engine_available=True,
        biscuit_loaded=False,
        vault_addr_configured=False,
        envoy_authz_filters=False,
        nats_loaded=False,
        secret_storage_mode="keychain",
    )

    def state(**overrides: Any) -> str:
        controls = evaluate_controls(dataclasses.replace(facts, **overrides))
        return next(c.state for c in controls if c.id == "model_calls_gated")

    assert state() == "off"
    assert state(model_gate_installed=True) == "unverified"
    assert state(model_gate_installed=True, gateway_enforcing=True) == "enforced"
    assert mc.model_gate_installed() is True  # the backend installed its gate on import
