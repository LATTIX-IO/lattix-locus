"""Tests for WS6: Redis Write-Ahead Log durability."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from app.platform_services import RedisMemoryStore


@pytest.fixture
def wal_dir(tmp_path):
    return tmp_path / "memory-wal"


@pytest.fixture
def store_with_wal(wal_dir):
    with patch.dict(
        os.environ,
        {
            "LOCUS_MEMORY_WAL_ENABLED": "true",
            "LOCUS_MEMORY_WAL_DIR": str(wal_dir),
        },
    ):
        store = RedisMemoryStore.__new__(RedisMemoryStore)
        store.url = ""
        store.enabled = False
        store._client = None
        store.max_entries = 200
        store.wal_enabled = True
        store.wal_dir = wal_dir
    return store


class TestWalAppend:
    def test_creates_wal_file(self, store_with_wal, wal_dir):
        store_with_wal._wal_append("sess-1", {"content": "hello"})
        wal_file = wal_dir / "sess-1.jsonl"
        assert wal_file.exists()
        lines = wal_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["content"] == "hello"

    def test_appends_multiple(self, store_with_wal, wal_dir):
        store_with_wal._wal_append("sess-1", {"content": "a"})
        store_with_wal._wal_append("sess-1", {"content": "b"})
        wal_file = wal_dir / "sess-1.jsonl"
        lines = wal_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_disabled_noop(self, wal_dir):
        store = RedisMemoryStore.__new__(RedisMemoryStore)
        store.wal_enabled = False
        store.wal_dir = wal_dir
        store._wal_append("sess-1", {"content": "test"})
        assert not (wal_dir / "sess-1.jsonl").exists()


class TestWalRecover:
    def test_recovers_entries(self, store_with_wal, wal_dir):
        store_with_wal._wal_append("sess-1", {"content": "a"})
        store_with_wal._wal_append("sess-1", {"content": "b"})
        recovered = store_with_wal._wal_recover("sess-1")
        assert len(recovered) == 2
        assert recovered[0]["content"] == "a"
        assert recovered[1]["content"] == "b"

    def test_recover_with_limit(self, store_with_wal, wal_dir):
        for i in range(10):
            store_with_wal._wal_append("sess-1", {"content": f"entry-{i}"})
        recovered = store_with_wal._wal_recover("sess-1", limit=3)
        assert len(recovered) == 3
        assert recovered[0]["content"] == "entry-7"

    def test_recover_missing_file(self, store_with_wal):
        recovered = store_with_wal._wal_recover("nonexistent")
        assert recovered == []

    def test_recover_disabled(self, wal_dir):
        store = RedisMemoryStore.__new__(RedisMemoryStore)
        store.wal_enabled = False
        store.wal_dir = wal_dir
        assert store._wal_recover("sess-1") == []


class TestWalCleanup:
    def test_removes_wal_file(self, store_with_wal, wal_dir):
        store_with_wal._wal_append("sess-1", {"content": "test"})
        assert (wal_dir / "sess-1.jsonl").exists()
        store_with_wal.cleanup_wal("sess-1")
        assert not (wal_dir / "sess-1.jsonl").exists()

    def test_cleanup_missing_is_noop(self, store_with_wal):
        store_with_wal.cleanup_wal("nonexistent")  # should not raise


class TestAppendEntryWithWal:
    def test_append_writes_wal_when_redis_unavailable(self, store_with_wal, wal_dir):
        store_with_wal.append_entry("sess-1", {"content": "fallback"})
        recovered = store_with_wal._wal_recover("sess-1")
        assert len(recovered) == 1
        assert recovered[0]["content"] == "fallback"

    def test_get_entries_falls_back_to_wal(self, store_with_wal, wal_dir):
        store_with_wal._wal_append("sess-1", {"content": "from-wal"})
        entries = store_with_wal.get_entries("sess-1")
        assert len(entries) == 1
        assert entries[0]["content"] == "from-wal"
