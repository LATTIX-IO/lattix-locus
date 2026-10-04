"""LOCUS-353: the loop controls behind Settings → Loop & Linear.

Turning the loop on and autostarting it widen what runs unattended, so on the
desktop profile they need the shell's request-bound proof; turning either off
only narrows. The Linear key is reported as present or absent, never returned.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
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

import app.main as main_module
from app.main import app, store
from app.request_security import CapabilityEffect, classify_route_access, classify_shell_proof

from locus_runtime.loop_runner import linear as loop_linear
from locus_runtime.loop_runner.state import KILL_FILE
from locus_tooling import shell_confirmation as sc

client = TestClient(app)
SHELL_SECRET = bytes(range(80, 112))


@pytest.fixture()
def loop_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loop"
    home.mkdir()
    monkeypatch.setenv("LOCUS_LOOP_HOME", str(home))
    monkeypatch.delenv("LOCUS_LOOP_DISABLED", raising=False)
    monkeypatch.setattr(loop_linear, "resolve_linear_key", lambda: "lin_api_secret_value")
    return home


@pytest.fixture()
def desktop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    monkeypatch.setenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", "true")
    sc.install_secret(SHELL_SECRET)
    try:
        yield
    finally:
        sc.install_secret(None)


def _encode(body: dict[str, Any] | None) -> bytes:
    return b"" if body is None else sc.canonical_body(json.dumps(body)).encode("utf-8")


def _proof(method: str, path: str, body: dict[str, Any] | None) -> str:
    rule = classify_shell_proof(method, path)
    assert rule is not None and rule.may_need_proof
    digest = sc.request_digest(method, path, _encode(body))
    return sc.proof_header(
        SHELL_SECRET,
        lambda nonce, stamp: sc.action_message(
            action=rule.action, digest=digest, nonce=nonce, timestamp=stamp
        ),
        nonce=secrets.token_hex(16),
        timestamp=int(time.time()),
    )


def _call(
    method: str, path: str, body: dict[str, Any] | None = None, proof: str | None = None
) -> Any:
    headers = {"Content-Type": "application/json"}
    if proof is not None:
        headers[sc.PROOF_HEADER] = proof
    return client.request(method, path, content=_encode(body), headers=headers)


def _last_audit(action: str) -> Any:
    return next(event for event in store.audit_events if event.action == action)


def test_routes_are_classified() -> None:
    assert classify_shell_proof("POST", "/loop/enable").effect == CapabilityEffect.WIDENING
    assert classify_shell_proof("POST", "/loop/autostart").effect == CapabilityEffect.WIDENING
    assert classify_shell_proof("POST", "/loop/disable").effect == CapabilityEffect.NARROWING
    assert classify_shell_proof("DELETE", "/loop/autostart").effect == CapabilityEffect.NARROWING
    assert classify_route_access("GET", "/loop/status") is not None


def test_status_reports_the_linear_key_without_its_value(desktop: None, loop_home: Path) -> None:
    response = _call("GET", "/loop/status")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["enabled"] is True
    assert data["linear"] == {"api_key_configured": True}
    assert data["autostart"] == {"enabled": False, "repo_path": ""}
    assert "home" not in data
    assert "lin_api_secret_value" not in response.text


def test_status_says_when_the_linear_key_is_missing(
    desktop: None, loop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing() -> str:
        raise loop_linear.LinearNotConfigured("LINEAR_API_KEY is not configured")

    monkeypatch.setattr(loop_linear, "resolve_linear_key", missing)
    assert _call("GET", "/loop/status").json()["linear"] == {"api_key_configured": False}


def test_disable_needs_no_proof_and_enable_needs_one(desktop: None, loop_home: Path) -> None:
    disabled = _call("POST", "/loop/disable", {})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert (loop_home / KILL_FILE).exists()

    refused = _call("POST", "/loop/enable", {})
    assert refused.status_code == 403 and "missing_proof" in refused.text
    assert (loop_home / KILL_FILE).exists()
    assert _last_audit("loop.enable").outcome == "blocked"

    enabled = _call("POST", "/loop/enable", {}, _proof("POST", "/loop/enable", {}))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert not (loop_home / KILL_FILE).exists()


def test_the_environment_switch_still_wins(
    desktop: None, loop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_LOOP_DISABLED", "1")
    data = _call("POST", "/loop/enable", {}, _proof("POST", "/loop/enable", {})).json()
    assert data["enabled"] is False
    assert data["disabled_by_environment"] is True


def test_autostart_needs_a_proof_and_a_workflow_checkout(
    desktop: None, loop_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "projects"
    repo = projects / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(main_module, "_PROJECTS_ROOT", str(projects))
    body = {"repo_path": str(repo)}

    assert _call("POST", "/loop/autostart", body).status_code == 403
    no_workflow = _call("POST", "/loop/autostart", body, _proof("POST", "/loop/autostart", body))
    assert no_workflow.status_code == 422

    (repo / "WORKFLOW.md").write_text("---\n---\n", encoding="utf-8")
    on = _call("POST", "/loop/autostart", body, _proof("POST", "/loop/autostart", body))
    assert on.status_code == 200, on.text
    assert on.json()["autostart"] == {"enabled": True, "repo_path": str(repo.resolve())}

    # Confined to the projects root: a checkout outside it, a traversal, and a
    # folder that isn't a git checkout are all refused.
    outside = tmp_path / "elsewhere"
    (outside / ".git").mkdir(parents=True)
    (outside / "WORKFLOW.md").write_text("---\n---\n", encoding="utf-8")
    plain = projects / "plain"
    plain.mkdir()
    (plain / "WORKFLOW.md").write_text("---\n---\n", encoding="utf-8")
    for bad in (str(outside), str(projects / "repo" / ".." / ".." / "elsewhere"), str(plain)):
        refused_body = {"repo_path": bad}
        refused = _call(
            "POST",
            "/loop/autostart",
            refused_body,
            _proof("POST", "/loop/autostart", refused_body),
        )
        assert refused.status_code == 422, bad

    off = _call("DELETE", "/loop/autostart")
    assert off.status_code == 200, off.text
    assert off.json()["autostart"]["enabled"] is False
