"""LOCUS-378 / LOCUS-387: memory embeddings go through the gated model client.

Every HTTP exchange runs against ``httpx.MockTransport``; nothing touches the
network. The gate is either a recording double or the real ``GatewayModelGate``
over a test gateway authorizer.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from locus_runtime import model_client as mc
from locus_runtime.memory.contract import EmbeddingUnavailable
from locus_runtime.memory.embedder import DEFAULT_EMBEDDING_MODEL, GatedEmbedder
from locus_runtime.memory.sqlite_store import SQLiteLongTermMemoryStore
from tests.gateway_support import AllowAllAuthorizer, FixedAuthorizer, installed

SECRET_TEXT = "PRIVATE-MEMORY-9931"


class EmbeddingServer:
    """A mock OpenAI-compatible ``/embeddings`` endpoint (Ollama's shape)."""

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(
                self.status, json={"error": {"message": 'model "nomic-embed-text" not found'}}
            )
        payload = json.loads(request.content or b"{}")
        inputs = payload["input"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": payload["model"],
                "data": [
                    {"object": "embedding", "index": i, "embedding": [float(i + 1)] * 8}
                    for i in range(len(inputs))
                ],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
        )


class RecordingGate:
    def __init__(self) -> None:
        self.calls: list[mc.ModelCall] = []
        self.usage: list[mc.ModelUsage] = []

    def authorize(self, call: mc.ModelCall) -> str:
        self.calls.append(call)
        return f"audit-{len(self.calls)}"

    def record(self, call: mc.ModelCall, usage: mc.ModelUsage) -> None:  # noqa: ARG002
        self.usage.append(usage)


class _Settings:
    def value(self, provider: str, field: str) -> str:
        if field == "base_url" and provider == "ollama":
            return "http://127.0.0.1:11434/v1"
        return ""


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _embedder(server: EmbeddingServer, gate: Any, **kwargs: Any) -> GatedEmbedder:
    def factory(endpoint: mc.ModelEndpoint) -> mc.ModelClient:
        return mc.ModelClient(
            endpoint,
            gate=gate,
            http_client=httpx.Client(transport=httpx.MockTransport(server.handler)),
            max_retries=0,
        )

    return GatedEmbedder(
        model=kwargs.pop("model", DEFAULT_EMBEDDING_MODEL),
        provider=kwargs.pop("provider", "ollama"),
        settings=_Settings(),
        client_factory=factory,
        **kwargs,
    )


def test_embeddings_are_a_gated_model_call_with_no_text_in_the_audit() -> None:
    server, gate = EmbeddingServer(), RecordingGate()
    vectors = _embedder(server, gate).embed([SECRET_TEXT, "second"])
    assert vectors == [[1.0] * 8, [2.0] * 8]
    (call,) = gate.calls
    assert call.operation == "embeddings" and call.provider == "ollama"
    assert call.model == DEFAULT_EMBEDDING_MODEL and call.egress_host == "127.0.0.1"
    kwargs = call.gateway_kwargs()
    assert kwargs["kind"] == "model_call" and kwargs["tool"] == "model:ollama"
    assert kwargs["args"]["operation"] == "embeddings"
    assert SECRET_TEXT not in json.dumps(kwargs)
    (usage,) = gate.usage
    assert usage.ok and usage.tokens_in == 5 and usage.audit_id == "audit-1"
    assert SECRET_TEXT not in json.dumps(usage.as_metadata())
    assert server.requests[0].url.path == "/v1/embeddings"


def test_chat_calls_keep_their_gateway_args() -> None:
    call = mc.ModelCall(provider="ollama", model="m", egress_host="127.0.0.1")
    assert "operation" not in call.gateway_kwargs()["args"]


def test_policy_denial_means_no_request_and_memory_degrades() -> None:
    server = EmbeddingServer()
    with installed(FixedAuthorizer("deny")):
        embedder = _embedder(server, mc.GatewayModelGate())
        with pytest.raises(EmbeddingUnavailable):
            embedder.embed([SECRET_TEXT])
    assert server.requests == []
    status = embedder.status()
    assert status.state == "pending_embedding_model" and "denied" in status.reason
    assert SECRET_TEXT not in status.reason


def test_real_gateway_gate_sees_the_embedding_action() -> None:
    server = EmbeddingServer()
    with installed(AllowAllAuthorizer()) as authorizer:
        _embedder(server, mc.GatewayModelGate()).embed(["hello"])
    (action,) = authorizer.actions
    assert action.kind == "model_call" and action.tool == "model:ollama"


def test_hosted_providers_are_refused_before_any_request() -> None:
    server, gate = EmbeddingServer(), RecordingGate()
    embedder = _embedder(server, gate, provider="openai")
    with pytest.raises(EmbeddingUnavailable) as refused:
        embedder.embed(["x"])
    assert "local engine only" in str(refused.value)
    assert gate.calls == [] and server.requests == []


def test_missing_model_backs_off_then_recovers() -> None:
    server, gate, clock = EmbeddingServer(status=404), RecordingGate(), _Clock()
    embedder = _embedder(server, gate, retry_seconds=60, clock=clock)
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["a"])
    assert "not available on the local engine" in embedder.status().reason
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["a"])  # inside the back-off window: no request
    assert len(server.requests) == 1
    clock.now += 61
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["a"])  # retried once, back-off doubles
    assert len(server.requests) == 2
    clock.now += 61
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["a"])
    assert len(server.requests) == 2
    server.status = 200
    clock.now += 120
    assert embedder.embed(["a"]) == [[1.0] * 8]
    assert embedder.status().ready


def test_an_empty_model_disables_semantic_search() -> None:
    embedder = _embedder(EmbeddingServer(), RecordingGate(), model="")
    assert embedder.status().state == "disabled"
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["a"])


def test_store_embeds_through_the_gate(tmp_path: Any) -> None:
    server, gate = EmbeddingServer(), RecordingGate()
    store = SQLiteLongTermMemoryStore(str(tmp_path / "m.db"), embedder=_embedder(server, gate))
    store.append_entry(
        bucket_id="b",
        session_id="b",
        memory_scope="agent",
        entry={"content": SECRET_TEXT},
        source="t",
    )
    assert gate.calls == []  # writes never wait for the engine
    assert store.backfill_embeddings() == 1
    assert [call.operation for call in gate.calls] == ["embeddings"]
    store.close()
