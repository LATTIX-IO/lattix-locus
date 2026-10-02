"""Track B4: desktop (Tauri) packaging integration — frozen-mode resolution,
desktop config, supervisor serve() lifecycle, and Tauri config validity.
Pure/unit; no Rust/PyInstaller build required."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from frontier_tooling import desktop as dt  # noqa: E402
from frontier_tooling import native_launcher as nl  # noqa: E402

_TAURI_DIR = _REPO_ROOT / "apps" / "desktop-tauri" / "src-tauri"


# --- frozen-mode resolution --------------------------------------------------
def test_is_frozen_default_false():
    assert dt.is_frozen() is False


def test_bundled_root_frozen_is_exe_dir(monkeypatch):
    # Tauri resources/bin sit next to the installed exe (not in _MEIPASS).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "C:/some/meipass/temp", raising=False)
    exe_parent = Path(sys.executable).resolve().parent
    assert dt.bundled_root() == exe_parent
    assert dt.bundled_bin_dir() == exe_parent / "bin"


def test_bundled_root_from_checkout_is_repo_root(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    # source_repo_root() is the package parent (repo root).
    assert (dt.bundled_root() / "frontier_tooling").exists()


def test_desktop_app_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONTIER_APP_HOME", str(tmp_path))
    assert dt.desktop_app_home() == tmp_path


# --- desktop NativeConfig ----------------------------------------------------
def test_desktop_config_uses_writable_bin_and_degrades(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONTIER_APP_HOME", str(tmp_path))
    cfg = dt.desktop_config()
    assert cfg.app_home == tmp_path
    # First-run fetch lands in the writable app-home bin (not the read-only bundle).
    assert cfg.bin_dir == tmp_path / "bin"
    # Desktop degrades (boots before sidecars are fetched) rather than raising.
    assert cfg.degrade_when_missing is True


def test_desktop_config_overrides_pass_through(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONTIER_APP_HOME", str(tmp_path))
    cfg = dt.desktop_config(enable_world_models=False)
    assert cfg.enable_world_models is False


def test_desktop_config_serves_backend_in_process(monkeypatch, tmp_path):
    # The frozen exe IS the backend (in-proc uvicorn) and runs agents in-proc,
    # so the supervisor must not spawn `python -m uvicorn` subprocesses.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("FRONTIER_APP_HOME", str(tmp_path))
    cfg = dt.desktop_config()
    assert cfg.manage_backend is False
    assert cfg.enable_agents is False


def test_desktop_plan_excludes_backend_and_agent_services(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("FRONTIER_APP_HOME", str(tmp_path))
    plan = dt.build_desktop_plan()  # degrade mode → no raise even with no sidecars present
    assert "backend" not in plan.service_names()
    assert not any(n.startswith("agent-") for n in plan.service_names())


# --- supervisor serve() lifecycle -------------------------------------------
def test_serve_starts_then_stops_on_interrupt():
    terminated: list[str] = []

    class _Proc:
        def __init__(self, name):
            self.name = name

        def poll(self):
            return None  # stays running

        def terminate(self):
            terminated.append(self.name)

    plan = nl.NativePlan(
        services=[
            nl.ServiceSpec(name="svc", argv=["svc"], health=nl.HealthCheck("none"), required=True)
        ],
        env={},
        warnings=[],
    )

    def _sleep(_s):
        raise KeyboardInterrupt  # first poll tick → simulate shell shutdown

    sup = nl.NativeSupervisor(
        plan,
        spawn=lambda argv, *, env, cwd: _Proc(argv[0]),
        run=lambda argv, *, env: 0,
        probe=lambda check: True,
        sleep=_sleep,
    )
    sup.serve()  # should not raise; start_all then stop_all
    assert terminated == ["svc"]


def test_serve_stops_when_required_service_dies():
    terminated: list[str] = []

    class _DeadProc:
        def poll(self):
            return 1  # already exited

        def terminate(self):
            terminated.append("svc")

    plan = nl.NativePlan(
        services=[
            nl.ServiceSpec(name="svc", argv=["svc"], health=nl.HealthCheck("none"), required=True)
        ],
        env={},
        warnings=[],
    )
    sup = nl.NativeSupervisor(
        plan,
        spawn=lambda argv, *, env, cwd: _DeadProc(),
        run=lambda argv, *, env: 0,
        probe=lambda check: True,
        sleep=lambda s: None,  # poll loop detects the dead required service
    )
    sup.serve()
    assert terminated == ["svc"]


# --- Tauri config validity ---------------------------------------------------
def test_tauri_conf_is_valid_and_complete():
    conf = json.loads((_TAURI_DIR / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["identifier"] == "com.lattix.xfrontier"
    assert conf["bundle"]["externalBin"] == ["bin/frontier-backend"]
    # The one-click updater pulls signed release manifests from GitHub releases.
    updater = conf.get("plugins", {}).get("updater", {})
    assert updater.get("pubkey")
    assert all(url.startswith("https://github.com/") for url in updater.get("endpoints", []))
    # macOS hardened runtime + Windows timestamp server are configured for signing.
    assert conf["bundle"]["macOS"]["hardenedRuntime"] is True
    assert conf["bundle"]["windows"]["timestampUrl"]


def test_tauri_capabilities_allow_sidecar_spawn():
    cap = json.loads((_TAURI_DIR / "capabilities" / "default.json").read_text(encoding="utf-8"))
    spawn_perms = [
        p
        for p in cap["permissions"]
        if isinstance(p, dict) and p.get("identifier") == "shell:allow-spawn"
    ]
    assert spawn_perms, "sidecar spawn permission must be granted"
    assert spawn_perms[0]["allow"][0]["sidecar"] is True


def test_pyinstaller_spec_targets_desktop_main():
    spec = (_REPO_ROOT / "packaging" / "frontier-backend.spec").read_text(encoding="utf-8")
    assert "desktop_main.py" in spec
    assert "frontier-backend" in spec
