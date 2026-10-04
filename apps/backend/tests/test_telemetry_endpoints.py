"""LOCUS-375: telemetry read API, exporter settings and posture."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

from app import capability_widening as cw  # noqa: E402
from app import main as main_module  # noqa: E402
from app.control_status import PostureFacts, evaluate_controls  # noqa: E402
from app.main import PlatformSettings, app  # noqa: E402
from app.request_security import (  # noqa: E402
    RouteAccessCategory,
    classify_route_access,
    classify_shell_proof,
)
from locus_runtime import telemetry  # noqa: E402
from locus_runtime.telemetry import semconv as sc  # noqa: E402
from locus_runtime.telemetry.contract import TelemetrySettings  # noqa: E402

client = TestClient(app)
HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}


@pytest.fixture()
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "telemetry.db"
    monkeypatch.setenv("LOCUS_TELEMETRY_DB", str(db))
    telemetry.configure(TelemetrySettings(db_path=str(db)), synchronous=True)
    for run_id, state in (("run-1", "done"), ("run-2", "blocked")):
        with telemetry.agent_run(run_id=run_id, agent="a", runtime="verified-loop") as run:
            with telemetry.tool_call("execute_bash"):
                pass
            with telemetry.gate("verify", run_id=run_id):
                telemetry.record_score("verification", 1.0, label=state)
            run.set(sc.LOCUS_END_STATE, state)
    yield db
    telemetry.reset()


@pytest.fixture()
def restore_settings() -> Iterator[None]:
    saved = main_module.store.platform_settings
    yield
    main_module.store.platform_settings = saved
    telemetry.reset()


def test_routes_are_authenticated_reads_and_not_mutating() -> None:
    for path in ("/telemetry/runs", "/telemetry/runs/r1/trace", "/telemetry/summary"):
        rule = classify_route_access("GET", path)
        assert rule is not None and rule.category == RouteAccessCategory.AUTHENTICATED_READ
        assert classify_shell_proof("GET", path) is None
    # The exporter settings ride on POST /platform/settings (state predicate).
    rule = classify_shell_proof("POST", "/platform/settings")
    assert rule is not None and rule.predicate == "platform_settings_widening"


def test_reads_require_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    for path in ("/telemetry/runs", "/telemetry/runs/r1/trace", "/telemetry/summary"):
        assert client.get(path).status_code == 401


def test_runs_listing_filters_and_paging(seeded: Path) -> None:
    response = client.get("/telemetry/runs", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2 and [r["run_id"] for r in body["runs"]] == ["run-2", "run-1"]
    assert body["runs"][1]["tool_calls"] == 1
    assert body["runs"][1]["scores"][0]["label"] == "done"
    blocked = client.get("/telemetry/runs?end_state=blocked", headers=HEADERS).json()
    assert [r["run_id"] for r in blocked["runs"]] == ["run-2"]
    page = client.get("/telemetry/runs?limit=1&offset=1", headers=HEADERS).json()
    assert [r["run_id"] for r in page["runs"]] == ["run-1"] and page["total"] == 2
    assert client.get("/telemetry/runs?limit=0", headers=HEADERS).status_code == 400
    assert client.get("/telemetry/runs?since=yesterday", headers=HEADERS).status_code == 400
    future = client.get("/telemetry/runs?since=2200-01-01T00:00:00Z", headers=HEADERS).json()
    assert future["total"] == 0
    for bad in ("since=2999-01-01T00:00:00Z", "until=-5", "since=1e30"):
        assert client.get(f"/telemetry/runs?{bad}", headers=HEADERS).status_code == 400


def test_trace_returns_the_span_tree(seeded: Path) -> None:
    response = client.get("/telemetry/runs/run-1/trace", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    (root,) = body["roots"]
    assert root["span"]["operation"] == sc.OP_INVOKE_AGENT
    assert {c["span"]["operation"] for c in root["children"]} == {sc.OP_EXECUTE_TOOL, sc.OP_GATE}
    assert body["scores"][0]["name"] == "verification"
    assert client.get("/telemetry/runs/nope/trace", headers=HEADERS).status_code == 404
    assert client.get("/telemetry/runs/bad%20id/trace", headers=HEADERS).status_code == 400


def test_summary_over_a_window(seeded: Path) -> None:
    response = client.get("/telemetry/summary?window_hours=1", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runs"] == 2 and body["tool_calls"] == 2
    assert body["gate_outcomes"] == {"verification": {"done": 1, "blocked": 1}}
    assert body["run_latency_ms"]["count"] == 2
    assert client.get("/telemetry/summary?window_hours=0", headers=HEADERS).status_code == 400


def test_empty_store_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    telemetry.reset()
    monkeypatch.setenv("LOCUS_TELEMETRY_DB", str(tmp_path / "absent.db"))
    assert client.get("/telemetry/runs", headers=HEADERS).json()["total"] == 0
    assert client.get("/telemetry/summary", headers=HEADERS).json()["runs"] == 0


# --------------------------------------------------------------------------- #
# Exporter settings
# --------------------------------------------------------------------------- #
def _defaults() -> dict[str, Any]:
    return PlatformSettings().model_dump()


def test_exporters_are_off_by_default_and_classified() -> None:
    defaults = PlatformSettings()
    assert defaults.telemetry_capture_content is False
    assert defaults.telemetry_otlp_enabled is False
    assert defaults.telemetry_langsmith_enabled is False
    assert cw.TELEMETRY_FIELDS == {
        f for f in PlatformSettings.model_fields if f.startswith("telemetry_")
    }


@pytest.mark.parametrize(
    ("change", "widens"),
    [
        ({"telemetry_otlp_enabled": True}, True),
        ({"telemetry_langsmith_enabled": True}, True),
        ({"telemetry_capture_content": True}, True),
        ({"telemetry_otlp_endpoint": "https://collector.example.test/v1/traces"}, True),
        (
            {"telemetry_langsmith_endpoint": "https://eu.api.smith.langchain.com/otel/v1/traces"},
            True,
        ),
        ({"telemetry_otlp_auth_secret_ref": "LANGFUSE_OTLP_AUTH"}, True),
        ({"telemetry_payload_retention_days": 365}, True),
        ({"telemetry_payload_retention_days": 30}, False),
        ({"telemetry_langsmith_project": "other"}, False),
    ],
)
def test_telemetry_widening_predicate(change: dict[str, Any], widens: bool) -> None:
    old = _defaults()
    new = {**old, **change}
    assert bool(cw.telemetry_widening_fields(old, new)) is widens
    assert cw.is_widening(old, new) is widens


def test_disabling_or_clearing_an_exporter_narrows() -> None:
    enabled = {
        **_defaults(),
        "telemetry_otlp_enabled": True,
        "telemetry_otlp_endpoint": "https://collector.example.test/v1/traces",
        "telemetry_capture_content": True,
    }
    disabled = {
        **enabled,
        "telemetry_otlp_enabled": False,
        "telemetry_otlp_endpoint": "",
        "telemetry_capture_content": False,
    }
    assert cw.telemetry_widening_fields(enabled, disabled) == []


def test_enabling_an_exporter_needs_confirmation(restore_settings: None) -> None:
    payload = {
        "telemetry_otlp_enabled": True,
        "telemetry_otlp_endpoint": "https://collector.example.test/api/public/otel/v1/traces",
    }
    response = client.post("/platform/settings", headers=HEADERS, json=payload)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert {"telemetry_otlp_enabled", "telemetry_otlp_endpoint"} <= set(
        detail["changed_sensitive_keys"]
    )
    confirmed = client.post(
        "/platform/settings", headers=HEADERS, json={**payload, "confirm_security_change": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    # Saved, but the host is not on the egress allowlist: blocked, never active.
    (otlp, langsmith) = telemetry.posture().external
    assert (otlp.state, otlp.reason, otlp.destination) == (
        "blocked",
        "egress_not_allowed",
        "remote",
    )
    assert langsmith.state == "off"
    # Disabling narrows: no confirmation needed.
    off = client.post("/platform/settings", headers=HEADERS, json={"telemetry_otlp_enabled": False})
    assert off.status_code == 200, off.text
    assert telemetry.posture().external[0].state == "off"


@pytest.mark.parametrize(
    "payload",
    [
        {"telemetry_otlp_endpoint": "http://collector.example.test/v1/traces"},
        {"telemetry_otlp_endpoint": "https://user:pw@collector.example.test/v1"},
        {"telemetry_otlp_auth_secret_ref": "sk-not-a-secret-name"},
        {"telemetry_langsmith_project": "bad\r\nheader"},
        {"telemetry_payload_retention_days": 0},
    ],
)
def test_invalid_telemetry_settings_are_rejected(
    payload: dict[str, Any], restore_settings: None
) -> None:
    response = client.post(
        "/platform/settings", headers=HEADERS, json={**payload, "confirm_security_change": True}
    )
    assert response.status_code == 400, response.text


def _facts(telemetry_posture: dict[str, Any]) -> PostureFacts:
    return PostureFacts(
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
        telemetry=telemetry_posture,
    )


def _telemetry_control(telemetry_posture: dict[str, Any]) -> Any:
    (control,) = [
        c for c in evaluate_controls(_facts(telemetry_posture)) if c.id == "telemetry_local"
    ]
    return control


def test_posture_reports_local_and_each_exporter(tmp_path: Path) -> None:
    telemetry.configure(
        TelemetrySettings(db_path=str(tmp_path / "t.db")), egress_check=None, synchronous=True
    )
    local_only = _telemetry_control(telemetry.posture().model_dump())
    assert local_only.state == "enforced"
    assert "content capture off" in local_only.evidence

    langsmith = telemetry.posture().model_dump()
    langsmith["external"][1].update(
        state="active", host="api.smith.langchain.com", destination="hosted_proprietary"
    )
    hosted = _telemetry_control(langsmith)
    assert hosted.state == "degraded"
    assert "hosted, proprietary; data leaves the machine" in hosted.evidence

    telemetry.reset()
    assert _telemetry_control(telemetry.posture().model_dump()).state == "off"
    report = client.get("/platform/security-policy", headers=HEADERS).json()
    assert "telemetry_local" in {c["id"] for c in report["control_status"]["controls"]}
