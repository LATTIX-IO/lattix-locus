"""LOCUS-346: the backend installs the computer-use controller and wires runs to it."""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app import control_status, graph_compiler, policy_gateway
from fastapi.routing import APIRoute

from locus_runtime.computer_use import controller as cu
from locus_runtime.gateway import Gateway, GatewaySession
from tests.gateway_support import FakeEngine, installed


@pytest.fixture(autouse=True)
def controller_state() -> Iterator[None]:
    previous = cu._DEFAULT, cu._INSTALLED  # noqa: SLF001
    cu._DEFAULT, cu._INSTALLED = None, False  # noqa: SLF001
    try:
        yield
    finally:
        cu._DEFAULT, cu._INSTALLED = previous  # noqa: SLF001


def _computer_use_state() -> str:
    facts = control_status.PostureFacts(
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
        # The live process facts this ticket wires:
        gateway_enforcing=control_status._gateway_enforcing(),  # noqa: SLF001
        computer_use_installed=control_status._computer_use_installed(),  # noqa: SLF001
    )
    states = {c.id: c.state for c in control_status.evaluate_controls(facts)}
    return states["computer_use"]


def test_enforcing_gateway_installs_the_controller_and_posture_is_enforced() -> None:
    with installed(Gateway(FakeEngine(running=True), lambda _r: None)):
        assert policy_gateway.ensure_computer_use_controller() is True
        assert cu.controller_installed()
        # Same instance the panic / status endpoints act on.
        assert cu.get_controller() is cu.get_controller()
        assert policy_gateway.ensure_computer_use_controller() is True  # idempotent
        assert _computer_use_state() == "enforced"


def test_non_enforcing_gateway_installs_nothing_and_posture_is_off() -> None:
    with installed(Gateway(FakeEngine(running=False), lambda _r: None)):
        assert policy_gateway.ensure_computer_use_controller() is False
        assert not cu.controller_installed()
        assert _computer_use_state() == "off"


def test_startup_installs_the_controller_after_the_gateway() -> None:
    source = inspect.getsource(main_module._startup_initialize_state)  # noqa: SLF001
    assert source.index("_ensure_gateway()") < source.index(
        "policy_gateway.ensure_computer_use_controller()"
    )


def test_panic_and_status_endpoint_paths_are_unchanged() -> None:
    routes = {
        (route.path, method)
        for route in main_module.app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert ("/computer-use/panic", "POST") in routes
    assert ("/computer-use/status", "GET") in routes
    assert ("/computer-use/reset", "POST") in routes


def test_computer_use_request_keeps_only_known_tools_and_bounded_apps() -> None:
    tools, apps = policy_gateway.computer_use_request(
        {
            "tools": ["browser_read", "rm_rf", "desktop_click", "browser_read"],
            "apps": ["notepad.exe", "", "x" * 500, "notepad.exe", "Calculator"],
        }
    )
    assert tools == ("browser_read", "desktop_click")
    assert apps == ("notepad.exe", "Calculator")
    assert policy_gateway.computer_use_request(None) == ((), ())
    assert policy_gateway.computer_use_request({"tools": "browser_read"}) == ((), ())


def test_harness_session_grants_computer_use_operations_and_apps(tmp_path: Path) -> None:
    opened: list[GatewaySession] = []
    with installed(Gateway(FakeEngine(running=True), lambda _r: None)):
        factory = policy_gateway.harness_session_factory(
            run_id="run-cu",
            principal="alice",
            egress=("example.com",),
            on_decision=None,
            opened=opened,
            computer_use_tools=("browser_navigate", "desktop_click"),
            apps=("notepad.exe",),
        )
        session = factory(tmp_path, [])
        plain = policy_gateway.harness_session_factory(
            run_id="run-plain", principal="alice", egress=(), on_decision=None, opened=opened
        )(tmp_path, [])
    assert session is not None and plain is not None
    assert {"browser_navigate", "network_egress", "ui_click"} <= session.capabilities.allowed_tools
    assert session.capabilities.allowed_apps == ("notepad.exe",)
    assert plain.capabilities.allowed_tools == policy_gateway.HARNESS_OPERATIONS
    assert plain.capabilities.allowed_apps == ()
    policy_gateway.close_sessions(opened)


def test_code_node_passes_computer_use_tools_to_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.harness import swe_agent

    seen: dict[str, Any] = {}

    class RecordingAgent:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def solve(self, task: Any) -> Any:
            return SimpleNamespace(
                has_patch=False, answer="ok", patch="", outcome="submitted", steps=1
            )

    monkeypatch.setattr(swe_agent, "SweAgent", RecordingAgent)
    deps = graph_compiler.CompilerDeps(
        resolve_agent=lambda cfg: None,  # type: ignore[arg-type,return-value]
        make_chat_client=lambda r: object(),
        execute_native=lambda *a: {},
        computer_use_tools=("browser_read",),
        computer_use_apps=("notepad.exe",),
    )
    deps.provisioned = SimpleNamespace(
        workspace=SimpleNamespace(executor=SimpleNamespace(workdir=lambda: str(tmp_path))),
        binding=SimpleNamespace(test_command="", allow_outside="ask"),
    )
    node = SimpleNamespace(id="code-1", title="Code")
    resolution = graph_compiler.AgentResolution(
        agent_id="engineer", system_prompt="", model="m", provider="ollama", base_url=""
    )
    result = graph_compiler._delegate_to_swe_agent(node, resolution, "do it", deps)  # noqa: SLF001
    assert result["mode"] == "code", result
    assert seen["computer_use_tools"] == ("browser_read",)
    assert seen["computer_use_apps"] == ("notepad.exe",)


def test_desktop_hotkey_call_is_authenticated_by_the_local_operator_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Rust hotkey POSTs without a token when the shell has none: on the desktop
    (local-native + local-operator bootstrap) that is the UI's own auth path."""
    from fastapi.testclient import TestClient

    controller = cu.ComputerUseController("takeover")
    cu.install_controller(controller)
    client = TestClient(main_module.app)

    assert client.post("/computer-use/panic").status_code == 401
    assert not controller.panicked

    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    monkeypatch.setenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", "true")
    response = client.post("/computer-use/panic")
    assert response.status_code == 200, response.text
    assert controller.panicked
    status = client.get("/computer-use/status")
    assert status.status_code == 200 and status.json()["panicked"] is True
