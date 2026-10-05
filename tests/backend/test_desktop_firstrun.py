"""Installer Phase 0: first-run provisioning + degrade-when-missing launcher.
Offline (injected provision/model_pull/which)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from locus_tooling import desktop_firstrun as fr  # noqa: E402
from locus_tooling import native_binaries as nb  # noqa: E402
from locus_tooling import native_launcher as nl  # noqa: E402


def _which_factory(available: set[str]):
    def _which(names, bin_dir):
        for name in names:
            if name in available:
                return f"/usr/bin/{name}"
        return None

    return _which


# --- first-run flow ----------------------------------------------------------
def test_ensure_sidecars_provisions_and_pulls_model(tmp_path):
    progress: list[str] = []
    pulled: list[tuple[str, str]] = []

    def _provision(targets, bin_dir):
        rep = nb.ProvisionReport()
        rep.installed = {"neo4j": "x", "postgres": "y"}
        rep.skipped = {"nats-server": "present"}
        return rep

    report = fr.ensure_sidecars(
        tmp_path / "bin",
        targets=["nats-server", "neo4j", "postgres", "ollama"],
        model="gpt-oss:20b",
        progress=progress.append,
        provision=_provision,
        model_pull=lambda ollama, model: pulled.append((ollama, model)) or 0,
        which=_which_factory({"ollama"}),
    )
    assert report.installed == {"neo4j": "x", "postgres": "y"}
    assert pulled == [("/usr/bin/ollama", "gpt-oss:20b")]
    assert any("installed neo4j" in m for m in progress)
    assert any("pulling model" in m for m in progress)


def test_ensure_sidecars_skips_model_when_no_ollama(tmp_path):
    pulled: list[tuple[str, str]] = []
    fr.ensure_sidecars(
        tmp_path / "bin",
        targets=[],
        model="gpt-oss:20b",
        progress=lambda _m: None,
        provision=lambda targets, bin_dir: nb.ProvisionReport(),
        model_pull=lambda o, m: pulled.append((o, m)) or 0,
        which=_which_factory(set()),  # ollama absent
    )
    assert pulled == []


# --- degrade-when-missing launcher plan -------------------------------------
def test_degrade_uses_sqlite_when_postgres_absent(tmp_path):
    # No infra binaries present at all → degrade mode must still build a plan.
    cfg = nl.NativeConfig(app_home=tmp_path, degrade_when_missing=True, enable_world_models=True)
    plan = nl.build_native_plan(cfg, which=_which_factory(set()))
    assert "postgres" not in plan.service_names()
    assert plan.env["LOCUS_SQLITE_STATE_PATH"].endswith("locus-state.db")
    assert "POSTGRES_DSN" not in plan.env
    # world models off (neo4j absent), nats degraded (agents in-proc).
    assert plan.env["LOCUS_MEMORY_GRAPH_PROJECTION_ENABLED"] == "false"
    assert "NATS_URL" not in plan.env
    assert any("postgres not present" in w for w in plan.warnings)
    # Long-term memory stays ON, in the embedded SQLite store (LOCUS-387).
    assert plan.env["LOCUS_MEMORY_ENABLE_LONG_TERM"] == "true"
    assert plan.env["LOCUS_MEMORY_STORE"] == "sqlite"
    assert plan.env["LOCUS_MEMORY_SQLITE_PATH"] == str(
        tmp_path / "data" / "memory" / "locus-memory.db"
    )


def test_ollama_pulls_the_embedding_model_before_the_chat_model(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCUS_MEMORY_EMBEDDING_MODEL", raising=False)
    cfg = nl.NativeConfig(app_home=tmp_path, degrade_when_missing=True)
    plan = nl.build_native_plan(cfg, which=_which_factory({"ollama"}))
    ollama = next(s for s in plan.services if s.name == "ollama")
    pulls = [step.argv[-1] for step in ollama.post_start]
    assert pulls == ["nomic-embed-text", cfg.ollama_model]
    assert plan.env["LOCUS_MEMORY_EMBEDDING_MODEL"] == "nomic-embed-text"


def test_embedding_model_can_be_disabled_or_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCUS_MEMORY_EMBEDDING_MODEL", "")
    plan = nl.build_native_plan(
        nl.NativeConfig(app_home=tmp_path, degrade_when_missing=True),
        which=_which_factory({"ollama"}),
    )
    ollama = next(s for s in plan.services if s.name == "ollama")
    assert [step.argv[-1] for step in ollama.post_start] == ["gpt-oss:20b"]
    assert plan.env["LOCUS_MEMORY_EMBEDDING_MODEL"] == ""

    monkeypatch.setenv("LOCUS_MEMORY_EMBEDDING_MODEL", "--insecure")
    plan = nl.build_native_plan(
        nl.NativeConfig(app_home=tmp_path, degrade_when_missing=True),
        which=_which_factory({"ollama"}),
    )
    ollama = next(s for s in plan.services if s.name == "ollama")
    assert [step.argv[-1] for step in ollama.post_start] == ["gpt-oss:20b"]
    assert any("invalid memory embedding model" in w for w in plan.warnings)


# --- long-term memory first run (LOCUS-387) ----------------------------------
def test_ensure_memory_store_is_idempotent(tmp_path):
    from locus_runtime.memory.bootstrap import default_store_path
    from locus_runtime.memory.sqlite_store import SQLiteLongTermMemoryStore

    progress: list[str] = []
    assert fr.ensure_memory_store(tmp_path, progress=progress.append)
    assert fr.ensure_memory_store(tmp_path, progress=progress.append)
    assert progress == ["memory: created the Personal collection", "memory: ready"]
    db = default_store_path(tmp_path)
    assert db == tmp_path / "data" / "memory" / "locus-memory.db" and db.is_file()
    store = SQLiteLongTermMemoryStore(str(db), load_extension=False)
    try:
        (personal,) = store.list_collections()  # once, however many first runs
        assert personal["id"] == "personal" and personal["name"] == "Personal"
    finally:
        store.close()


def test_desktop_supervisor_turns_long_term_memory_on(tmp_path, monkeypatch):
    """The installed desktop build used to force LOCUS_MEMORY_ENABLE_LONG_TERM=false
    (memory "off"); it now selects the embedded store and bootstraps it first."""
    import os
    import types

    from locus_tooling import desktop

    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    for key in (
        "LOCUS_MEMORY_ENABLE_LONG_TERM",
        "LOCUS_MEMORY_STORE",
        "LOCUS_MEMORY_SQLITE_PATH",
        "LOCUS_SQLITE_STATE_PATH",
        "LOCUS_MEMORY_GRAPH_PROJECTION_ENABLED",
        "PLAYWRIGHT_BROWSERS_PATH",
        "POSTGRES_DSN",
    ):
        monkeypatch.delenv(key, raising=False)

    class _Supervisor:
        def __init__(self, *args, **kwargs):
            pass

        def start_all(self):
            return {}

        def stop_all(self):
            return None

    served: dict[str, str] = {}

    def _serve(*_args, **_kwargs):
        served.update({k: v for k, v in os.environ.items() if k.startswith("LOCUS_MEMORY")})

    monkeypatch.setattr(desktop, "build_native_plan", lambda cfg: nl.NativePlan([], {}, []))
    monkeypatch.setattr(desktop, "NativeSupervisor", _Supervisor)
    monkeypatch.setattr(desktop, "_install_shutdown_hooks", lambda: None)
    monkeypatch.setattr(desktop, "_safe", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "resume_loop_after_start", lambda **k: None)
    monkeypatch.setattr(desktop, "_LIVE_SUPERVISORS", [])
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=_serve))
    monkeypatch.setitem(sys.modules, "app", types.ModuleType("app"))
    monkeypatch.setitem(sys.modules, "app.main", types.SimpleNamespace(app=object()))

    log: list[str] = []
    desktop.run_desktop_supervisor(log=log.append)

    db = tmp_path / "data" / "memory" / "locus-memory.db"
    assert served["LOCUS_MEMORY_ENABLE_LONG_TERM"] == "true"
    assert served["LOCUS_MEMORY_STORE"] == "sqlite"
    assert served["LOCUS_MEMORY_SQLITE_PATH"] == str(db)
    assert db.is_file()
    assert "memory: created the Personal collection" in log


def test_ensure_memory_store_never_raises(tmp_path):
    progress: list[str] = []
    blocker = tmp_path / "data"
    blocker.write_text("not a directory", encoding="utf-8")
    assert fr.ensure_memory_store(tmp_path, progress=progress.append) is False
    assert progress and progress[0].startswith("FAILED memory store")


def test_ensure_memory_store_is_owner_only(tmp_path):
    import os
    import stat

    import pytest

    if os.name == "nt":
        pytest.skip("POSIX permission bits; Windows inherits the per-user app-home ACL")
    assert fr.ensure_memory_store(tmp_path, progress=lambda _m: None)
    db = tmp_path / "data" / "memory" / "locus-memory.db"
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700


def test_strict_mode_still_raises_without_postgres(tmp_path):
    import pytest

    cfg = nl.NativeConfig(app_home=tmp_path, degrade_when_missing=False)
    with pytest.raises(nl.NativeLauncherError):
        nl.build_native_plan(cfg, which=_which_factory(set()))


def test_degrade_full_stack_present_uses_postgres(tmp_path):
    # When everything is present, degrade mode behaves like the strict plan.
    all_bins = {"postgres", "pg_ctl", "initdb", "psql", "neo4j", "nats-server", "ollama"}
    cfg = nl.NativeConfig(app_home=tmp_path, degrade_when_missing=True)
    plan = nl.build_native_plan(cfg, which=_which_factory(all_bins))
    assert "postgres" in plan.service_names()
    assert "POSTGRES_DSN" in plan.env and "LOCUS_SQLITE_STATE_PATH" not in plan.env


# --- Playwright Chromium for the agent browser (LOCUS-346) -------------------
def _fake_driver():
    return (
        "/bundle/playwright/driver/node",
        "/bundle/playwright/driver/package/cli.js",
        {"PATH": "/usr/bin", "PLAYWRIGHT_DOWNLOAD_HOST": "https://evil.example"},
        "1.63.0",
    )


def test_playwright_install_command_uses_bundled_driver_and_app_home(tmp_path):
    argv, env, version = fr.playwright_install_command(tmp_path, driver=_fake_driver)
    assert argv == [
        "/bundle/playwright/driver/node",
        "/bundle/playwright/driver/package/cli.js",
        "install",
        "chromium",
    ]
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "playwright")
    assert "PLAYWRIGHT_DOWNLOAD_HOST" not in env  # no redirected downloads
    assert version == "1.63.0"


def test_real_driver_resolves_to_the_pinned_playwright_package(tmp_path):
    argv, env, version = fr.playwright_install_command(tmp_path)
    assert argv[2:] == ["install", "chromium"]
    assert argv[1].replace("\\", "/").endswith("playwright/driver/package/cli.js")
    assert env["PW_LANG_NAME"] == "python"
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"playwright=={version}"' in pyproject


def test_ensure_playwright_chromium_installs_once_per_version(tmp_path):
    calls: list[tuple[list[str], dict]] = []
    progress: list[str] = []

    def run(argv, env):
        calls.append((argv, env))
        return 0

    assert fr.ensure_playwright_chromium(
        tmp_path, driver=_fake_driver, run=run, progress=progress.append
    )
    assert len(calls) == 1 and calls[0][1]["PLAYWRIGHT_BROWSERS_PATH"] == str(
        tmp_path / "playwright"
    )
    assert (tmp_path / "playwright" / ".locus-chromium-1.63.0").exists()
    assert any("installed playwright chromium" in line for line in progress)

    # Second launch: marker present, nothing runs.
    assert fr.ensure_playwright_chromium(
        tmp_path, driver=_fake_driver, run=run, progress=progress.append
    )
    assert len(calls) == 1


def test_ensure_playwright_chromium_failures_never_raise(tmp_path):
    progress: list[str] = []
    assert not fr.ensure_playwright_chromium(
        tmp_path, driver=_fake_driver, run=lambda argv, env: 1, progress=progress.append
    )
    assert not (tmp_path / "playwright" / ".locus-chromium-1.63.0").exists()

    def boom():
        raise ImportError("no playwright")

    assert not fr.ensure_playwright_chromium(tmp_path, driver=boom, progress=progress.append)
    assert any(line.startswith("FAILED playwright chromium") for line in progress)
