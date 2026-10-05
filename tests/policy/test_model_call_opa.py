"""LOCUS-336: gateway ``model_call`` decisions against the real Rego (OPA sidecar).

The gateway maps a model call onto existing policies -- no new Rego:
``agent_policy`` (operation ``llm_call``, its data-ceiling rule), ``network_egress``
(the engine host) and ``budget_policy`` (token/cost figures from usage).
Skips locally without an OPA binary; CI sets ``LOCUS_REQUIRE_OPA=1``.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from locus_runtime import model_client as mc
from locus_runtime.gateway import (
    BudgetFigures,
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    GatewaySession,
)
from locus_runtime.policy_engine import OpaSidecarEngine

NIM_HOST = "integrate.api.nvidia.com"


def _caps(**overrides: object) -> Capabilities:
    base = Capabilities(
        allowed_tools=frozenset({"llm_call"}),
        allowed_egress_hosts=(NIM_HOST, "127.0.0.1", "localhost"),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture()
def audit() -> list[GatewayAuditRecord]:
    return []


@pytest.fixture()
def gateway(opa_engine: OpaSidecarEngine, audit: list[GatewayAuditRecord]) -> Gateway:
    return Gateway(opa_engine, audit.append)


def _session(gateway: Gateway, **overrides: object) -> GatewaySession:
    return gateway.open_session(
        run_id="run-model", principal="alice", engine="model.nim", capabilities=_caps(**overrides)
    )


def _call(provider: str, host: str) -> mc.ModelCall:
    return mc.ModelCall(provider=provider, model="m", egress_host=host)


def _decide(session: GatewaySession, call: mc.ModelCall) -> tuple[str, tuple[str, ...], str]:
    decision = session.authorize(**call.gateway_kwargs())
    return decision.outcome, decision.reasons, decision.risk.label


def test_allowlisted_hosted_and_local_engines_are_allowed(gateway: Gateway) -> None:
    session = _session(gateway)
    assert _decide(session, _call("nim", NIM_HOST))[::2] == ("allow", "R2")
    assert _decide(session, _call("ollama", "127.0.0.1"))[::2] == ("allow", "R1")


def test_non_allowlisted_engine_host_is_denied(gateway: Gateway) -> None:
    outcome, reasons, _risk = _decide(_session(gateway), _call("nim", "evil-nim.example.com"))
    assert outcome == "deny"
    assert any(reason.startswith("network_egress") for reason in reasons)


def test_model_call_needs_the_llm_call_capability(gateway: Gateway) -> None:
    session = _session(gateway, allowed_tools=frozenset({"read_file"}))
    assert _decide(session, _call("nim", NIM_HOST))[0] == "deny"


def test_restricted_data_only_reaches_local_engines(gateway: Gateway) -> None:
    session = _session(gateway, data_classification="restricted")
    assert _decide(session, _call("nim", NIM_HOST))[0] == "deny"
    assert _decide(session, _call("ollama", "127.0.0.1"))[0] == "allow"
    # A hosted provider cannot claim to be local: "local" is derived from the host.
    assert _decide(session, _call("ollama", NIM_HOST))[0] == "deny"
    confidential = _session(gateway, data_classification="confidential")
    assert _decide(confidential, _call("nim", NIM_HOST))[0] == "allow"


def test_exhausted_token_budget_denies(gateway: Gateway) -> None:
    within = _session(gateway, budget=BudgetFigures(tokens_used=10, max_tokens=100))
    assert _decide(within, _call("nim", NIM_HOST))[0] == "allow"
    over = _session(gateway, budget=BudgetFigures(tokens_used=101, max_tokens=100))
    assert _decide(over, _call("nim", NIM_HOST))[0] == "deny"


def test_client_denied_by_real_policy_sends_nothing_and_audits_no_prompt(
    gateway: Gateway, audit: list[GatewayAuditRecord]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = mc.ModelClient(
        mc.ModelEndpoint("nim", "m", "https://nim.attacker.example/v1", api_key="nvapi-x" * 3),
        gate=mc.GatewayModelGate(session=_session(gateway)),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(mc.ModelCallDenied):
        client.complete([{"role": "user", "content": "PROMPT-SHOULD-NOT-BE-AUDITED"}])
    assert requests == []
    assert audit and audit[-1].outcome == "deny"
    assert "PROMPT-SHOULD-NOT-BE-AUDITED" not in json.dumps([r.as_metadata() for r in audit])


def test_embedding_calls_get_the_same_policy_as_chat(gateway: Gateway) -> None:
    """LOCUS-378: memory embeddings are model_call actions under the same Rego."""

    def embedding(provider: str, host: str) -> mc.ModelCall:
        return mc.ModelCall(provider=provider, model="m", egress_host=host, operation="embeddings")

    session = _session(gateway)
    assert _decide(session, embedding("ollama", "127.0.0.1"))[::2] == ("allow", "R1")
    assert _decide(session, embedding("nim", "evil-nim.example.com"))[0] == "deny"
    restricted = _session(gateway, data_classification="restricted")
    assert _decide(restricted, embedding("ollama", "127.0.0.1"))[0] == "allow"
    assert _decide(restricted, embedding("nim", NIM_HOST))[0] == "deny"
