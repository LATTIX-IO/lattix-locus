"""Focused tests for the knowledge module (reference-plan Phase C)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.knowledge import chunk_text, collection_bucket
from app.main import app, store

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-frontier-actor": "frontier-admin"}


def test_chunk_text_packs_paragraphs() -> None:
    text = "\n\n".join(f"Paragraph {i} content." for i in range(5))
    chunks = chunk_text(text, chunk_chars=200, overlap=20)
    assert len(chunks) >= 1
    assert all(len(c) <= 220 for c in chunks)
    assert "Paragraph 0" in chunks[0]


def test_chunk_text_splits_oversize_paragraph() -> None:
    big = "x" * 3000
    chunks = chunk_text(big, chunk_chars=1000, overlap=100)
    assert len(chunks) >= 3
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_text_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_collection_bucket_namespacing() -> None:
    assert collection_bucket("abc-123") == "knowledge:abc-123"


def test_collection_crud_lifecycle() -> None:
    created = client.post(
        "/knowledge/collections",
        json={"name": "Runbooks", "description": "Ops docs"},
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 200
    body = created.json()
    collection_id = body["id"]
    try:
        assert body["name"] == "Runbooks"
        assert body["document_count"] == 0
        assert body["chunk_count"] == 0

        listing = client.get("/knowledge/collections", headers=ADMIN_HEADERS)
        assert any(item["id"] == collection_id for item in listing.json())

        deleted = client.delete(f"/knowledge/collections/{collection_id}", headers=ADMIN_HEADERS)
        assert deleted.status_code == 200
        collection_id = None
    finally:
        if collection_id:
            store.knowledge_collections.pop(collection_id, None)


def test_create_requires_name() -> None:
    response = client.post("/knowledge/collections", json={}, headers=ADMIN_HEADERS)
    assert response.status_code == 400


def test_memory_layers_reports_all_tiers() -> None:
    response = client.get("/knowledge/memory-layers", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    layers = response.json()["layers"]
    ids = {layer["id"] for layer in layers}
    assert {"short_term", "long_term", "world_graph", "knowledge"} <= ids
    for layer in layers:
        assert {"name", "backend", "scope", "enabled", "healthy", "stats"} <= set(layer)


def test_vector_stores_list_includes_builtin_platform() -> None:
    response = client.get("/knowledge/vector-stores", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    stores = response.json()["vector_stores"]
    platform = next((s for s in stores if s["id"] == "platform"), None)
    assert platform is not None
    assert platform["kind"] == "builtin"


def test_create_rejects_unknown_vector_store() -> None:
    response = client.post(
        "/knowledge/collections",
        json={"name": "KB", "vector_store_id": "does-not-exist"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 400


def test_create_defaults_to_platform_vector_store() -> None:
    created = client.post(
        "/knowledge/collections", json={"name": "Default store KB"}, headers=ADMIN_HEADERS
    )
    assert created.status_code == 200
    collection_id = created.json()["id"]
    try:
        assert created.json()["vector_store_id"] == "platform"
    finally:
        store.knowledge_collections.pop(collection_id, None)


def test_document_add_degrades_without_memory_store(monkeypatch) -> None:
    created = client.post("/knowledge/collections", json={"name": "KB"}, headers=ADMIN_HEADERS)
    collection_id = created.json()["id"]
    try:
        monkeypatch.setattr(main_module._POSTGRES_MEMORY, "enabled", False, raising=False)
        response = client.post(
            f"/knowledge/collections/{collection_id}/documents",
            json={"name": "doc", "text": "some content"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 503
    finally:
        store.knowledge_collections.pop(collection_id, None)


def test_document_add_chunks_and_indexes_with_fake_store(monkeypatch) -> None:
    created = client.post(
        "/knowledge/collections", json={"name": "Indexed KB"}, headers=ADMIN_HEADERS
    )
    collection_id = created.json()["id"]
    appended: list[dict] = []

    class _FakeMemory:
        enabled = True

        def healthcheck(self):
            return True

        def append_entry(self, **kwargs):
            appended.append(kwargs)

        def search_entries(self, query, **kwargs):
            return [
                {
                    "content": "chunk text",
                    "document_name": "doc",
                    "chunk_index": 0,
                    "score": 0.91,
                }
            ]

    monkeypatch.setattr(main_module, "_POSTGRES_MEMORY", _FakeMemory())
    try:
        added = client.post(
            f"/knowledge/collections/{collection_id}/documents",
            json={"name": "doc", "text": "para one.\n\npara two.\n\npara three."},
            headers=ADMIN_HEADERS,
        )
        assert added.status_code == 200
        body = added.json()
        assert body["chunks_indexed"] >= 1
        assert len(appended) == body["chunks_indexed"]
        assert appended[0]["memory_scope"] == "knowledge"
        assert appended[0]["bucket_id"] == f"knowledge:{collection_id}"
        assert body["collection"]["document_count"] == 1

        searched = client.post(
            f"/knowledge/collections/{collection_id}/search",
            json={"query": "para", "top_k": 3},
            headers=ADMIN_HEADERS,
        )
        assert searched.status_code == 200
        results = searched.json()["results"]
        assert results and results[0]["score"] == 0.91
    finally:
        store.knowledge_collections.pop(collection_id, None)
