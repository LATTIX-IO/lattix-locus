"""LOCUS-349 (D-26): desktop update readiness / loop hold / version endpoints."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

from app import main as main_module
from app.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}


@pytest.fixture(autouse=True)
def loop_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loop"
    monkeypatch.setenv("LOCUS_LOOP_HOME", str(home))
    monkeypatch.delenv("LOCUS_LOOP_DISABLED", raising=False)
    monkeypatch.setenv("LOCUS_APP_VERSION", "0.1.0-dev.9")
    monkeypatch.setattr(main_module, "_active_agent_run_count", lambda: 0)
    return home


@pytest.fixture()
def desktop_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module, "_local_authenticated_operator_bootstrap_enabled", lambda: True
    )


def test_status_requires_authentication_on_secure_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    assert client.get("/system/update/status").status_code == 401
    assert client.post("/system/update/prepare").status_code == 401


def test_status_reports_readiness_and_handshake(loop_home: Path) -> None:
    response = client.get("/system/update/status", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["port_version"] == "1.0"
    assert body["ready"] is False  # read-only: the loop is not held
    assert body["active_runs"] == 0
    assert body["loop"]["enabled"] is True
    assert body["build_version"] == ""  # a source checkout carries no stamp
    assert body["handshake"]["result"] == "unstamped"
    assert body["handshake"]["app_version"] == "0.1.0-dev.9"
    assert not (loop_home / "loop.lock").exists()


def test_prepare_and_cancel_are_desktop_only() -> None:
    assert client.post("/system/update/prepare", headers=HEADERS).status_code == 404
    assert client.post("/system/update/cancel", headers=HEADERS).status_code == 404


def test_prepare_holds_the_loop_and_cancel_releases_it(
    loop_home: Path, desktop_profile: None
) -> None:
    response = client.post("/system/update/prepare", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True and body["loop"]["paused_for_update"] is True
    owner = json.loads((loop_home / "loop.lock").read_text(encoding="utf-8"))["owner"]
    assert owner.startswith("desktop-update")

    released = client.post("/system/update/cancel", headers=HEADERS)
    assert released.status_code == 200 and released.json() == {"released": True}
    assert not (loop_home / "loop.lock").exists()


def test_prepare_waits_for_agent_runs_and_a_running_loop(
    loop_home: Path, desktop_profile: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "_active_agent_run_count", lambda: 2)
    body = client.post("/system/update/prepare", headers=HEADERS).json()
    assert body["ready"] is False and body["active_runs"] == 2
    client.post("/system/update/cancel", headers=HEADERS)

    import time

    loop_home.mkdir(parents=True, exist_ok=True)
    (loop_home / "loop.lock").write_text(
        json.dumps({"owner": "tick-1a2b", "pid": 1, "acquired_at": time.time()}), encoding="utf-8"
    )
    monkeypatch.setattr(main_module, "_active_agent_run_count", lambda: 0)
    body = client.post("/system/update/prepare", headers=HEADERS).json()
    assert body["ready"] is False and body["loop"]["lock_owner"] == "tick-1a2b"
    # A running loop run is never interrupted: its lock is untouched.
    assert json.loads((loop_home / "loop.lock").read_text(encoding="utf-8"))["owner"] == "tick-1a2b"
    assert client.post("/system/update/cancel", headers=HEADERS).json() == {"released": False}


def test_platform_version_reports_the_full_dev_version(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shell's LOCUS_APP_VERSION wins; the UI shows it, never the base 0.1.0.
    monkeypatch.setattr(main_module, "_fetch_remote_release_manifest", lambda: None)
    body = client.get("/platform/version").json()
    assert body["current_version"] == "0.1.0-dev.9"


def test_platform_version_falls_back_to_the_build_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stamped sidecar reports its stamp, not the package metadata (0.1.0).
    monkeypatch.delenv("LOCUS_APP_VERSION", raising=False)
    monkeypatch.setattr(main_module, "_fetch_remote_release_manifest", lambda: None)
    monkeypatch.setattr(main_module, "_stamped_build_version", lambda: "0.1.0-dev.16")
    monkeypatch.setattr(main_module.importlib_metadata, "version", lambda _name: "0.1.0")
    assert client.get("/platform/version").json()["current_version"] == "0.1.0-dev.16"


def test_newer_dev_build_is_an_update_for_semver(monkeypatch: pytest.MonkeyPatch) -> None:
    assert main_module._version_is_newer("0.1.0-dev.17", "0.1.0-dev.16")
    assert main_module._version_is_newer("0.1.0-dev.10", "0.1.0-dev.9")
    assert main_module._version_is_newer("0.1.0", "0.1.0-dev.16")
    assert not main_module._version_is_newer("0.1.0-dev.16", "0.1.0-dev.16")
