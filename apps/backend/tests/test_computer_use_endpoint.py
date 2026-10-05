"""LOCUS-341: computer-use panic endpoint and posture control."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

from app.control_status import PostureFacts, evaluate_controls
from app.main import app

from locus_runtime.computer_use import controller as cu

client = TestClient(app)
HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}


@pytest.fixture(autouse=True)
def fresh_controller() -> Iterator[cu.ComputerUseController]:
    controller = cu.ComputerUseController("takeover")
    previous_default, previous_installed = cu._DEFAULT, cu._INSTALLED  # noqa: SLF001
    cu.install_controller(controller)
    try:
        yield controller
    finally:
        cu._DEFAULT, cu._INSTALLED = previous_default, previous_installed  # noqa: SLF001


def test_panic_requires_authentication(fresh_controller: cu.ComputerUseController) -> None:
    response = client.post("/computer-use/panic")
    assert response.status_code == 401
    assert not fresh_controller.panicked


def test_panic_cancels_in_flight_actions_latches_and_is_idempotent(
    fresh_controller: cu.ComputerUseController,
) -> None:
    token = fresh_controller.begin("ui_type")
    first = client.post("/computer-use/panic", headers=HEADERS)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["panicked"] is True and body["already_latched"] is False
    assert body["cancelled_actions"] == 1 and body["latency_ms"] < 100
    assert token.cancelled
    with pytest.raises(cu.ComputerUseCancelled):
        fresh_controller.begin("ui_click")
    second = client.post("/computer-use/panic", headers=HEADERS)
    assert second.status_code == 200 and second.json()["already_latched"] is True

    status = client.get("/computer-use/status", headers=HEADERS).json()
    assert status["panicked"] is True and status["installed"] is True

    assert client.post("/computer-use/reset").status_code == 401
    reset = client.post("/computer-use/reset", headers=HEADERS)
    assert reset.status_code == 200 and reset.json()["panicked"] is False
    assert reset.json()["mode"] == "observe"


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


@pytest.mark.parametrize(
    ("installed", "gateway", "expected"),
    [
        (False, False, "off"),
        (False, True, "off"),
        (True, False, "unverified"),
        (True, True, "enforced"),
    ],
)
def test_computer_use_control_needs_the_wiring_and_an_enforcing_gateway(
    installed: bool, gateway: bool, expected: str
) -> None:
    states = {
        control.id: control.state
        for control in evaluate_controls(
            _facts(computer_use_installed=installed, gateway_enforcing=gateway)
        )
    }
    assert states["computer_use"] == expected
