"""Pre-rename (xFrontier) installs and data keep working under Locus names."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from locus_runtime.legacy import (
    alias_legacy_env,
    migrate_env_text,
    normalize_legacy_identifiers,
    prefer_existing_path,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_platform_services():
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend"))
    path = _REPO_ROOT / "apps" / "backend" / "app" / "platform_services.py"
    spec = importlib.util.spec_from_file_location("platform_services_legacy_compat", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_env_vars_alias_to_locus_names_without_overriding_explicit_values():
    env = {
        "FRONTIER_AUTH_MODE": "oidc",
        "FRONTIER_RUNTIME_PROFILE": "local-secure",
        "LOCUS_RUNTIME_PROFILE": "hosted",
        "NEXT_PUBLIC_FRONTIER_ACTOR": "operator",
        "UNRELATED": "x",
    }

    aliased = alias_legacy_env(env)

    assert env["LOCUS_AUTH_MODE"] == "oidc"
    assert env["LOCUS_RUNTIME_PROFILE"] == "hosted"
    assert env["NEXT_PUBLIC_LOCUS_ACTOR"] == "operator"
    assert sorted(aliased) == ["LOCUS_AUTH_MODE", "NEXT_PUBLIC_LOCUS_ACTOR"]


def test_env_file_keys_are_migrated_and_values_untouched():
    text = "# comment about FRONTIER_X\nFRONTIER_AUTH_MODE=oidc\nexport FRONTIER_TOKEN=FRONTIER_value\nOTHER=1\n"

    migrated = migrate_env_text(text)

    assert migrated == (
        "# comment about FRONTIER_X\nLOCUS_AUTH_MODE=oidc\nexport LOCUS_TOKEN=FRONTIER_value\nOTHER=1\n"
    )


def test_stored_node_types_and_graph_schema_are_normalized():
    graph = {
        "schema_version": "frontier-graph/1.0",
        "nodes": [{"type": "frontier/agent", "config": {"prompt": "explore the frontier/edge case"}}],
    }

    normalized = normalize_legacy_identifiers(graph)

    assert normalized["schema_version"] == "locus-graph/1.0"
    assert normalized["nodes"][0]["type"] == "locus/agent"
    # Only values that start with the legacy prefix change; free text is left alone.
    assert normalized["nodes"][0]["config"]["prompt"] == "explore the frontier/edge case"


def test_prefer_existing_path_falls_back_only_when_new_location_is_missing(tmp_path):
    current, legacy = tmp_path / "Locus", tmp_path / "xFrontier"
    assert prefer_existing_path(current, legacy) == current
    legacy.mkdir()
    assert prefer_existing_path(current, legacy) == legacy
    current.mkdir()
    assert prefer_existing_path(current, legacy) == current


def test_sqlite_state_store_adopts_legacy_table_and_normalizes_payload(tmp_path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE frontier_state_store (state_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO frontier_state_store (state_key, payload) VALUES (?, ?)",
            ("section:workflow_definitions", json.dumps({"wf-1": {"nodes": [{"type": "frontier/trigger"}]}})),
        )

    platform_services = _load_platform_services()
    store = platform_services.SQLiteStateStore(str(db_path))
    state = store.load_state()

    assert state == {"workflow_definitions": {"wf-1": {"nodes": [{"type": "locus/trigger"}]}}}
    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "locus_state_store" in tables
    assert "frontier_state_store" not in tables
