"""LOCUS-313: posture surfaces report true control state (P9)."""

from __future__ import annotations

import itertools
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.control_status as control_status
from app.control_status import (
    CONTROL_STATES,
    PostureFacts,
    build_control_status_report,
    evaluate_controls,
)
from app.main import app

client = TestClient(app)
READ_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}

DECLARED_ONLY_CONTROLS = {
    "policy_engine_rego",
    "capability_tokens_biscuit",
    "secret_broker_vault",
    "gateway_envoy_authz",
    "messaging_nats",
}


def _facts(**overrides: object) -> PostureFacts:
    base = PostureFacts(
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
        policy_engine_available=False,
        biscuit_loaded=False,
        vault_addr_configured=False,
        envoy_authz_filters=False,
        nats_loaded=False,
        secret_storage_mode="keychain",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _states(facts: PostureFacts) -> dict[str, str]:
    return {control.id: control.state for control in evaluate_controls(facts)}


@pytest.mark.parametrize(
    ("rego", "biscuit", "vault", "envoy", "nats"),
    list(
        itertools.product(
            [False, True], [False, True], [False, True], [None, False, True], [False, True]
        )
    ),
)
def test_declared_only_controls_never_report_enforced(
    rego: bool, biscuit: bool, vault: bool, envoy: bool | None, nats: bool
) -> None:
    states = _states(
        _facts(
            policy_engine_available=rego,
            biscuit_loaded=biscuit,
            vault_addr_configured=vault,
            envoy_authz_filters=envoy,
            nats_loaded=nats,
        )
    )
    for control_id in DECLARED_ONLY_CONTROLS:
        assert states[control_id] in {"off", "unverified"}, control_id


def test_declared_only_controls_are_off_when_nothing_is_present() -> None:
    states = _states(_facts())
    assert {states[control_id] for control_id in DECLARED_ONLY_CONTROLS} == {"off"}


def test_every_control_has_a_known_state_and_evidence() -> None:
    report = build_control_status_report(_facts())
    assert report["controls"]
    for item in report["controls"]:
        assert item["state"] in CONTROL_STATES
        assert item["evidence"].strip()
        assert item["label"].strip()
    assert sum(report["summary"].values()) == len(report["controls"])


@pytest.mark.parametrize(
    "strategy", ["kernel-bwrap", "kernel-seatbelt", "windows-appcontainer", "hardened-docker"]
)
def test_sandbox_planner_reports_enforced_when_on_execution_path(strategy: str) -> None:
    assert _states(_facts(sandbox_requested=True, sandbox_strategy=strategy))[
        "execution_sandbox"
    ] == ("enforced")


@pytest.mark.parametrize(
    ("requested", "strategy", "expected"),
    [
        (False, "kernel-bwrap", "off"),
        (True, "restricted-process", "degraded"),
        (True, "k8s-gvisor", "unverified"),
        (True, "k8s-kata", "unverified"),
        (True, None, "unverified"),
        (None, "kernel-bwrap", "unverified"),
    ],
)
def test_sandbox_states_follow_runtime_facts(
    requested: bool | None, strategy: str | None, expected: str
) -> None:
    facts = _facts(sandbox_requested=requested, sandbox_strategy=strategy)
    assert _states(facts)["execution_sandbox"] == expected


def test_flag_alone_does_not_make_presidio_enforced() -> None:
    assert _states(_facts(presidio_flag=False))["pii_analyzer_presidio"] == "off"
    assert (
        _states(_facts(presidio_flag=True, presidio_state="not_loaded"))["pii_analyzer_presidio"]
        == "unverified"
    )
    assert (
        _states(_facts(presidio_flag=True, presidio_state="unavailable"))["pii_analyzer_presidio"]
        == "degraded"
    )
    assert (
        _states(_facts(presidio_flag=True, presidio_state="loaded"))["pii_analyzer_presidio"]
        == "enforced"
    )


def test_egress_allowlist_and_audit_log_never_claim_full_enforcement() -> None:
    states = _states(_facts(egress_allowlist=True, audit_durable=True))
    assert states["egress_allowlist"] == "degraded"
    assert states["audit_log"] == "degraded"
    assert _states(_facts(egress_allowlist=False))["egress_allowlist"] == "off"


def test_collect_posture_facts_reads_real_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delitem(sys.modules, "nats", raising=False)
    facts = control_status.collect_posture_facts(
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
    )
    assert facts.vault_addr_configured is False
    assert facts.nats_loaded is False
    # The shipped Envoy config has no authz filters.
    assert facts.envoy_authz_filters is False


@pytest.mark.parametrize(
    ("mode", "expected", "evidence"),
    [
        ("keychain", "enforced", "OS keychain"),
        ("dpapi_file", "degraded", "DPAPI-encrypted file"),
        ("plaintext_file_opt_in", "degraded", "plaintext file (opt-in, LOCUS-317)"),
        ("env_only", "unverified", "environment"),
        ("unavailable", "off", "fail closed"),
        (None, "unverified", "could not be determined"),
        ("bogus", "unverified", "could not be determined"),
    ],
)
def test_secret_storage_control_follows_storage_mode(
    mode: str | None, expected: str, evidence: str
) -> None:
    controls = {c.id: c for c in evaluate_controls(_facts(secret_storage_mode=mode))}
    assert controls["secret_storage"].state == expected
    assert evidence in controls["secret_storage"].evidence


def test_collect_posture_facts_reads_secret_storage_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from locus_tooling import native_secrets

    monkeypatch.setattr(native_secrets, "_RESOLVED", {})
    monkeypatch.setenv(native_secrets.STORAGE_MODE_ENV, "dpapi_file")
    facts = control_status.collect_posture_facts(
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
    )
    assert facts.secret_storage_mode == "dpapi_file"
    # A launcher "keychain" claim is not trusted without a usable keychain backend.
    monkeypatch.setenv(native_secrets.STORAGE_MODE_ENV, "keychain")
    monkeypatch.setattr(native_secrets, "_keychain_backend", lambda: None)
    assert control_status._detect_secret_storage_mode() == "env_only"
    monkeypatch.delenv(native_secrets.STORAGE_MODE_ENV)
    assert control_status._detect_secret_storage_mode() == "env_only"


def _patch_sandbox(monkeypatch: pytest.MonkeyPatch, requested: bool, strategy: str) -> None:
    monkeypatch.setattr(control_status, "_sandbox_executor_requested", lambda: requested)
    monkeypatch.setattr(control_status, "_detect_sandbox_strategy", lambda: strategy)


def test_platform_security_policy_lists_only_enforced_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sandbox(monkeypatch, True, "kernel-bwrap")
    response = client.get("/platform/security-policy", headers=READ_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    controls = {item["id"]: item for item in payload["control_status"]["controls"]}
    enforced = {cid for cid, item in controls.items() if item["state"] == "enforced"}
    assert set(payload["backend_enforced_controls"]) == enforced
    assert not DECLARED_ONLY_CONTROLS & enforced
    assert controls["execution_sandbox"]["state"] == "enforced"
    # Previously-declared rails that have no proven code path are gone.
    for stale in ("capability_filter", "policy_gate_filter", "readonly_sandbox_rootfs"):
        assert stale not in payload["backend_enforced_controls"]


def test_atf_report_and_health_details_share_the_control_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sandbox(monkeypatch, False, "kernel-bwrap")
    atf = client.get("/audit/atf-alignment-report", headers=READ_HEADERS)
    assert atf.status_code == 200
    atf_payload = atf.json()
    atf_states = {item["id"]: item["state"] for item in atf_payload["control_status"]["controls"]}
    assert atf_states["execution_sandbox"] == "off"
    for control_id in DECLARED_ONLY_CONTROLS:
        assert atf_states[control_id] != "enforced"
    # Audit is never hash-chained today, so behavior monitoring cannot be "strong",
    # and an unconfined sandbox keeps segmentation partial.
    assert atf_payload["pillars"]["behavior_monitoring"]["status"] == "partial"
    assert atf_payload["pillars"]["segmentation"]["status"] == "partial"
    assert atf_payload["maturity_estimate"] != "principal"

    health = client.get("/healthz/details", headers=READ_HEADERS)
    assert health.status_code == 200
    health_states = {
        item["id"]: item["state"] for item in health.json()["control_status"]["controls"]
    }
    assert health_states == atf_states


def test_policy_engine_is_unverified_when_available_and_off_otherwise() -> None:
    # LOCUS-328: the engine exists but no execution-path gateway calls it yet.
    assert _states(_facts(policy_engine_available=True))["policy_engine_rego"] == "unverified"
    assert _states(_facts(policy_engine_available=False))["policy_engine_rego"] == "off"


def test_policy_engine_availability_is_a_runtime_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import locus_runtime.policy_engine as policy_engine

    monkeypatch.setattr(policy_engine, "find_opa_binary", lambda: None)
    monkeypatch.delenv("LOCUS_OPA_URL", raising=False)
    assert control_status._policy_engine_available() is False
    monkeypatch.setenv("LOCUS_OPA_URL", "http://10.0.0.5:8181")
    assert control_status._policy_engine_available() is False
    monkeypatch.setenv("LOCUS_OPA_URL", "http://127.0.0.1:8181")
    assert control_status._policy_engine_available() is True
