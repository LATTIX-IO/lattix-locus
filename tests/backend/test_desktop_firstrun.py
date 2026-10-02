"""Installer Phase 0: first-run provisioning + degrade-when-missing launcher.
Offline (injected provision/model_pull/which)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from frontier_tooling import desktop_firstrun as fr  # noqa: E402
from frontier_tooling import native_binaries as nb  # noqa: E402
from frontier_tooling import native_launcher as nl  # noqa: E402


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
    assert plan.env["FRONTIER_SQLITE_STATE_PATH"].endswith("frontier-state.db")
    assert "POSTGRES_DSN" not in plan.env
    # world models off (neo4j absent), nats degraded (agents in-proc).
    assert plan.env["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] == "false"
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
    assert "POSTGRES_DSN" in plan.env and "FRONTIER_SQLITE_STATE_PATH" not in plan.env
