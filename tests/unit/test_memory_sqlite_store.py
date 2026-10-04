"""Embedded SQLite memory store (LOCUS-387): hybrid search, no-model degradation,
backfill, schema idempotency, validation and owner-only file permissions."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from locus_runtime.memory import bootstrap
from locus_runtime.memory.backfill import EmbeddingBackfillWorker
from locus_runtime.memory.contract import MAX_IDENTIFIER_CHARS
from locus_runtime.memory.sqlite_store import (
    SCHEMA_VERSION,
    SQLiteLongTermMemoryStore,
    prepare_private_file,
)
from tests.memory_support import ConceptEmbedder

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


def _store(
    tmp_path: Path, *, available: bool = False, vec: bool = True
) -> tuple[SQLiteLongTermMemoryStore, ConceptEmbedder]:
    embedder = ConceptEmbedder(available=available)
    store = SQLiteLongTermMemoryStore(
        str(tmp_path / "memory" / "locus-memory.db"), embedder=embedder, load_extension=vec
    )
    store.initialize()
    return store, embedder


def _add(store: SQLiteLongTermMemoryStore, content: str, bucket: str = "agent:a") -> None:
    store.append_entry(
        bucket_id=bucket,
        session_id=bucket,
        memory_scope="agent",
        entry={"content": content},
        source="test",
    )


@pytest.mark.parametrize("vec", [True, False], ids=["sqlite-vec", "python-vectors"])
def test_memory_works_without_a_model_then_backfills_and_searches_semantically(
    tmp_path: Path, vec: bool
) -> None:
    store, embedder = _store(tmp_path, available=False, vec=vec)
    if vec:
        assert store.vector_extension_version.startswith("v")
    _add(store, "The kitten sleeps on the sofa.")
    _add(store, "Renew the automobile insurance.")

    # No embedding model: memory is on, writes are kept, keyword search answers.
    assert store.healthcheck()
    assert store.status() == ("connected", "")
    assert store.vector_enabled is False
    facts = store.describe()
    assert facts["store"] == "sqlite" and facts["keyword_search"] == "fts5"
    assert facts["semantic_search"] == "pending_embedding_model"
    assert facts["pending_embeddings"] == 2
    assert [e["content"] for e in store.search_entries("kitten", bucket_id="agent:a")] == [
        "The kitten sleeps on the sofa."
    ]
    assert store.search_entries("feline", bucket_id="agent:a") == []  # no keyword overlap
    assert store.backfill_embeddings() == 0  # still pending, nothing raised

    # The model arrives: the backfill embeds the pending entries.
    embedder.available = True
    assert store.backfill_embeddings() == 2
    assert store.pending_embeddings() == 0
    assert store.vector_enabled is True
    assert store.describe()["semantic_search"] == "ready"

    # Semantic hit with no shared keyword; keyword + semantic fuse as "hybrid".
    (hit,) = [r for r in store.search_entries("feline", bucket_id="agent:a", limit=1)]
    assert hit["content"] == "The kitten sleeps on the sofa." and hit["match"] == "semantic"
    assert hit["similarity"] > 0.9
    top = store.search_entries("kitten", bucket_id="agent:a", limit=2)[0]
    assert top["match"] == "hybrid" and top["content"].startswith("The kitten")
    similar = store.find_similar_entries("cat", bucket_id="agent:a", threshold=0.9)
    assert [entry["content"] for entry in similar] == ["The kitten sleeps on the sofa."]


def test_changed_content_is_re_embedded(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, available=True)
    store.append_entry(
        bucket_id="b",
        session_id="b",
        memory_scope="agent",
        entry={"id": "e", "content": "cat"},
        source="t",
    )
    assert store.backfill_embeddings() == 1 and store.pending_embeddings() == 0
    store.append_entry(
        bucket_id="b",
        session_id="b",
        memory_scope="agent",
        entry={"id": "e", "content": "car"},
        source="t",
    )
    assert store.pending_embeddings() == 1
    assert store.backfill_embeddings() == 1
    assert store.search_entries("vehicle", bucket_id="b")[0]["content"] == "car"


def test_backfill_probe_reports_ready_without_entries(tmp_path: Path) -> None:
    from locus_runtime.memory.contract import EmbedderStatus

    class _Lazy(ConceptEmbedder):
        def __init__(self) -> None:
            super().__init__(available=True)
            self.answered = False

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            vectors = super().embed(texts)
            self.answered = True
            return vectors

        def status(self) -> EmbedderStatus:
            if self.answered:
                return super().status()
            return EmbedderStatus(state="pending_embedding_model", model=self.model)

    embedder = _Lazy()
    store = SQLiteLongTermMemoryStore(str(tmp_path / "m.db"), embedder=embedder)
    assert store.describe()["semantic_search"] == "pending_embedding_model"
    assert store.backfill_embeddings() == 0
    assert embedder.calls == [["locus memory embedding probe"]]  # fixed text, no user content
    assert store.describe()["semantic_search"] == "ready"


def test_fts_query_text_is_never_syntax(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _add(store, 'He said "hello" NEAR the door')
    for query in ('"hello', "hello OR", "NEAR(", "door*", "-hello", "a:b", "'; DROP TABLE x; --"):
        store.search_entries(query, bucket_id="agent:a")  # must not raise
    assert store.search_entries('"hello" AND', bucket_id="agent:a")
    assert store.get_entries(bucket_id="agent:a")  # table intact


def test_like_fallback_when_fts5_is_missing(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.fts_enabled = False
    _add(store, "Quarterly invoice paid")
    _add(store, "codexzyfile")
    assert [e["content"] for e in store.search_entries("invoice", bucket_id="agent:a")] == [
        "Quarterly invoice paid"
    ]
    # "_" is escaped, not a LIKE wildcard: "x_y" must not match "xzy".
    assert store.search_entries("dex_yfi", bucket_id="agent:a") == []


def test_schema_is_idempotent_and_versioned(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _add(store, "kept across restarts")
    store.close()
    again = SQLiteLongTermMemoryStore(store.path)
    again.initialize()
    again.initialize()
    assert [e["content"] for e in again.get_entries(bucket_id="agent:a")] == [
        "kept across restarts"
    ]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] in (0, 1)
    again.close()


def test_identifiers_and_metadata_are_bounded(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        store.append_entry(
            bucket_id="b" * (MAX_IDENTIFIER_CHARS + 1),
            session_id="s",
            memory_scope="agent",
            entry={"content": "x"},
            source="t",
        )
    with pytest.raises(ValueError):
        store.append_entry(
            bucket_id="", session_id="s", memory_scope="agent", entry={"content": "x"}, source="t"
        )
    store.append_entry(
        bucket_id="b",
        session_id="s",
        memory_scope="agent",
        entry={"content": "big", "blob": "y" * 200_000, "small": "ok"},
        source="t",
    )
    (entry,) = store.get_entries(bucket_id="b")
    assert entry["metadata"].get("metadata_truncated") is True
    assert entry["metadata"].get("small") == "ok" and "blob" not in entry["metadata"]


def test_collections_registry_is_idempotent(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert bootstrap.ensure_personal_collection(store) is True
    assert bootstrap.ensure_personal_collection(store) is False
    (personal,) = store.list_collections()
    assert personal["id"] == "personal" and personal["name"] == "Personal"
    store.remove_collection("personal")
    assert store.list_collections() == []


def test_catalog_entry_never_overwrites_an_existing_collection() -> None:
    catalog: dict[str, dict] = {}
    assert bootstrap.ensure_catalog_entry(catalog) is True
    catalog["personal"]["document_count"] = 3
    assert bootstrap.ensure_catalog_entry(catalog) is False
    assert catalog["personal"]["document_count"] == 3
    assert catalog["personal"]["vector_store_id"] == "platform"


def test_backfill_worker_drains_in_batches(tmp_path: Path) -> None:
    store, embedder = _store(tmp_path, available=True)
    for index in range(5):
        _add(store, f"cat number {index}")
    worker = EmbeddingBackfillWorker(store, batch_size=2)
    assert worker.run_once() == 5
    assert store.pending_embeddings() == 0
    assert len(embedder.calls) == 3


def test_describe_and_logs_carry_no_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, _ = _store(tmp_path)
    secret = "PRIVATE-NOTE-4471"
    _add(store, f"remember {secret}")
    store.search_entries(secret, bucket_id="agent:a")
    store.backfill_embeddings()
    assert secret not in repr(store.describe())
    assert secret not in caplog.text


@POSIX_ONLY
def test_store_file_is_owner_only(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _add(store, "private")
    db = Path(store.path)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    for sidecar in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) & 0o077 == 0


@POSIX_ONLY
def test_symlinked_store_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.db"
    target.write_bytes(b"")
    link = tmp_path / "memory" / "locus-memory.db"
    link.parent.mkdir()
    link.symlink_to(target)
    with pytest.raises(OSError):
        prepare_private_file(link)


def test_prepare_private_file_creates_directory_and_file(tmp_path: Path) -> None:
    path = tmp_path / "home" / "data" / "memory" / "locus-memory.db"
    prepare_private_file(path)
    prepare_private_file(path)  # idempotent
    assert path.is_file()
