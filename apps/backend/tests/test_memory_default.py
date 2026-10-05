"""LOCUS-387: long-term memory is on by default on the desktop / local profile.

The backend composes the embedded SQLite store when no Postgres is configured on
the zero-container profiles, bootstraps the Personal collection idempotently, and
the status endpoints the Settings page reads report memory enabled, the Personal
collection and store ``sqlite`` -- with keyword search working before any
embedding model is available.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import app, store
from locus_runtime.memory.sqlite_store import SQLiteLongTermMemoryStore
from tests.memory_support import ConceptEmbedder

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}

_STORE_ENV = (
    "LOCUS_MEMORY_STORE",
    "LOCUS_MEMORY_SQLITE_PATH",
    "POSTGRES_DSN",
    "LOCUS_SQLITE_STATE_PATH",
    "LOCUS_RUNTIME_PROFILE",
)


@pytest.fixture
def sqlite_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The desktop composition: embedded store, embedding model not pulled yet."""
    memory = SQLiteLongTermMemoryStore(
        str(tmp_path / "data" / "memory" / "locus-memory.db"),
        embedder=ConceptEmbedder(available=False),
    )
    memory.initialize()
    monkeypatch.setattr(main_module, "_POSTGRES_MEMORY", memory)
    monkeypatch.setattr(store, "knowledge_collections", {})
    yield memory
    memory.close()


def test_store_choice_follows_the_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _STORE_ENV:
        monkeypatch.delenv(name, raising=False)
    assert main_module._memory_store_choice() == "postgres"  # hosted default, unchanged
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    assert main_module._memory_store_choice() == "sqlite"  # desktop: on, no setup
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://example/locus")
    assert main_module._memory_store_choice() == "postgres"  # full stack keeps pgvector
    monkeypatch.setenv("LOCUS_MEMORY_STORE", "sqlite")
    assert main_module._memory_store_choice() == "sqlite"  # explicit wins
    monkeypatch.delenv("LOCUS_RUNTIME_PROFILE")
    monkeypatch.delenv("LOCUS_APP_HOME", raising=False)
    monkeypatch.setenv("LOCUS_SQLITE_STATE_PATH", str(tmp_path / "state" / "state.db"))
    # A SQLite-state deployment without an app home keeps memory beside the state.
    assert main_module._memory_sqlite_path() == tmp_path / "state" / "locus-memory.db"
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path / "home"))
    assert main_module._memory_sqlite_path() == (
        tmp_path / "home" / "data" / "memory" / "locus-memory.db"
    )
    monkeypatch.setenv("LOCUS_MEMORY_SQLITE_PATH", str(tmp_path / "m.db"))
    built = main_module._build_long_term_memory_store()
    try:
        assert isinstance(built, SQLiteLongTermMemoryStore)
        assert built.path == str(tmp_path / "m.db")
        assert built.embedder is main_module._MEMORY_EMBEDDER
    finally:
        built.close()


def test_desktop_world_graph_uses_its_separate_postgres_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://state-store/locus")
    monkeypatch.setenv("LOCUS_WORLD_GRAPH_DSN", "postgresql://world-graph/locus")
    monkeypatch.setenv("LOCUS_MEMORY_GRAPH_PROJECTION_ENABLED", "true")

    graph = main_module._build_world_graph()

    assert graph.dsn == "postgresql://world-graph/locus"
    assert graph.enabled is True


def test_short_term_memory_uses_the_builtin_cache_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "_REDIS_MEMORY",
        SimpleNamespace(enabled=False, healthcheck=lambda: False),
    )

    layers = {layer["id"]: layer for layer in main_module._knowledge_memory_layers()}

    assert layers["short_term"]["enabled"] is True
    assert layers["short_term"]["healthy"] is True
    assert layers["short_term"]["backend"] == "Process-local session cache"
    assert layers["world_graph"]["backend"] == "PostgreSQL"


def test_personal_collection_is_bootstrapped_once(sqlite_memory) -> None:
    assert main_module._ensure_memory_collections() is True
    assert main_module._ensure_memory_collections() is False
    listing = client.get("/knowledge/collections", headers=ADMIN_HEADERS)
    assert listing.status_code == 200
    (personal,) = listing.json()
    assert personal["id"] == "personal" and personal["name"] == "Personal"
    assert personal["vector_store_id"] == "platform"
    assert [c["id"] for c in sqlite_memory.list_collections()] == ["personal"]


def test_status_endpoints_report_memory_on_with_sqlite(sqlite_memory) -> None:
    main_module._ensure_memory_collections()
    layers = client.get("/knowledge/memory-layers", headers=ADMIN_HEADERS).json()["layers"]
    long_term = next(layer for layer in layers if layer["id"] == "long_term")
    assert long_term["enabled"] is True and long_term["healthy"] is True
    assert "SQLite" in long_term["backend"]
    assert long_term["stats"]["store"] == "sqlite"
    assert long_term["stats"]["semantic_search"] == "pending_embedding_model"
    assert long_term["stats"]["vector_search"] is False
    knowledge = next(layer for layer in layers if layer["id"] == "knowledge")
    assert knowledge["enabled"] is True and knowledge["healthy"] is True
    assert knowledge["stats"]["collections"] == 1 and knowledge["stats"]["store"] == "sqlite"

    stores = client.get("/knowledge/vector-stores", headers=ADMIN_HEADERS).json()
    platform = next(s for s in stores["vector_stores"] if s["id"] == "platform")
    assert platform["ready"] is True and platform["store"] == "sqlite"
    assert "semantic search is pending" in platform["note"]

    health = client.get("/healthz/details", headers=ADMIN_HEADERS)
    assert health.status_code == 200
    assert health.json()["long_term_memory"] == "connected"
    assert health.json()["long_term_memory_store"] == "sqlite"


def test_knowledge_works_by_keyword_before_any_embedding_model(sqlite_memory) -> None:
    main_module._ensure_memory_collections()
    added = client.post(
        "/knowledge/collections/personal/documents",
        json={"name": "notes", "text": "The boiler service is due in March.\n\nWater the tomato."},
        headers=ADMIN_HEADERS,
    )
    assert added.status_code == 200 and added.json()["chunks_indexed"] >= 1
    found = client.post(
        "/knowledge/collections/personal/search",
        json={"query": "boiler service"},
        headers=ADMIN_HEADERS,
    )
    assert found.status_code == 200
    results = found.json()["results"]
    assert results and "boiler" in results[0]["content"]
    assert results[0]["document_name"] == "notes"


def test_deleting_a_collection_forgets_its_chunks(sqlite_memory) -> None:
    created = client.post("/knowledge/collections", json={"name": "Scratch"}, headers=ADMIN_HEADERS)
    collection_id = created.json()["id"]
    assert collection_id in {c["id"] for c in sqlite_memory.list_collections()}
    client.post(
        f"/knowledge/collections/{collection_id}/documents",
        json={"name": "d", "text": "ephemeral scratch content"},
        headers=ADMIN_HEADERS,
    )
    bucket = f"knowledge:{collection_id}"
    assert sqlite_memory.get_entries(bucket_id=bucket)
    deleted = client.delete(f"/knowledge/collections/{collection_id}", headers=ADMIN_HEADERS)
    assert deleted.status_code == 200
    assert sqlite_memory.get_entries(bucket_id=bucket) == []
    assert collection_id not in {c["id"] for c in sqlite_memory.list_collections()}


def test_memory_writes_land_in_the_embedded_store(sqlite_memory) -> None:
    main_module._memory_append_entry(
        "agent:memory-default-test",
        {"content": "prefers concise answers"},
        memory_scope="agent",
        source="test",
    )
    entries = main_module._memory_load_long_term_entries(
        "agent:memory-default-test", memory_scope="agent", query_text="concise"
    )
    assert [entry["content"] for entry in entries] == ["prefers concise answers"]
