"""LOCUS-336: unified model client -- providers, keys, gated calls, streaming, fallback.

Every HTTP exchange runs against an in-process mock OpenAI-compatible server
(``httpx.MockTransport``); nothing touches the network or the real keychain.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from locus_runtime import gateway as gw
from locus_runtime import model_client as mc
from locus_runtime.harness.llm import GatedChatClient
from locus_runtime.harness.model_profiles import resolve_profile
from tests.gateway_support import AllowAllAuthorizer, FakeEngine, FixedAuthorizer, installed

SECRET_PROMPT = "TOP-SECRET-PROMPT-7731"
NIM_KEY = "nvapi-unit-test-key-0123456789"


# --------------------------------------------------------------------------- #
# Mock OpenAI-compatible server
# --------------------------------------------------------------------------- #
class MockServer:
    """Records requests; answers chat completions (plain, tool call, or SSE stream)."""

    def __init__(self, *, mode: str = "text", status: int = 200, body: str = "") -> None:
        self.mode = mode
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(request.content or b"{}") for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": self.body}})
        payload = json.loads(request.content or b"{}")
        if payload.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=self._sse(payload).encode("utf-8"),
            )
        message: dict[str, Any] = {"role": "assistant", "content": "hello from mock"}
        finish = "stop"
        if self.mode == "tool":
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                    }
                ],
            }
            finish = "tool_calls"
        return httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": payload.get("model"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            },
        )

    def _sse(self, payload: dict[str, Any]) -> str:
        def event(delta: dict[str, Any], finish: str | None = None, usage: Any = None) -> str:
            body: dict[str, Any] = {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": payload.get("model"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if usage is not None:
                body["choices"] = []
                body["usage"] = usage
            return f"data: {json.dumps(body)}\n\n"

        parts = [event({"role": "assistant", "content": ""})]
        if self.mode == "tool":
            parts.append(
                event(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_9",
                                "type": "function",
                                "function": {"name": "run_tests", "arguments": '{"tar'},
                            }
                        ]
                    }
                )
            )
            parts.append(
                event({"tool_calls": [{"index": 0, "function": {"arguments": 'get": "all"}'}}]})
            )
            parts.append(event({}, "tool_calls"))
        else:
            for piece in ("Hel", "lo ", "stream"):
                parts.append(event({"content": piece}))
            parts.append(event({}, "stop"))
        if (payload.get("stream_options") or {}).get("include_usage"):
            parts.append(
                event({}, usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})
            )
        parts.append("data: [DONE]\n\n")
        return "".join(parts)


class RecordingGate:
    """ModelCallGate double: allows, and records calls and usage."""

    def __init__(self) -> None:
        self.calls: list[mc.ModelCall] = []
        self.usage: list[mc.ModelUsage] = []

    def authorize(self, call: mc.ModelCall) -> str:
        self.calls.append(call)
        return f"audit-{len(self.calls)}"

    def record(self, call: mc.ModelCall, usage: mc.ModelUsage) -> None:  # noqa: ARG002
        self.usage.append(usage)


def _client(
    server: MockServer,
    provider: str = "nim",
    *,
    gate: Any = None,
    base_url: str = "",
    model: str = "",
    api_key: str = NIM_KEY,
) -> mc.ModelClient:
    spec = mc.PROVIDERS[provider]
    endpoint = mc.ModelEndpoint(
        provider=provider,
        model=model or spec.default_model,
        base_url=base_url or spec.default_base_url,
        api_key=api_key if spec.key_env else "",
    )
    return mc.ModelClient(
        endpoint,
        gate=gate or RecordingGate(),
        http_client=httpx.Client(transport=httpx.MockTransport(server.handler)),
        max_retries=0,
    )


@pytest.fixture(autouse=True)
def _hermetic_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from locus_tooling import native_secrets

    monkeypatch.setattr(native_secrets, "_dpapi_read", lambda name, app_home: None)
    for spec in mc.PROVIDERS.values():
        for name in spec.key_env:
            monkeypatch.delenv(name, raising=False)
    for name in (mc.AGENT_CHAIN_ENV, mc.LOCAL_FALLBACK_ENV, mc.PRICES_ENV, "NIM_MODEL"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# Provider resolution and keys
# --------------------------------------------------------------------------- #
def test_provider_resolution_and_registry() -> None:
    assert mc.resolve_provider("nim/meta/llama-3.3-70b-instruct") == (
        "nim",
        "meta/llama-3.3-70b-instruct",
    )
    assert mc.resolve_provider("OLLAMA/gpt-oss:20b") == ("ollama", "gpt-oss:20b")
    assert mc.resolve_provider("gpt-5.2") == ("openai", "gpt-5.2")
    assert mc.resolve_provider("gpt-5.2", default="") == ("", "gpt-5.2")
    assert mc.PROVIDERS["nim"].default_base_url == "https://integrate.api.nvidia.com/v1"
    assert "integrate.api.nvidia.com" in mc.PROVIDERS["nim"].allowlisted_hosts
    assert mc.PROVIDERS["ollama"].local and not mc.PROVIDERS["ollama"].key_required


def test_default_chain_is_nim_then_ollama_and_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = mc.default_agent_chain()
    assert [tier.provider for tier in chain] == ["nim", "ollama"]
    assert chain[0].model == mc.PROVIDERS["nim"].default_model

    monkeypatch.setenv("NIM_MODEL", "openai/gpt-oss-120b")
    assert mc.default_agent_chain()[0].qualified == "nim/openai/gpt-oss-120b"

    monkeypatch.setenv(mc.AGENT_CHAIN_ENV, "nim/meta/llama-3.1-8b-instruct, ollama, bogus-model")
    chain = mc.default_agent_chain()
    assert [tier.qualified for tier in chain] == [
        "nim/meta/llama-3.1-8b-instruct",
        f"ollama/{mc.PROVIDERS['ollama'].default_model}",
    ]


def test_chain_for_honours_explicit_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    assert [t.provider for t in mc.chain_for("ollama", "gpt-oss:20b", explicit=True)] == ["ollama"]
    hosted = mc.chain_for("nim", "meta/llama-3.3-70b-instruct", explicit=True)
    assert [t.provider for t in hosted] == ["nim", "ollama"]
    monkeypatch.setenv(mc.LOCAL_FALLBACK_ENV, "0")
    assert [t.provider for t in mc.chain_for("nim", "x", explicit=True)] == ["nim"]
    assert [t.provider for t in mc.chain_for("ollama", "", explicit=False)] == ["nim", "ollama"]


def test_keys_resolve_env_then_keychain_and_set_clear_round_trip(
    monkeypatch: pytest.MonkeyPatch, in_memory_keychain: Any
) -> None:
    keys = mc.ProviderKeyStore()
    assert keys.get("nim") == "" and keys.source("nim") == "none"
    with pytest.raises(mc.ModelProviderError) as missing:
        mc.resolve_endpoint("nim", keys=keys)
    assert missing.value.code == mc.PROVIDER_NOT_CONFIGURED
    assert missing.value.http_status == 412

    assert keys.set("nim", NIM_KEY) == "keychain"
    assert ("lattix-locus", "NVIDIA_API_KEY") in in_memory_keychain.store
    assert keys.get("nim") == NIM_KEY and keys.source("nim") == "keychain"
    endpoint = mc.resolve_endpoint("nim", keys=keys)
    assert endpoint.api_key == NIM_KEY
    assert endpoint.egress_host == "integrate.api.nvidia.com"
    assert NIM_KEY not in repr(endpoint)

    # The alias env var wins over the keychain (operator-supplied).
    monkeypatch.setenv("NIM_API_KEY", "nvapi-from-env-999999")
    assert keys.get("nim") == "nvapi-from-env-999999" and keys.source("nim") == "env"
    monkeypatch.delenv("NIM_API_KEY")

    keys.clear("nim")
    assert keys.get("nim") == ""
    assert ("lattix-locus", "NVIDIA_API_KEY") not in in_memory_keychain.store
    # Ollama needs no key.
    assert mc.resolve_endpoint("ollama", "gpt-oss:20b", keys=keys).local is True


def test_nim_profile_uses_openai_tools_and_configured_context() -> None:
    profile = resolve_profile("nim", "meta/llama-3.3-70b-instruct")
    assert profile.profile_id == "nim-hosted"
    assert profile.tool_protocol == "native-fc"
    assert profile.max_effective_context >= 32_768
    assert resolve_profile("nim", "openai/gpt-oss-120b").profile_id == "nim-gpt-oss"


# --------------------------------------------------------------------------- #
# Calls through the mock server
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("provider", "base_url", "host"),
    [
        ("nim", "", "integrate.api.nvidia.com"),
        ("ollama", "http://127.0.0.1:11434/v1", "127.0.0.1"),
    ],
)
def test_non_streaming_completion(provider: str, base_url: str, host: str) -> None:
    server = MockServer()
    gate = RecordingGate()
    client = _client(server, provider, gate=gate, base_url=base_url)
    result = client.complete([{"role": "user", "content": "hi"}], temperature=0.1)
    assert result.text == "hello from mock"
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 7}
    request = server.requests[0]
    assert request.url.host == host
    assert request.url.path.endswith("/chat/completions")
    if provider == "nim":
        assert request.headers["authorization"] == f"Bearer {NIM_KEY}"
    assert gate.calls[0].egress_host == host and gate.calls[0].provider == provider
    usage = gate.usage[0]
    assert (usage.tokens_in, usage.tokens_out, usage.usage_reported) == (11, 7, True)
    assert usage.est_cost_usd == 0.0 and usage.cost_known  # NIM free tier / local


@pytest.mark.parametrize("provider", ["nim", "ollama"])
def test_tool_calls(provider: str) -> None:
    server = MockServer(mode="tool")
    client = _client(
        server, provider, base_url="http://localhost:11434/v1" if provider == "ollama" else ""
    )
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    result = client.complete([{"role": "user", "content": "go"}], tools=tools)
    assert result.tool_calls == [
        {"id": "call_1", "name": "read_file", "arguments": '{"path": "a.py"}'}
    ]
    sent = server.payloads()[0]
    assert sent["tools"] == tools and sent["tool_choice"] == "auto"


@pytest.mark.parametrize("provider", ["nim", "ollama"])
def test_streaming_text_and_usage(provider: str) -> None:
    server = MockServer()
    gate = RecordingGate()
    client = _client(
        server,
        provider,
        gate=gate,
        base_url="http://localhost:11434/v1" if provider == "ollama" else "",
    )
    pieces: list[str] = []
    result = client.stream([{"role": "user", "content": "hi"}], on_chunk=pieces.append)
    assert pieces == ["Hel", "lo ", "stream"]
    assert result.text == "Hello stream"
    assert gate.calls[0].stream is True
    usage = gate.usage[0]
    if provider == "ollama":  # asks for stream usage; the server reports it
        assert server.payloads()[0]["stream_options"] == {"include_usage": True}
        assert (usage.tokens_in, usage.tokens_out, usage.usage_reported) == (5, 3, True)
    else:  # NIM: no stream_options sent; usage is estimated, flagged as such
        assert "stream_options" not in server.payloads()[0]
        assert usage.usage_reported is False and usage.tokens_in > 0


def test_streaming_tool_call_deltas_are_assembled() -> None:
    server = MockServer(mode="tool")
    result = _client(server).stream([{"role": "user", "content": "x"}])
    assert result.tool_calls == [
        {"id": "call_9", "name": "run_tests", "arguments": '{"target": "all"}'}
    ]
    assert result.finish_reason == "tool_calls"


def test_sdk_shaped_surface_is_gated() -> None:
    server = MockServer()
    gate = RecordingGate()
    client = _client(server, gate=gate)
    response = client.chat.completions.create(
        model="meta/llama-3.1-8b-instruct", messages=[{"role": "user", "content": "hi"}]
    )
    assert response.choices[0].message.content == "hello from mock"
    assert gate.calls[0].model == "meta/llama-3.1-8b-instruct"


def test_provider_failure_is_typed_and_redacted() -> None:
    server = MockServer(status=401, body=f"invalid key {NIM_KEY} Bearer {NIM_KEY}")
    with pytest.raises(mc.ModelProviderError) as failure:
        _client(server).complete([{"role": "user", "content": SECRET_PROMPT}])
    err = failure.value
    assert err.code == mc.PROVIDER_CALL_FAILED and err.http_status == 424
    assert "401" in err.reason
    assert NIM_KEY not in str(err) and NIM_KEY not in err.reason
    assert SECRET_PROMPT not in str(err)


def test_denied_call_never_reaches_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    server = MockServer()
    with installed(FixedAuthorizer("deny")):
        client = _client(server, gate=mc.GatewayModelGate())
        with pytest.raises(mc.ModelCallDenied) as denied:
            client.complete([{"role": "user", "content": "hi"}])
    assert denied.value.code == mc.MODEL_CALL_DENIED and denied.value.http_status == 403
    assert server.requests == []
    # No installed gate and no gateway: the default gate fails closed.
    monkeypatch.setattr(mc, "_PROCESS_GATE_FACTORY", None)
    with installed(None):
        bare = mc.ModelClient(
            mc.ModelEndpoint("ollama", "m", "http://localhost:11434/v1"),
            http_client=httpx.Client(transport=httpx.MockTransport(server.handler)),
        )
        with pytest.raises(mc.ModelCallDenied):
            bare.complete([{"role": "user", "content": "hi"}])
    assert server.requests == []


# --------------------------------------------------------------------------- #
# Gateway mapping and audit
# --------------------------------------------------------------------------- #
def test_model_call_risk_class_local_r1_hosted_r2() -> None:
    assert gw.classify_risk(kind="model_call", egress_host="127.0.0.1") == gw.RiskClass.R1
    assert gw.classify_risk(kind="model_call", egress_host="localhost") == gw.RiskClass.R1
    assert (
        gw.classify_risk(kind="model_call", egress_host="integrate.api.nvidia.com")
        == gw.RiskClass.R2
    )
    assert gw.classify_risk(kind="model_call") == gw.RiskClass.R2  # unknown host = hosted


def _gateway_session(
    engine: FakeEngine, audit: list[gw.GatewayAuditRecord], **caps: Any
) -> gw.GatewaySession:
    gateway = gw.Gateway(engine, audit.append)
    capabilities = gw.Capabilities(
        allowed_tools=frozenset({"llm_call"}),
        allowed_egress_hosts=("integrate.api.nvidia.com",),
        **caps,
    )
    return gateway.open_session(
        run_id="run-mc", principal="alice", engine="model.nim", capabilities=capabilities
    )


def test_gateway_policy_inputs_and_audit_carry_no_prompt_text() -> None:
    engine = FakeEngine()
    audit: list[gw.GatewayAuditRecord] = []
    session = _gateway_session(
        engine,
        audit,
        data_classification="restricted",
        budget=gw.BudgetFigures(tokens_used=10, max_tokens=1000),
    )
    usage: list[mc.ModelUsage] = []
    gate = mc.GatewayModelGate(session=session, usage_sink=usage.append)
    server = MockServer()
    result = _client(server, gate=gate).complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": SECRET_PROMPT}]
    )
    assert result.text == "hello from mock"

    policies = {policy: payload for policy, payload in engine.calls}
    agent = policies["agent_policy"]
    assert agent["tool"] == "llm_call" and agent["provider"] == "nim"
    assert agent["classification"] == "restricted"
    assert policies["network_egress"]["target"] == "integrate.api.nvidia.com"
    assert policies["budget_policy"] == {"tokens_used": 10, "max_tokens": 1000}

    record = audit[0]
    assert record.action_kind == "model_call" and record.tool == "model:nim"
    assert record.risk_class == "R2" and record.outcome == "allow"
    assert usage[0].audit_id == record.audit_id
    assert (usage[0].tokens_in, usage[0].tokens_out) == (11, 7)
    for item in (*(r.as_metadata() for r in audit), *(u.as_metadata() for u in usage)):
        assert SECRET_PROMPT not in json.dumps(item)
    assert SECRET_PROMPT not in json.dumps(engine.calls)


def test_local_engine_is_reported_as_local_to_policy() -> None:
    engine = FakeEngine()
    audit: list[gw.GatewayAuditRecord] = []
    gateway = gw.Gateway(engine, audit.append)
    session = gateway.open_session(
        run_id="r",
        principal="p",
        engine="model.ollama",
        capabilities=gw.Capabilities(
            allowed_tools=frozenset({"llm_call"}), allowed_egress_hosts=("127.0.0.1",)
        ),
    )
    gate = mc.GatewayModelGate(session=session)
    _client(MockServer(), "ollama", gate=gate, base_url="http://127.0.0.1:11434/v1").complete(
        [{"role": "user", "content": "hi"}]
    )
    assert dict(engine.calls)["agent_policy"]["provider"] == "local"
    assert audit[0].risk_class == "R1"


def test_session_factory_gets_fresh_session_and_closes_it() -> None:
    opened: list[gw.GatewaySession] = []
    gateway = gw.Gateway(FakeEngine(), lambda _r: None)

    def factory(call: mc.ModelCall) -> gw.GatewaySession:
        session = gateway.open_session(
            run_id="r",
            principal="p",
            engine=f"model.{call.provider}",
            capabilities=gw.Capabilities(
                allowed_tools=frozenset({"llm_call"}),
                allowed_egress_hosts=(call.egress_host,),
            ),
        )
        opened.append(session)
        return session

    gate = mc.GatewayModelGate(session_factory=factory)
    client = _client(MockServer(), gate=gate)
    client.complete([{"role": "user", "content": "a"}])
    client.complete([{"role": "user", "content": "b"}])
    assert len(opened) == 2
    assert gateway._authenticate(opened[0].caller) is None  # noqa: SLF001 - closed after use


# --------------------------------------------------------------------------- #
# Fallback and the harness client
# --------------------------------------------------------------------------- #
def _router(
    nim: MockServer | None, ollama: MockServer, events: list[mc.FallbackEvent]
) -> mc.ModelRouter:
    tiers = [
        mc.ModelTier("nim", "meta/llama-3.3-70b-instruct"),
        mc.ModelTier("ollama", "gpt-oss:20b"),
    ]

    def factory(tier: mc.ModelTier) -> mc.ModelClient:
        if tier.provider == "nim":
            if nim is None:
                return mc.build_client(tier, keys=mc.ProviderKeyStore())  # no key -> 412
            return _client(nim, "nim", model=tier.model)
        return _client(ollama, "ollama", base_url="http://localhost:11434/v1", model=tier.model)

    return mc.ModelRouter(tiers, client_factory=factory, on_fallback=events.append)


def test_fallback_nim_to_ollama_is_recorded_with_reason() -> None:
    events: list[mc.FallbackEvent] = []
    nim, ollama = MockServer(status=503, body="overloaded"), MockServer()
    router = _router(nim, ollama, events)
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.text == "hello from mock"
    assert (result.provider, result.model) == ("ollama", "gpt-oss:20b")
    assert len(events) == 1 and result.fallbacks == events
    event = events[0]
    assert (event.from_provider, event.to_provider) == ("nim", "ollama")
    assert event.reason_code == mc.PROVIDER_CALL_FAILED and "503" in event.reason
    assert router.provider == "ollama"
    # A failed call is retried on NIM next time (not sticky).
    router.complete([{"role": "user", "content": "again"}])
    assert len(nim.requests) == 2


def test_unconfigured_nim_falls_back_once_and_stays_skipped() -> None:
    events: list[mc.FallbackEvent] = []
    ollama = MockServer()
    router = _router(None, ollama, events)
    router.complete([{"role": "user", "content": "hi"}])
    router.complete([{"role": "user", "content": "hi"}])
    assert [e.reason_code for e in events] == [mc.PROVIDER_NOT_CONFIGURED]
    assert "API key missing" in events[0].reason
    assert len(ollama.requests) == 2


def test_all_tiers_failing_raises_typed_error() -> None:
    events: list[mc.FallbackEvent] = []
    router = _router(MockServer(status=500), MockServer(status=500), events)
    with pytest.raises(mc.ModelProviderError) as failure:
        router.complete([{"role": "user", "content": "hi"}])
    assert failure.value.code == mc.PROVIDER_CALL_FAILED
    assert "all model tiers failed" in failure.value.reason
    assert len(events) == 1


def test_streaming_fallback_only_before_first_chunk() -> None:
    events: list[mc.FallbackEvent] = []
    router = _router(MockServer(status=503), MockServer(), events)
    pieces: list[str] = []
    result = router.stream([{"role": "user", "content": "hi"}], on_chunk=pieces.append)
    assert "".join(pieces) == "Hello stream" == result.text
    assert len(events) == 1


def test_gated_chat_client_drives_the_harness_contract() -> None:
    events: list[mc.FallbackEvent] = []
    router = _router(MockServer(mode="tool"), MockServer(), events)
    client = GatedChatClient(router)
    assert (client.provider, client.model) == ("nim", "meta/llama-3.3-70b-instruct")
    response = client.complete([{"role": "user", "content": "go"}], max_tokens=64)
    assert response.tool_calls[0].name == "read_file"
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7}
    assert response.raw is not None
    assert events == []


def test_estimate_cost_uses_configured_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    assert mc.estimate_cost("nim", "m", 1000, 1000) == (0.0, True)
    assert mc.estimate_cost("openai", "gpt-5.2", 1000, 1000) == (0.0, False)
    monkeypatch.setenv(mc.PRICES_ENV, json.dumps({"openai/gpt-5.2": [2.0, 8.0], "nim": [1, 1]}))
    assert mc.estimate_cost("openai", "gpt-5.2", 1_000_000, 500_000) == (6.0, True)
    assert mc.estimate_cost("nim", "x", 1_000_000, 0) == (1.0, True)


def test_usage_meter_budget_figures() -> None:
    meter = mc.UsageMeter()
    assert meter.budget("r", max_tokens=None) is None
    meter.add(mc.ModelUsage("nim", "m", 30, 20, 0.5, True, True, "a", 1, True, run_id="r"))
    figures = meter.budget("r", max_tokens=100, max_cost_usd=2.0)
    assert figures is not None
    assert figures.as_input() == {
        "tokens_used": 50.0,
        "max_tokens": 100.0,
        "cost_used_usd": 0.5,
        "max_cost_usd": 2.0,
    }


def test_allow_all_gateway_double_still_sees_model_calls() -> None:
    authorizer = AllowAllAuthorizer()
    with installed(authorizer):
        _client(MockServer(), gate=mc.GatewayModelGate()).complete(
            [{"role": "user", "content": SECRET_PROMPT}]
        )
    action = authorizer.actions[0]
    assert (
        action.kind == "model_call" and action.target == f"nim/{mc.PROVIDERS['nim'].default_model}"
    )
    assert SECRET_PROMPT not in json.dumps(dict(action.args_summary))
