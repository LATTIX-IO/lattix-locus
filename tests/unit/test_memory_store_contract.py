"""Contract suite for the long-term memory port (D-28; LOCUS-387).

Every adapter of :class:`locus_runtime.memory.contract.LongTermMemoryStore` must pass
these tests -- they are its admission test. The SQLite adapter runs everywhere (with
and without the sqlite-vec extension); the Postgres adapter runs when
``LOCUS_TEST_POSTGRES_DSN`` points at a database with pgvector.

Behaviour that must hold with **no embedding model** is tested with an unavailable
embedder: memory stays on, writes are kept, keyword search answers.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from locus_runtime.memory.contract import MAX_CONTENT_CHARS, LongTermMemoryStore
from locus_runtime.memory.sqlite_store import SQLiteLongTermMemoryStore
from tests.memory_support import ConceptEmbedder

_ADAPTERS = ["sqlite-vec", "sqlite-python-vectors", "postgres"]


def _postgres_store(embedder: ConceptEmbedder) -> Any:
    dsn = str(os.getenv("LOCUS_TEST_POSTGRES_DSN") or "").strip()
    if not dsn:
        pytest.skip("LOCUS_TEST_POSTGRES_DSN not set (Postgres adapter contract run)")
    from app.platform_services import PostgresLongTermMemoryStore

    store = PostgresLongTermMemoryStore(dsn, embedder=embedder)
    if not store.enabled or not store.healthcheck():
        pytest.skip("Postgres adapter not reachable")
    return store


@pytest.fixture(params=_ADAPTERS)
def make_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    created: list[Any] = []

    def factory(*, embeddings: bool = True) -> Any:
        embedder = ConceptEmbedder(available=embeddings)
        if request.param == "postgres":
            store = _postgres_store(embedder)
        else:
            store = SQLiteLongTermMemoryStore(
                str(tmp_path / f"memory-{len(created)}.db"),
                embedder=embedder,
                load_extension=request.param == "sqlite-vec",
            )
        store.initialize()
        created.append(store)
        return store

    yield factory
    for store in created:
        close = getattr(store, "close", None)
        if callable(close):
            close()


def _bucket() -> str:
    # Unique per test so a shared Postgres database never mixes runs.
    return f"contract:{uuid4()}"


def _append(store: Any, bucket: str, content: str, *, scope: str = "agent", **extra: Any) -> None:
    store.append_entry(
        bucket_id=bucket,
        session_id=bucket,
        memory_scope=scope,
        entry={"content": content, **extra},
        source="contract-test",
    )


def test_adapter_satisfies_the_port(make_store: Any) -> None:
    store = make_store()
    assert isinstance(store, LongTermMemoryStore)
    assert store.enabled and store.healthcheck()
    assert store.store_kind in {"sqlite", "postgres"}


def test_append_then_get_round_trips_oldest_first(make_store: Any) -> None:
    store = make_store()
    bucket = _bucket()
    _append(store, bucket, "first note", id=f"{bucket}:1", kind="note")
    _append(store, bucket, "second note", id=f"{bucket}:2")
    entries = store.get_entries(bucket_id=bucket, memory_scope="agent")
    assert [entry["content"] for entry in entries] == ["first note", "second note"]
    first = entries[0]
    assert first["id"] == f"{bucket}:1"
    assert first["bucket_id"] == bucket and first["memory_scope"] == "agent"
    assert first["source"] == "contract-test" and first["tier"] == "long-term"
    assert first["kind"] == "note"  # entry fields come back with the entry
    assert first["at"]


def test_append_with_same_id_upserts(make_store: Any) -> None:
    store = make_store()
    bucket = _bucket()
    _append(store, bucket, "draft", id=f"{bucket}:x")
    _append(store, bucket, "final", id=f"{bucket}:x")
    entries = store.get_entries(bucket_id=bucket)
    assert [entry["content"] for entry in entries] == ["final"]


def test_filters_and_limit(make_store: Any) -> None:
    store = make_store()
    bucket, other = _bucket(), _bucket()
    for index in range(5):
        _append(store, bucket, f"entry {index}")
    _append(store, other, "elsewhere")
    assert len(store.get_entries(bucket_id=bucket, limit=3)) == 3
    assert [e["content"] for e in store.get_entries(bucket_id=bucket, limit=2)] == [
        "entry 3",
        "entry 4",
    ]
    assert [e["content"] for e in store.get_entries(bucket_id=other)] == ["elsewhere"]
    assert store.get_entries(bucket_id=bucket, memory_scope="session") == []


def test_content_is_bounded(make_store: Any) -> None:
    store = make_store()
    bucket = _bucket()
    _append(store, bucket, "x" * (MAX_CONTENT_CHARS + 500))
    (entry,) = store.get_entries(bucket_id=bucket)
    assert len(entry["content"]) == MAX_CONTENT_CHARS


def test_keyword_search_works_without_an_embedding_model(make_store: Any) -> None:
    store = make_store(embeddings=False)
    bucket = _bucket()
    _append(store, bucket, "The quarterly invoice was paid late.")
    _append(store, bucket, "Tomato seedlings go in the garden in May.")
    results = store.search_entries("invoice paid", bucket_id=bucket, limit=5)
    assert results and results[0]["content"].startswith("The quarterly invoice")
    assert all("garden" not in result["content"] for result in results)


def test_empty_query_lists_recent_entries(make_store: Any) -> None:
    store = make_store()
    bucket = _bucket()
    _append(store, bucket, "alpha")
    _append(store, bucket, "beta")
    assert [e["content"] for e in store.search_entries("", bucket_id=bucket)] == [
        "alpha",
        "beta",
    ]


def test_clear_entries_is_scoped_and_never_unfiltered(make_store: Any) -> None:
    store = make_store()
    bucket, keep = _bucket(), _bucket()
    _append(store, bucket, "to forget")
    _append(store, keep, "to keep")
    store.clear_entries()  # no filter: a no-op, never a full wipe
    assert store.get_entries(bucket_id=bucket)
    store.clear_entries(bucket_id=bucket)
    assert store.get_entries(bucket_id=bucket) == []
    assert store.search_entries("forget", bucket_id=bucket) == []
    assert [e["content"] for e in store.get_entries(bucket_id=keep)] == ["to keep"]


def test_consolidation_queue_lifecycle(make_store: Any) -> None:
    store = make_store()
    bucket = _bucket()
    before = store.count_consolidation_candidates(status="pending")
    store.enqueue_consolidation_candidate(
        bucket_id=bucket,
        session_id=bucket,
        memory_scope="agent",
        entry={"id": f"{bucket}:e1", "content": "promote me"},
        source="contract-test",
    )
    (candidate,) = store.list_consolidation_candidates(bucket_id=bucket)
    assert candidate["entry_id"] == f"{bucket}:e1" and candidate["status"] == "pending"
    assert candidate["content"] == "promote me"
    assert store.count_consolidation_candidates(status="pending") == before + 1
    store.mark_consolidation_candidate(
        candidate["id"], status="applied", extra_metadata={"reviewed_by": "contract"}
    )
    assert store.list_consolidation_candidates(bucket_id=bucket) == []
    (applied,) = store.list_consolidation_candidates(bucket_id=bucket, status="applied")
    assert applied["metadata"]["reviewed_by"] == "contract"
    assert applied["metadata"]["queued_for_consolidation"] is True
    # Re-enqueueing the same entry resets it to pending instead of duplicating it.
    store.enqueue_consolidation_candidate(
        bucket_id=bucket,
        session_id=bucket,
        memory_scope="agent",
        entry={"id": f"{bucket}:e1", "content": "promote me again"},
        source="contract-test",
    )
    (again,) = store.list_consolidation_candidates(bucket_id=bucket, status=None)
    assert again["status"] == "pending" and again["content"] == "promote me again"


def test_find_similar_entries_without_embeddings_is_empty(make_store: Any) -> None:
    store = make_store(embeddings=False)
    bucket = _bucket()
    _append(store, bucket, "kitten on the sofa")
    assert store.find_similar_entries("feline", bucket_id=bucket, threshold=0.5) == []
