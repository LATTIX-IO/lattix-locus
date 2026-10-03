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
