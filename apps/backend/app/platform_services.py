from __future__ import annotations

import json
import os
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from locus_runtime.legacy import LEGACY_TABLES, normalize_legacy_identifiers

LOGGER = logging.getLogger(__name__)

def _legacy_table_name(current: str) -> str | None:
    for legacy, name in LEGACY_TABLES.items():
        if name == current:
            return legacy
    return None


def _rename_legacy_tables_postgres(cursor: Any, tables: tuple[str, ...]) -> None:
    """Rename pre-Locus tables in place so existing data carries over."""
    for current in tables:
        legacy = _legacy_table_name(current)
        if legacy is None:
            continue
        cursor.execute("SELECT to_regclass(%s), to_regclass(%s)", (legacy, current))
        row = cursor.fetchone()
        if row and row[0] and not row[1]:
            # Both names come from the LEGACY_TABLES constant, never from input.
            cursor.execute(f"ALTER TABLE {legacy} RENAME TO {current}")
            LOGGER.info("renamed legacy table %s to %s", legacy, current)


def _rename_legacy_tables_sqlite(connection: Any, tables: tuple[str, ...]) -> None:
    for current in tables:
        legacy = _legacy_table_name(current)
        if legacy is None:
            continue
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
                (legacy, current),
            ).fetchall()
        }
        if legacy in names and current not in names:
            connection.execute(f"ALTER TABLE {legacy} RENAME TO {current}")
            LOGGER.info("renamed legacy table %s to %s", legacy, current)

PSYCOPG_IMPORT_ERROR: str | None = None

try:
    import psycopg
    from psycopg import sql as psycopg_sql
except Exception as exc:  # pragma: no cover - optional dependency in some local test paths
    psycopg = None
    psycopg_sql = None
    PSYCOPG_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

try:
    import redis
except Exception:  # pragma: no cover - optional dependency in some local test paths
    redis = None

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - optional dependency in some local test paths
    GraphDatabase = None

# Embedded long-term memory (LOCUS-387): re-exported so the backend's composition
# root resolves both adapters of the memory port from this module.
from locus_runtime.memory.contract import Embedder, EmbeddingUnavailable  # noqa: E402
from locus_runtime.memory.sqlite_store import SQLiteLongTermMemoryStore  # noqa: E402,F401


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _safe_json_loads(payload: Any) -> Any:
    # psycopg returns JSONB columns as already-decoded dicts/lists; membership
    # checks against a set would raise on unhashable values, so test explicitly.
    if payload is None:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    if isinstance(payload, str) and not payload.strip():
        return None
    try:
        return json.loads(str(payload))
    except Exception:  # noqa: BLE001
        return payload


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(item):.8f}" for item in values) + "]"


def _validated_embedding_dimensions(value: int) -> int:
    return max(8, min(int(value), 3_072))


def _vector_type_sql(dimensions: int) -> Any:
    assert psycopg_sql is not None
    return psycopg_sql.SQL("vector({})").format(
        psycopg_sql.SQL(str(_validated_embedding_dimensions(dimensions)))
    )


def _embedding_column_statement(dimensions: int) -> str:
    validated_dimensions = _validated_embedding_dimensions(dimensions)
    return (
        "ALTER TABLE locus_long_term_memory "
        f"ADD COLUMN IF NOT EXISTS embedding vector({validated_dimensions})"
    )


class _BasePostgresService:
    def __init__(self, dsn: str) -> None:
        self.dsn = str(dsn or "").strip()
        self.enabled = bool(self.dsn) and psycopg is not None
        self._initialized = False
        self.import_error = PSYCOPG_IMPORT_ERROR

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if not self.enabled:
            raise RuntimeError("Postgres service is not enabled")
        assert psycopg is not None
        with psycopg.connect(self.dsn, autocommit=True, connect_timeout=5) as connection:
            yield connection

    def healthcheck(self) -> bool:
        if not self.enabled:
            return False
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception:  # noqa: BLE001
            return False

    def status(self) -> tuple[str, str]:
        if not self.dsn:
            return "disabled", "POSTGRES_DSN is not configured"
        if psycopg is None:
            return "degraded", self.import_error or "psycopg is unavailable"
        if self.healthcheck():
            return "connected", ""
        return "degraded", "Postgres connection healthcheck failed"


class PostgresStateStore(_BasePostgresService):
    SECTION_KEY_PREFIX = "section:"

    def initialize(self) -> None:
        if not self.enabled or self._initialized:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                _rename_legacy_tables_postgres(cursor, ("locus_state_store",))
                cursor.execute(
                    """
					CREATE TABLE IF NOT EXISTS locus_state_store (
						state_key TEXT PRIMARY KEY,
						payload JSONB NOT NULL,
						updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
					)
					"""
                )
        self._initialized = True

    def load_state(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT state_key, payload FROM locus_state_store")
                rows = cursor.fetchall()
        if not rows:
            return None
        legacy: dict[str, Any] = {}
        sections: dict[str, Any] = {}
        for state_key, raw_payload in rows:
            value = normalize_legacy_identifiers(_safe_json_loads(raw_payload))
            key = str(state_key)
            if key == "global" and isinstance(value, dict):
                legacy = value
            elif key.startswith(self.SECTION_KEY_PREFIX):
                sections[key[len(self.SECTION_KEY_PREFIX) :]] = value
        if not legacy and not sections:
            return None
        # Per-section rows are authoritative; the legacy "global" row only fills
        # sections that have not been rewritten since the sectioned format landed.
        merged = dict(legacy)
        merged.update(sections)
        return merged

    def save_state(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.initialize()
        encoded_payload = json.dumps(payload, default=_json_default)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					INSERT INTO locus_state_store (state_key, payload, updated_at)
					VALUES (%s, %s::jsonb, timezone('utc', now()))
					ON CONFLICT (state_key)
					DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
					""",
                    ("global", encoded_payload),
                )

    def save_state_sections(
        self, encoded_sections: dict[str, str], *, replace_all: bool = False
    ) -> None:
        """Upsert only the store sections whose content changed.

        ``encoded_sections`` maps section name to its already-JSON-encoded payload.
        ``replace_all`` marks a full snapshot write, after which the legacy
        single-row "global" payload is removed.
        """
        if not self.enabled or not encoded_sections:
            return
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for section, encoded in encoded_sections.items():
                    cursor.execute(
                        """
						INSERT INTO locus_state_store (state_key, payload, updated_at)
						VALUES (%s, %s::jsonb, timezone('utc', now()))
						ON CONFLICT (state_key)
						DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
						""",
                        (f"{self.SECTION_KEY_PREFIX}{section}", encoded),
                    )
                if replace_all:
                    cursor.execute(
                        "DELETE FROM locus_state_store WHERE state_key = %s",
                        ("global",),
                    )


class PostgresAuditLog(_BasePostgresService):
    """Append-only audit event log.

    One small insert per audit event instead of rewriting the full audit
    section blob on every store persist — audit events are immutable, so the
    append-only table is both cheaper and a better tamper-evidence posture.
    """

    def initialize(self) -> None:
        if not self.enabled or self._initialized:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                _rename_legacy_tables_postgres(cursor, ("locus_audit_events",))
                cursor.execute(
                    """
					CREATE TABLE IF NOT EXISTS locus_audit_events (
						id TEXT PRIMARY KEY,
						action TEXT NOT NULL,
						actor TEXT NOT NULL,
						outcome TEXT NOT NULL,
						created_at TEXT NOT NULL,
						metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
						inserted_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
					)
					"""
                )
        self._initialized = True

    def append(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					INSERT INTO locus_audit_events
						(id, action, actor, outcome, created_at, metadata)
					VALUES (%s, %s, %s, %s, %s, %s::jsonb)
					ON CONFLICT (id) DO NOTHING
					""",
                    (
                        str(event.get("id") or ""),
                        str(event.get("action") or ""),
                        str(event.get("actor") or ""),
                        str(event.get("outcome") or ""),
                        str(event.get("created_at") or ""),
                        json.dumps(event.get("metadata") or {}, default=_json_default),
                    ),
                )

    def load_recent(self, limit: int = 2000) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					SELECT id, action, actor, outcome, created_at, metadata
					FROM locus_audit_events
					ORDER BY inserted_at DESC
					LIMIT %s
					""",
                    (max(1, int(limit)),),
                )
                rows = cursor.fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            metadata = _safe_json_loads(row[5])
            events.append(
                {
                    "id": str(row[0]),
                    "action": str(row[1]),
                    "actor": str(row[2]),
                    "outcome": str(row[3]),
                    "created_at": str(row[4]),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
        return events


class _BaseSQLiteService:
    """Zero-container persistence backend (resource plan 2.3).

    Stdlib sqlite3 with WAL journaling; one shared connection guarded by a lock
    (writes are short and infrequent — sections only persist when changed).
    """

    def __init__(self, path: str) -> None:
        self.path = str(path or "").strip()
        self.enabled = bool(self.path)
        self._initialized = False
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError("SQLite service is not enabled")
        if self._connection is None:
            db_path = Path(self.path)
            if db_path.parent and not db_path.parent.exists():
                db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
        return self._connection

    def healthcheck(self) -> bool:
        if not self.enabled:
            return False
        try:
            with self._lock:
                self._connect().execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False


class SQLiteStateStore(_BaseSQLiteService):
    """Drop-in alternative to PostgresStateStore for zero-container local mode."""

    SECTION_KEY_PREFIX = "section:"

    def initialize(self) -> None:
        if not self.enabled or self._initialized:
            return
        with self._lock:
            _rename_legacy_tables_sqlite(self._connect(), ("locus_state_store",))
            self._connect().execute(
                """
				CREATE TABLE IF NOT EXISTS locus_state_store (
					state_key TEXT PRIMARY KEY,
					payload TEXT NOT NULL,
					updated_at TEXT NOT NULL DEFAULT (datetime('now'))
				)
				"""
            )
            self._connect().commit()
        self._initialized = True

    def load_state(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        self.initialize()
        with self._lock:
            rows = (
                self._connect()
                .execute("SELECT state_key, payload FROM locus_state_store")
                .fetchall()
            )
        if not rows:
            return None
        legacy: dict[str, Any] = {}
        sections: dict[str, Any] = {}
        for state_key, raw_payload in rows:
            value = normalize_legacy_identifiers(_safe_json_loads(raw_payload))
            key = str(state_key)
            if key == "global" and isinstance(value, dict):
                legacy = value
            elif key.startswith(self.SECTION_KEY_PREFIX):
                sections[key[len(self.SECTION_KEY_PREFIX) :]] = value
        if not legacy and not sections:
            return None
        merged = dict(legacy)
        merged.update(sections)
        return merged

    def save_state(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.initialize()
        encoded_payload = json.dumps(payload, default=_json_default)
        with self._lock:
            connection = self._connect()
            connection.execute(
                "INSERT OR REPLACE INTO locus_state_store (state_key, payload, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("global", encoded_payload),
            )
            connection.commit()

    def save_state_sections(
        self, encoded_sections: dict[str, str], *, replace_all: bool = False
    ) -> None:
        if not self.enabled or not encoded_sections:
            return
        self.initialize()
        with self._lock:
            connection = self._connect()
            for section, encoded in encoded_sections.items():
                connection.execute(
                    "INSERT OR REPLACE INTO locus_state_store (state_key, payload, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (f"{self.SECTION_KEY_PREFIX}{section}", encoded),
                )
            if replace_all:
                connection.execute(
                    "DELETE FROM locus_state_store WHERE state_key = ?", ("global",)
                )
            connection.commit()


class SQLiteAuditLog(_BaseSQLiteService):
    """Append-only audit log for zero-container local mode."""

    def initialize(self) -> None:
        if not self.enabled or self._initialized:
            return
        with self._lock:
            _rename_legacy_tables_sqlite(self._connect(), ("locus_audit_events",))
            self._connect().execute(
                """
				CREATE TABLE IF NOT EXISTS locus_audit_events (
					id TEXT PRIMARY KEY,
					action TEXT NOT NULL,
					actor TEXT NOT NULL,
					outcome TEXT NOT NULL,
					created_at TEXT NOT NULL,
					metadata TEXT NOT NULL DEFAULT '{}'
				)
				"""
            )
            self._connect().commit()
        self._initialized = True

    def append(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.initialize()
        with self._lock:
            connection = self._connect()
            connection.execute(
                "INSERT OR IGNORE INTO locus_audit_events "
                "(id, action, actor, outcome, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(event.get("id") or ""),
                    str(event.get("action") or ""),
                    str(event.get("actor") or ""),
                    str(event.get("outcome") or ""),
                    str(event.get("created_at") or ""),
                    json.dumps(event.get("metadata") or {}, default=_json_default),
                ),
            )
            connection.commit()

    def load_recent(self, limit: int = 2000) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        self.initialize()
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    "SELECT id, action, actor, outcome, created_at, metadata "
                    "FROM locus_audit_events ORDER BY rowid DESC LIMIT ?",
                    (max(1, int(limit)),),
                )
                .fetchall()
            )
        events: list[dict[str, Any]] = []
        for row in rows:
            metadata = _safe_json_loads(row[5])
            events.append(
                {
                    "id": str(row[0]),
                    "action": str(row[1]),
                    "actor": str(row[2]),
                    "outcome": str(row[3]),
                    "created_at": str(row[4]),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
        return events


class RedisMemoryStore:
    def __init__(self, url: str) -> None:
        self.url = str(url or "").strip()
        self.enabled = bool(self.url) and redis is not None
        self._client = redis.from_url(self.url, decode_responses=True) if self.enabled else None
        self.max_entries = max(10, int(os.getenv("LOCUS_SHORT_TERM_MEMORY_MAX", "200")))
        self.wal_enabled = os.getenv("LOCUS_MEMORY_WAL_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.wal_dir = Path(os.getenv("LOCUS_MEMORY_WAL_DIR", ".locus/memory-wal"))

    def _key(self, session_id: str) -> str:
        return f"locus:memory:short:{session_id}"

    def _nonce_key(self, nonce: str) -> str:
        return f"locus:a2a:nonce:{nonce}"

    def healthcheck(self) -> bool:
        if not self.enabled or self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except Exception:  # noqa: BLE001
            return False

    def get_entries(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.enabled or self._client is None:
            return self._wal_recover(session_id, limit=limit)
        try:
            start = -max(1, limit)
            payloads = self._client.lrange(self._key(session_id), start, -1)
        except Exception:  # noqa: BLE001
            payloads = []
        entries: list[dict[str, Any]] = []
        for item in payloads:
            decoded = _safe_json_loads(item)
            if isinstance(decoded, dict):
                entries.append(decoded)
        if not entries:
            recovered = self._wal_recover(session_id, limit=limit)
            if recovered:
                self.load_entries(session_id, recovered)
                return recovered
        return entries

    def append_entry(self, session_id: str, entry: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            self._wal_append(session_id, entry)
            return
        try:
            self._client.rpush(self._key(session_id), json.dumps(entry, default=_json_default))
            self._client.ltrim(self._key(session_id), -self.max_entries, -1)
        except Exception:  # noqa: BLE001
            pass
        self._wal_append(session_id, entry)

    def load_entries(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        if not self.enabled or self._client is None or not entries:
            return
        try:
            serialized = [
                json.dumps(item, default=_json_default)
                for item in entries
                if isinstance(item, dict)
            ]
            if not serialized:
                return
            self._client.rpush(self._key(session_id), *serialized)
            self._client.ltrim(self._key(session_id), -self.max_entries, -1)
        except Exception:  # noqa: BLE001
            return

    def clear_entries(self, session_id: str) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.delete(self._key(session_id))
        except Exception:  # noqa: BLE001
            return

    def register_nonce_once(self, nonce: str, *, ttl_seconds: int) -> bool:
        if not self.enabled or self._client is None:
            return False
        nonce_text = str(nonce or "").strip()
        if not nonce_text:
            return False
        try:
            created = self._client.set(
                self._nonce_key(nonce_text), "1", ex=max(1, int(ttl_seconds)), nx=True
            )
        except Exception:  # noqa: BLE001
            return False
        return bool(created)

    # -- Write-Ahead Log (WAL) for Redis durability --

    def _wal_path(self, session_id: str) -> Path:
        return self.wal_dir / f"{session_id}.jsonl"

    def _wal_append(self, session_id: str, entry: dict[str, Any]) -> None:
        if not self.wal_enabled:
            return
        try:
            self.wal_dir.mkdir(parents=True, exist_ok=True)
            with open(self._wal_path(session_id), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=_json_default) + "\n")
        except Exception:  # noqa: BLE001
            return

    def _wal_recover(self, session_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if not self.wal_enabled:
            return []
        wal_file = self._wal_path(session_id)
        if not wal_file.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with open(wal_file, encoding="utf-8") as fh:
                for line in fh:
                    decoded = _safe_json_loads(line.strip())
                    if isinstance(decoded, dict):
                        entries.append(decoded)
        except Exception:  # noqa: BLE001
            return []
        return entries[-max(1, limit) :]

    def cleanup_wal(self, session_id: str) -> None:
        if not self.wal_enabled:
            return
        try:
            wal_file = self._wal_path(session_id)
            if wal_file.exists():
                wal_file.unlink()
        except Exception:  # noqa: BLE001
            return


class PostgresLongTermMemoryStore(_BasePostgresService):
    """Long-term memory port adapter on Postgres + pgvector (the full stack).

    Embeddings come from the injected embedder (the gated local engine, LOCUS-378);
    without one, or while it is unavailable, entries are stored without a vector
    and search falls back to keywords.
    """

    store_kind = "postgres"
    backend_label = "Postgres + pgvector"

    def __init__(self, dsn: str, *, embedder: Embedder | None = None) -> None:
        super().__init__(dsn)
        self.vector_enabled = False
        # Must match the embedding model's output (768 for the default
        # nomic-embed-text); vectors of another size are stored without the
        # pgvector column (keyword search still finds them).
        self.embedding_dimensions = _validated_embedding_dimensions(
            int(os.getenv("LOCUS_MEMORY_EMBEDDING_DIMENSIONS", "768"))
        )
        self.embedder = embedder

    @property
    def embedding_model(self) -> str:
        return self.embedder.model if self.embedder is not None else ""

    def describe(self) -> dict[str, Any]:
        status = self.embedder.status() if self.embedder is not None else None
        return {
            "store": self.store_kind,
            "keyword_search": "ilike",
            "vector_extension": "pgvector" if self.vector_enabled else "unavailable",
            "semantic_search": status.state if status is not None else "disabled",
            "semantic_search_reason": status.reason if status is not None else "",
            "embedding_model": self.embedding_model,
        }

    def initialize(self) -> None:
        if not self.enabled or self._initialized:
            return

        with self._connect() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception:  # noqa: BLE001
                    pass

                try:
                    cursor.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                    )
                    row = cursor.fetchone()
                    self.vector_enabled = bool(row and row[0])
                except Exception:  # noqa: BLE001
                    self.vector_enabled = False

                _rename_legacy_tables_postgres(cursor, ("locus_long_term_memory",))
                cursor.execute(
                    """
					CREATE TABLE IF NOT EXISTS locus_long_term_memory (
						id TEXT PRIMARY KEY,
						bucket_id TEXT NOT NULL,
						session_id TEXT NOT NULL,
						memory_scope TEXT NOT NULL,
						source TEXT NOT NULL,
						task_id TEXT,
						content TEXT NOT NULL,
						metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
						embedding_model TEXT,
						embedding_json JSONB,
						created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
						updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
					)
					"""
                )
                if self.vector_enabled:
                    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    cursor.execute(_embedding_column_statement(self.embedding_dimensions))
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS locus_long_term_memory_bucket_idx ON locus_long_term_memory (bucket_id, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS locus_long_term_memory_session_idx ON locus_long_term_memory (session_id, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS locus_long_term_memory_scope_idx ON locus_long_term_memory (memory_scope, created_at DESC)"
                )
                _rename_legacy_tables_postgres(cursor, ("locus_memory_consolidation_queue",))
                cursor.execute(
                    """
					CREATE TABLE IF NOT EXISTS locus_memory_consolidation_queue (
						id TEXT PRIMARY KEY,
						entry_id TEXT NOT NULL UNIQUE,
						bucket_id TEXT NOT NULL,
						session_id TEXT NOT NULL,
						memory_scope TEXT NOT NULL,
						source TEXT NOT NULL,
						task_id TEXT,
						candidate_kind TEXT NOT NULL DEFAULT 'promotion',
						status TEXT NOT NULL DEFAULT 'pending',
						content TEXT NOT NULL,
						metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
						created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
						updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
					)
					"""
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS locus_memory_consolidation_queue_status_idx ON locus_memory_consolidation_queue (status, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS locus_memory_consolidation_queue_bucket_idx ON locus_memory_consolidation_queue (bucket_id, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS locus_memory_consolidation_queue_scope_idx ON locus_memory_consolidation_queue (memory_scope, created_at DESC)"
                )
                if self.vector_enabled:
                    try:
                        cursor.execute(
                            "CREATE INDEX IF NOT EXISTS locus_long_term_memory_embedding_idx ON locus_long_term_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                        )
                    except Exception:  # noqa: BLE001
                        pass
        self._initialized = True

    def _embed_text(self, text: str) -> tuple[list[float] | None, str | None]:
        """Embed through the gated embedder (a gateway ``model_call``, LOCUS-378)."""
        if not text.strip() or self.embedder is None:
            return None, None
        try:
            vectors = self.embedder.embed([text[:8000]])
        except EmbeddingUnavailable:
            return None, None
        except Exception:  # noqa: BLE001 - embeddings never fail a memory write
            LOGGER.warning("long-term memory embedding failed", exc_info=False)
            return None, None
        vector = vectors[0] if vectors else None
        if not isinstance(vector, list) or len(vector) != self.embedding_dimensions:
            return None, None
        return [float(value) for value in vector], self.embedding_model

    def _row_to_entry(self, row: tuple[Any, ...]) -> dict[str, Any]:
        created_at = row[8]
        metadata = _safe_json_loads(row[7]) if row[7] is not None else {}
        payload = dict(metadata) if isinstance(metadata, dict) else {}
        payload.update(
            {
                "id": str(row[0]),
                "bucket_id": str(row[1]),
                "session_id": str(row[2]),
                "memory_scope": str(row[3]),
                "source": str(row[4]),
                "task_id": str(row[5] or ""),
                "content": str(row[6]),
                "metadata": metadata if isinstance(metadata, dict) else {},
                "at": created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at or ""),
                "tier": "long-term",
            }
        )
        return payload

    def _row_to_consolidation_candidate(self, row: tuple[Any, ...]) -> dict[str, Any]:
        created_at = row[9]
        updated_at = row[10]
        metadata = _safe_json_loads(row[12]) if row[12] is not None else {}
        payload = dict(metadata) if isinstance(metadata, dict) else {}
        payload.update(
            {
                "id": str(row[0]),
                "entry_id": str(row[1]),
                "bucket_id": str(row[2]),
                "session_id": str(row[3]),
                "memory_scope": str(row[4]),
                "source": str(row[5]),
                "task_id": str(row[6] or ""),
                "candidate_kind": str(row[7]),
                "status": str(row[8]),
                "content": str(row[11]),
                "metadata": metadata if isinstance(metadata, dict) else {},
                "created_at": created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at or ""),
                "updated_at": updated_at.isoformat()
                if hasattr(updated_at, "isoformat")
                else str(updated_at or ""),
            }
        )
        return payload

    def get_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					SELECT id, bucket_id, session_id, memory_scope, source, task_id, content, metadata, created_at
					FROM locus_long_term_memory
					WHERE (%s IS NULL OR bucket_id = %s)
					AND (%s IS NULL OR session_id = %s)
					AND (%s IS NULL OR memory_scope = %s)
					ORDER BY created_at DESC
					LIMIT %s
					""",
                    (
                        bucket_id,
                        bucket_id,
                        session_id,
                        session_id,
                        memory_scope,
                        memory_scope,
                        max(1, limit),
                    ),
                )
                rows = cursor.fetchall()
        return [self._row_to_entry(row) for row in reversed(rows)]

    def search_entries(
        self,
        query_text: str,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        normalized_query = str(query_text or "").strip()
        if not normalized_query:
            return self.get_entries(
                bucket_id=bucket_id, session_id=session_id, memory_scope=memory_scope, limit=limit
            )

        self.initialize()
        vector, _model = self._embed_text(normalized_query)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if self.vector_enabled and vector:
                    cursor.execute(
                        """
						SELECT id, bucket_id, session_id, memory_scope, source, task_id, content, metadata, created_at
						FROM locus_long_term_memory
						WHERE (%s::text IS NULL OR bucket_id = %s::text)
						AND (%s::text IS NULL OR session_id = %s::text)
						AND (%s::text IS NULL OR memory_scope = %s::text)
						AND embedding IS NOT NULL
						ORDER BY embedding <=> %s::vector, created_at DESC
						LIMIT %s
						""",
                        (
                            bucket_id,
                            bucket_id,
                            session_id,
                            session_id,
                            memory_scope,
                            memory_scope,
                            _vector_literal(vector),
                            max(1, limit),
                        ),
                    )
                    rows = cursor.fetchall()
                else:
                    # Keyword fallback when vector search is unavailable. Match
                    # on individual terms (ranked by how many match) rather than
                    # the whole query as one literal substring, so multi-word
                    # queries still retrieve relevant chunks.
                    terms = [
                        term
                        for term in re.split(r"\W+", normalized_query.lower())
                        if len(term) >= 3
                    ][:12] or [normalized_query[:60].lower()]
                    score_terms = " + ".join(["(content ILIKE %s)::int"] * len(terms))
                    match_clause = " OR ".join(["content ILIKE %s"] * len(terms))
                    like_params = [f"%{term}%" for term in terms]
                    cursor.execute(
                        f"""
						SELECT id, bucket_id, session_id, memory_scope, source, task_id, content, metadata, created_at
						FROM locus_long_term_memory
						WHERE (%s::text IS NULL OR bucket_id = %s::text)
						AND (%s::text IS NULL OR session_id = %s::text)
						AND (%s::text IS NULL OR memory_scope = %s::text)
						AND ({match_clause})
						ORDER BY ({score_terms}) DESC, created_at DESC
						LIMIT %s
						""",
                        (
                            bucket_id,
                            bucket_id,
                            session_id,
                            session_id,
                            memory_scope,
                            memory_scope,
                            *like_params,  # match_clause
                            *like_params,  # score_terms
                            max(1, limit),
                        ),
                    )
                    rows = cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    def append_entry(
        self,
        *,
        bucket_id: str,
        session_id: str,
        memory_scope: str,
        entry: dict[str, Any],
        source: str,
        task_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.initialize()
        content = str(entry.get("content") or json.dumps(entry, default=_json_default))[:4000]
        metadata = dict(entry) if isinstance(entry, dict) else {"raw": entry}
        entry_id = str(entry.get("id") or uuid4())
        vector, embedding_model = self._embed_text(content)
        encoded_metadata = json.dumps(metadata, default=_json_default)
        encoded_embedding = json.dumps(vector, default=_json_default) if vector else None

        with self._connect() as connection:
            with connection.cursor() as cursor:
                if self.vector_enabled and vector:
                    cursor.execute(
                        """
						INSERT INTO locus_long_term_memory (
							id, bucket_id, session_id, memory_scope, source, task_id, content,
							metadata, embedding_model, embedding_json, embedding, created_at, updated_at
						)
						VALUES (
							%s, %s, %s, %s, %s, %s, %s,
							%s::jsonb, %s, %s::jsonb, %s::vector, timezone('utc', now()), timezone('utc', now())
						)
						ON CONFLICT (id)
						DO UPDATE SET
							bucket_id = EXCLUDED.bucket_id,
							session_id = EXCLUDED.session_id,
							memory_scope = EXCLUDED.memory_scope,
							source = EXCLUDED.source,
							task_id = EXCLUDED.task_id,
							content = EXCLUDED.content,
							metadata = EXCLUDED.metadata,
							embedding_model = EXCLUDED.embedding_model,
							embedding_json = EXCLUDED.embedding_json,
							embedding = EXCLUDED.embedding,
							updated_at = timezone('utc', now())
						""",
                        (
                            entry_id,
                            bucket_id,
                            session_id,
                            memory_scope,
                            source,
                            task_id,
                            content,
                            encoded_metadata,
                            embedding_model,
                            encoded_embedding,
                            _vector_literal(vector),
                        ),
                    )
                else:
                    cursor.execute(
                        """
						INSERT INTO locus_long_term_memory (
							id, bucket_id, session_id, memory_scope, source, task_id, content,
							metadata, embedding_model, embedding_json, created_at, updated_at
						)
						VALUES (
							%s, %s, %s, %s, %s, %s, %s,
							%s::jsonb, %s, %s::jsonb, timezone('utc', now()), timezone('utc', now())
						)
						ON CONFLICT (id)
						DO UPDATE SET
							bucket_id = EXCLUDED.bucket_id,
							session_id = EXCLUDED.session_id,
							memory_scope = EXCLUDED.memory_scope,
							source = EXCLUDED.source,
							task_id = EXCLUDED.task_id,
							content = EXCLUDED.content,
							metadata = EXCLUDED.metadata,
							embedding_model = EXCLUDED.embedding_model,
							embedding_json = EXCLUDED.embedding_json,
							updated_at = timezone('utc', now())
						""",
                        (
                            entry_id,
                            bucket_id,
                            session_id,
                            memory_scope,
                            source,
                            task_id,
                            content,
                            encoded_metadata,
                            embedding_model,
                            encoded_embedding,
                        ),
                    )

    def enqueue_consolidation_candidate(
        self,
        *,
        bucket_id: str,
        session_id: str,
        memory_scope: str,
        entry: dict[str, Any],
        source: str,
        task_id: str | None = None,
        candidate_kind: str = "promotion",
    ) -> None:
        if not self.enabled:
            return
        self.initialize()
        entry_id = str(entry.get("id") or uuid4())
        candidate_id = f"consolidation:{entry_id}"
        content = str(entry.get("content") or json.dumps(entry, default=_json_default))[:4000]
        metadata = dict(entry) if isinstance(entry, dict) else {"raw": entry}
        metadata.setdefault("queued_for_consolidation", True)
        metadata.setdefault("candidate_kind", candidate_kind)
        encoded_metadata = json.dumps(metadata, default=_json_default)

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					INSERT INTO locus_memory_consolidation_queue (
						id, entry_id, bucket_id, session_id, memory_scope, source, task_id,
						candidate_kind, status, content, metadata, created_at, updated_at
					)
					VALUES (
						%s, %s, %s, %s, %s, %s, %s,
						%s, 'pending', %s, %s::jsonb, timezone('utc', now()), timezone('utc', now())
					)
					ON CONFLICT (entry_id)
					DO UPDATE SET
						bucket_id = EXCLUDED.bucket_id,
						session_id = EXCLUDED.session_id,
						memory_scope = EXCLUDED.memory_scope,
						source = EXCLUDED.source,
						task_id = EXCLUDED.task_id,
						candidate_kind = EXCLUDED.candidate_kind,
						status = 'pending',
						content = EXCLUDED.content,
						metadata = EXCLUDED.metadata,
						updated_at = timezone('utc', now())
					""",
                    (
                        candidate_id,
                        entry_id,
                        bucket_id,
                        session_id,
                        memory_scope,
                        source,
                        task_id,
                        str(candidate_kind or "promotion"),
                        content,
                        encoded_metadata,
                    ),
                )

    def list_consolidation_candidates(
        self,
        *,
        bucket_id: str | None = None,
        memory_scope: str | None = None,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        self.initialize()

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					SELECT id, entry_id, bucket_id, session_id, memory_scope, source, task_id,
					candidate_kind, status, created_at, updated_at, content, metadata
					FROM locus_memory_consolidation_queue
					WHERE (%s IS NULL OR bucket_id = %s)
					AND (%s IS NULL OR memory_scope = %s)
					AND (%s IS NULL OR status = %s)
					ORDER BY created_at DESC
					LIMIT %s
					""",
                    (
                        bucket_id,
                        bucket_id,
                        memory_scope,
                        memory_scope,
                        status,
                        status,
                        max(1, limit),
                    ),
                )
                rows = cursor.fetchall()
        return [self._row_to_consolidation_candidate(row) for row in reversed(rows)]

    def mark_consolidation_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.initialize()

        with self._connect() as connection:
            with connection.cursor() as cursor:
                if isinstance(extra_metadata, dict) and extra_metadata:
                    cursor.execute(
                        """
						UPDATE locus_memory_consolidation_queue
						SET status = %s,
						metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
						updated_at = timezone('utc', now())
						WHERE id = %s
						""",
                        (
                            str(status or "pending"),
                            json.dumps(extra_metadata, default=_json_default),
                            str(candidate_id),
                        ),
                    )
                else:
                    cursor.execute(
                        """
						UPDATE locus_memory_consolidation_queue
						SET status = %s,
						updated_at = timezone('utc', now())
						WHERE id = %s
						""",
                        (str(status or "pending"), str(candidate_id)),
                    )

    def clear_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.initialize()
        if not any((bucket_id, session_id, memory_scope)):
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					DELETE FROM locus_long_term_memory
					WHERE (%s IS NULL OR bucket_id = %s)
					AND (%s IS NULL OR session_id = %s)
					AND (%s IS NULL OR memory_scope = %s)
					""",
                    (
                        bucket_id,
                        bucket_id,
                        session_id,
                        session_id,
                        memory_scope,
                        memory_scope,
                    ),
                )

    def find_similar_entries(
        self,
        text: str,
        *,
        bucket_id: str | None = None,
        memory_scope: str | None = None,
        threshold: float = 0.92,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not self.enabled or not self.vector_enabled:
            return []
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return []
        self.initialize()
        vector, _model = self._embed_text(normalized_text)
        if not vector:
            return []
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
					SELECT id, bucket_id, session_id, memory_scope, source, task_id, content, metadata, created_at,
						1 - (embedding <=> %s::vector) AS similarity
					FROM locus_long_term_memory
					WHERE embedding IS NOT NULL
					AND (%s IS NULL OR bucket_id = %s)
					AND (%s IS NULL OR memory_scope = %s)
					AND 1 - (embedding <=> %s::vector) > %s
					ORDER BY similarity DESC
					LIMIT %s
					""",
                    (
                        _vector_literal(vector),
                        bucket_id,
                        bucket_id,
                        memory_scope,
                        memory_scope,
                        _vector_literal(vector),
                        float(threshold),
                        max(1, limit),
                    ),
                )
                rows = cursor.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            entry = self._row_to_entry(row[:9])
            entry["similarity"] = float(row[9]) if row[9] is not None else 0.0
            results.append(entry)
        return results

    def count_consolidation_candidates(self, *, status: str = "pending") -> int:
        if not self.enabled:
            return 0
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM locus_memory_consolidation_queue WHERE status = %s",
                    (str(status or "pending"),),
                )
                row = cursor.fetchone()
        return int(row[0]) if row else 0


class Neo4jRunGraph:
    def __init__(self, uri: str, username: str, password: str) -> None:
        self.uri = str(uri or "").strip()
        self.username = str(username or "").strip()
        self.password = str(password or "").strip()
        self.enabled = bool(
            self.uri and self.username and self.password and GraphDatabase is not None
        )
        self._driver = (
            GraphDatabase.driver(self.uri, auth=(self.username, self.password))
            if self.enabled
            else None
        )

    def healthcheck(self) -> bool:
        if not self.enabled or self._driver is None:
            return False
        try:
            with self._driver.session() as session:
                result = session.run("RETURN 1 AS ok")
                row = result.single()
            return bool(row and row["ok"] == 1)
        except Exception:  # noqa: BLE001
            return False

    def record_run(
        self, *, run_id: str, title: str, agent: str | None, workflow: str | None
    ) -> None:
        if not self.enabled or self._driver is None:
            return
        try:
            with self._driver.session() as session:
                session.run(
                    """
					MERGE (r:WorkflowRun {id: $run_id})
					SET r.title = $title,
						r.updated_at = datetime()
					WITH r
					FOREACH (_ IN CASE WHEN $agent IS NULL OR $agent = '' THEN [] ELSE [1] END |
						MERGE (a:Agent {name: $agent})
						MERGE (r)-[:EXECUTED_BY]->(a)
					)
					FOREACH (_ IN CASE WHEN $workflow IS NULL OR $workflow = '' THEN [] ELSE [1] END |
						MERGE (w:Workflow {name: $workflow})
						MERGE (r)-[:PART_OF]->(w)
					)
					""",
                    {
                        "run_id": run_id,
                        "title": title,
                        "agent": agent,
                        "workflow": workflow,
                    },
                )
        except Exception:  # noqa: BLE001
            return

    def project_memory_summary(self, *, projection: dict[str, Any]) -> None:
        if not self.enabled or self._driver is None:
            return

        owner = projection.get("owner") if isinstance(projection.get("owner"), dict) else {}
        memory = projection.get("memory") if isinstance(projection.get("memory"), dict) else {}
        topics = projection.get("topics") if isinstance(projection.get("topics"), list) else []
        evidences = (
            projection.get("evidences") if isinstance(projection.get("evidences"), list) else []
        )

        if not owner or not memory:
            return

        try:
            with self._driver.session() as session:
                session.run(
                    """
					MERGE (owner:KnowledgeOwner {id: $owner_id})
					SET owner.name = $owner_name,
						owner.owner_type = $owner_type,
						owner.memory_scope = $owner_scope,
						owner.updated_at = datetime()

					MERGE (memory:KnowledgeMemory {id: $memory_id})
					SET memory.content = $memory_content,
						memory.kind = $memory_kind,
						memory.bucket_id = $memory_bucket_id,
						memory.session_id = $memory_session_id,
						memory.memory_scope = $memory_scope,
						memory.candidate_kind = $candidate_kind,
						memory.source_count = $source_count,
						memory.created_at = $memory_created_at,
						memory.updated_at = datetime()

					MERGE (owner)-[rel:OWNS_MEMORY]->(memory)
					SET rel.memory_scope = $memory_scope,
						rel.updated_at = datetime()
					""",
                    {
                        "owner_id": str(owner.get("id") or ""),
                        "owner_name": str(owner.get("name") or ""),
                        "owner_type": str(owner.get("type") or "Owner"),
                        "owner_scope": str(
                            owner.get("memory_scope") or memory.get("memory_scope") or "session"
                        ),
                        "memory_id": str(memory.get("id") or ""),
                        "memory_content": str(memory.get("content") or ""),
                        "memory_kind": str(memory.get("kind") or "memory-consolidation"),
                        "memory_bucket_id": str(memory.get("bucket_id") or ""),
                        "memory_session_id": str(memory.get("session_id") or ""),
                        "memory_scope": str(memory.get("memory_scope") or "session"),
                        "candidate_kind": str(memory.get("candidate_kind") or "promotion"),
                        "source_count": int(memory.get("source_count") or 0),
                        "memory_created_at": str(
                            memory.get("at") or memory.get("created_at") or ""
                        ),
                    },
                )

                if evidences:
                    session.run(
                        """
						UNWIND $evidences AS evidence
						MERGE (memory:KnowledgeMemory {id: $memory_id})
						MERGE (source:MemoryEvidence {id: evidence.id})
						SET source.name = evidence.name,
							source.bucket_id = evidence.bucket_id,
							source.memory_scope = evidence.memory_scope,
							source.updated_at = datetime()
						MERGE (memory)-[rel:DERIVED_FROM]->(source)
						SET rel.updated_at = datetime()
						""",
                        {
                            "memory_id": str(memory.get("id") or ""),
                            "evidences": [
                                {
                                    "id": str(item.get("id") or ""),
                                    "name": str(item.get("name") or item.get("id") or ""),
                                    "bucket_id": str(
                                        item.get("bucket_id") or memory.get("bucket_id") or ""
                                    ),
                                    "memory_scope": str(
                                        item.get("memory_scope")
                                        or memory.get("memory_scope")
                                        or "session"
                                    ),
                                }
                                for item in evidences
                                if isinstance(item, dict) and str(item.get("id") or "")
                            ],
                        },
                    )

                if topics:
                    session.run(
                        """
						UNWIND $topics AS topic
						MERGE (owner:KnowledgeOwner {id: $owner_id})
						MERGE (memory:KnowledgeMemory {id: $memory_id})
						MERGE (node:KnowledgeTopic {id: topic.id})
						SET node.name = topic.name,
							node.weight = topic.weight,
							node.updated_at = datetime()
						MERGE (memory)-[rel:MENTIONS_TOPIC]->(node)
						SET rel.weight = topic.weight,
							rel.updated_at = datetime()
						MERGE (owner)-[owner_rel:RELATES_TO_TOPIC]->(node)
						SET owner_rel.updated_at = datetime()
						""",
                        {
                            "owner_id": str(owner.get("id") or ""),
                            "memory_id": str(memory.get("id") or ""),
                            "topics": [
                                {
                                    "id": str(item.get("id") or ""),
                                    "name": str(item.get("name") or ""),
                                    "weight": int(item.get("weight") or 0),
                                }
                                for item in topics
                                if isinstance(item, dict) and str(item.get("id") or "")
                            ],
                        },
                    )
        except Exception:  # noqa: BLE001
            return

    def project_causal_assembly(self, *, projection: dict[str, Any]) -> bool:
        if not self.enabled or self._driver is None:
            return False

        assembly = (
            projection.get("assembly") if isinstance(projection.get("assembly"), dict) else {}
        )
        if not assembly:
            return False

        assembly_id = str(assembly.get("assembly_id") or assembly.get("id") or "").strip()
        if not assembly_id:
            return False
        assembly_graph_id = str(assembly.get("id") or f"causal-assembly:{assembly_id}").strip()

        columns = [item for item in projection.get("columns", []) if isinstance(item, dict)]
        belief_snapshots = [
            item for item in projection.get("belief_snapshots", []) if isinstance(item, dict)
        ]
        beliefs = [item for item in projection.get("beliefs", []) if isinstance(item, dict)]
        confidence_samples = [
            item for item in projection.get("confidence_samples", []) if isinstance(item, dict)
        ]
        outcomes = [item for item in projection.get("outcomes", []) if isinstance(item, dict)]
        support_edges = [
            item for item in projection.get("support_edges", []) if isinstance(item, dict)
        ]
        dissent_edges = [
            item for item in projection.get("dissent_edges", []) if isinstance(item, dict)
        ]

        try:
            with self._driver.session() as session:
                tx = session.begin_transaction()
                try:
                    tx.run(
                        """
                        MATCH (assembly:CausalAssembly {assembly_id: $assembly_id})
                        OPTIONAL MATCH path = (assembly)-[*0..4]->(node)
                        WITH [item IN collect(DISTINCT node) WHERE item IS NOT NULL] AS nodes
                        UNWIND nodes AS node
                        DETACH DELETE node
                        """,
                        {"assembly_id": assembly_id},
                    )

                    tx.run(
                        """
                        MERGE (assembly:CausalAssembly {assembly_id: $assembly_id})
                        SET assembly.id = $assembly_graph_id,
                            assembly.updated_at = $updated_at,
                            assembly.column_count = $column_count,
                            assembly.belief_snapshot_count = $belief_snapshot_count,
                            assembly.belief_count = $belief_count,
                            assembly.confidence_sample_count = $confidence_sample_count,
                            assembly.outcome_count = $outcome_count,
                            assembly.projected_at = datetime()
                        """,
                        {
                            "assembly_id": assembly_id,
                            "assembly_graph_id": assembly_graph_id,
                            "updated_at": float(assembly.get("updated_at") or 0.0),
                            "column_count": int(assembly.get("column_count") or 0),
                            "belief_snapshot_count": int(
                                assembly.get("belief_snapshot_count") or 0
                            ),
                            "belief_count": int(assembly.get("belief_count") or 0),
                            "confidence_sample_count": int(
                                assembly.get("confidence_sample_count") or 0
                            ),
                            "outcome_count": int(assembly.get("outcome_count") or 0),
                        },
                    )

                    if columns:
                        tx.run(
                            """
                        UNWIND $columns AS column
                        MERGE (assembly:CausalAssembly {assembly_id: $assembly_id})
                        MERGE (node:CausalColumn {id: column.id})
                        SET node.assembly_id = column.assembly_id,
                            node.column_id = column.column_id,
                            node.kind = column.kind,
                            node.confidence = column.confidence,
                            node.last_updated = column.last_updated,
                            node.evidence_refs = column.evidence_refs,
                            node.adaptation_metrics_json = column.adaptation_metrics_json,
                            node.belief_count = column.belief_count,
                            node.projected_at = datetime()
                        MERGE (assembly)-[rel:HAS_COLUMN]->(node)
                        SET rel.updated_at = datetime()
                        """,
                            {"assembly_id": assembly_id, "columns": columns},
                        )

                    if belief_snapshots:
                        tx.run(
                            """
                        UNWIND $belief_snapshots AS snapshot
                        MATCH (column:CausalColumn {id: snapshot.column_node_id})
                        MERGE (node:CausalBeliefSnapshot {id: snapshot.id})
                        SET node.assembly_id = snapshot.assembly_id,
                            node.column_id = snapshot.column_id,
                            node.recorded_at = snapshot.recorded_at,
                            node.confidence = snapshot.confidence,
                            node.evidence_refs = snapshot.evidence_refs,
                            node.cause_json = snapshot.cause_json,
                            node.belief_count = snapshot.belief_count,
                            node.projected_at = datetime()
                        MERGE (column)-[rel:HAS_BELIEF_SNAPSHOT]->(node)
                        SET rel.updated_at = datetime()
                        """,
                            {"belief_snapshots": belief_snapshots},
                        )

                    if beliefs:
                        tx.run(
                            """
                        UNWIND $beliefs AS belief
                        MATCH (snapshot:CausalBeliefSnapshot {id: belief.snapshot_id})
                        MERGE (node:CausalBelief {id: belief.id})
                        SET node.assembly_id = belief.assembly_id,
                            node.column_id = belief.column_id,
                            node.belief_key = belief.belief_key,
                            node.value_json = belief.value_json,
                            node.confidence = belief.confidence,
                            node.evidence_refs = belief.evidence_refs,
                            node.rationale = belief.rationale,
                            node.metadata_json = belief.metadata_json,
                            node.projected_at = datetime()
                        MERGE (snapshot)-[rel:HAS_BELIEF]->(node)
                        SET rel.updated_at = datetime()
                        """,
                            {"beliefs": beliefs},
                        )

                    if confidence_samples:
                        tx.run(
                            """
                        UNWIND $confidence_samples AS sample
                        MATCH (column:CausalColumn {id: sample.column_node_id})
                        MERGE (node:CausalConfidenceSample {id: sample.id})
                        SET node.assembly_id = sample.assembly_id,
                            node.column_id = sample.column_id,
                            node.recorded_at = sample.recorded_at,
                            node.confidence = sample.confidence,
                            node.adaptation_metrics_json = sample.adaptation_metrics_json,
                            node.cause_json = sample.cause_json,
                            node.projected_at = datetime()
                        MERGE (column)-[rel:HAS_CONFIDENCE_SAMPLE]->(node)
                        SET rel.updated_at = datetime()
                        """,
                            {"confidence_samples": confidence_samples},
                        )

                    if outcomes:
                        tx.run(
                            """
                        UNWIND $outcomes AS outcome
                        MERGE (assembly:CausalAssembly {assembly_id: $assembly_id})
                        MERGE (node:CausalOutcome {id: outcome.id})
                        SET node.assembly_id = outcome.assembly_id,
                            node.outcome = outcome.outcome,
                            node.recorded_at = outcome.recorded_at,
                            node.metadata_json = outcome.metadata_json,
                            node.decision = outcome.decision,
                            node.commitment_confidence = outcome.commitment_confidence,
                            node.is_ready = outcome.is_ready,
                            node.blockers = outcome.blockers,
                            node.next_actions = outcome.next_actions,
                            node.supporting_columns = outcome.supporting_columns,
                            node.dissenting_columns = outcome.dissenting_columns,
                            node.projected_at = datetime()
                        MERGE (assembly)-[rel:HAS_OUTCOME]->(node)
                        SET rel.updated_at = datetime()
                        """,
                            {"assembly_id": assembly_id, "outcomes": outcomes},
                        )

                    if support_edges:
                        tx.run(
                            """
                        UNWIND $support_edges AS edge
                        MATCH (outcome:CausalOutcome {id: edge.outcome_id})
                        MATCH (column:CausalColumn {id: edge.column_id})
                        MERGE (outcome)-[rel:SUPPORTED_BY]->(column)
                        SET rel.updated_at = datetime()
                        """,
                            {"support_edges": support_edges},
                        )

                    if dissent_edges:
                        tx.run(
                            """
                        UNWIND $dissent_edges AS edge
                        MATCH (outcome:CausalOutcome {id: edge.outcome_id})
                        MATCH (column:CausalColumn {id: edge.column_id})
                        MERGE (outcome)-[rel:DISSENTED_BY]->(column)
                        SET rel.updated_at = datetime()
                        """,
                            {"dissent_edges": dissent_edges},
                        )

                    tx.commit()
                except Exception:
                    tx.rollback()
                    raise
                return True
        except Exception:  # noqa: BLE001
            return False

    def query_memory_context(
        self,
        *,
        bucket_id: str,
        memory_scope: str,
        query_text: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        if not self.enabled or self._driver is None:
            return {"memories": [], "topics": [], "relations": []}

        owner_id = f"owner:{str(bucket_id or '').strip()}"
        bounded_limit = max(1, int(limit))
        query = str(query_text or "").strip().lower()
        memory_rows: list[dict[str, Any]] = []
        topic_rows: list[dict[str, Any]] = []

        try:
            with self._driver.session() as session:
                memory_result = session.run(
                    """
					MATCH (owner:KnowledgeOwner {id: $owner_id})-[:OWNS_MEMORY]->(memory:KnowledgeMemory)
					WHERE memory.memory_scope = $memory_scope
					AND ($query = '' OR toLower(memory.content) CONTAINS $query)
					RETURN memory.id AS id,
					memory.content AS content,
					memory.kind AS kind,
					memory.candidate_kind AS candidate_kind,
					memory.bucket_id AS bucket_id,
					memory.session_id AS session_id,
					memory.source_count AS source_count,
					memory.created_at AS created_at
					ORDER BY memory.created_at DESC
					LIMIT $limit
					""",
                    {
                        "owner_id": owner_id,
                        "memory_scope": str(memory_scope or "session"),
                        "query": query,
                        "limit": bounded_limit,
                    },
                )
                for row in memory_result:
                    memory_rows.append(
                        {
                            "id": str(row.get("id") or ""),
                            "content": str(row.get("content") or ""),
                            "kind": str(row.get("kind") or "memory-consolidation"),
                            "candidate_kind": str(row.get("candidate_kind") or "promotion"),
                            "bucket_id": str(row.get("bucket_id") or bucket_id),
                            "session_id": str(row.get("session_id") or bucket_id),
                            "source_count": int(row.get("source_count") or 0),
                            "tier": "world-graph",
                            "at": str(row.get("created_at") or ""),
                        }
                    )

                topic_result = session.run(
                    """
					MATCH (owner:KnowledgeOwner {id: $owner_id})-[:RELATES_TO_TOPIC]->(topic:KnowledgeTopic)
					RETURN topic.id AS id,
					topic.name AS name,
					topic.weight AS weight
					ORDER BY topic.weight DESC, topic.name ASC
					LIMIT $limit
					""",
                    {
                        "owner_id": owner_id,
                        "limit": bounded_limit,
                    },
                )
                for row in topic_result:
                    topic_rows.append(
                        {
                            "id": str(row.get("id") or ""),
                            "name": str(row.get("name") or ""),
                            "weight": int(row.get("weight") or 0),
                        }
                    )
        except Exception:  # noqa: BLE001
            return {"memories": [], "topics": [], "relations": []}

        relations = [
            {
                "type": "RELATES_TO_TOPIC",
                "from": owner_id,
                "to": str(topic.get("id") or ""),
            }
            for topic in topic_rows
            if str(topic.get("id") or "")
        ]
        return {
            "memories": memory_rows,
            "topics": topic_rows,
            "relations": relations,
        }


class PostgresWorldGraph(_BasePostgresService):
    """World-model graph hosted in the bundled Postgres (no Java / no Neo4j).

    Drop-in for :class:`Neo4jRunGraph` — same methods + return shapes — backed by
    two relational tables (``locus_kg_nodes`` / ``locus_kg_edges``) queried
    with plain SQL. Node labels and edge relation names mirror the Cypher model
    (KnowledgeOwner / KnowledgeMemory / KnowledgeTopic / MemoryEvidence /
    WorkflowRun / Agent / Workflow; OWNS_MEMORY / DERIVED_FROM / MENTIONS_TOPIC /
    RELATES_TO_TOPIC / EXECUTED_BY / PART_OF) so the projection + retrieval code is
    unchanged.
    """

    def _ensure(self) -> None:
        if self._initialized or not self.enabled:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            _rename_legacy_tables_postgres(cursor, ("locus_kg_nodes",))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS locus_kg_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT '',
                    props JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            _rename_legacy_tables_postgres(cursor, ("locus_kg_edges",))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS locus_kg_edges (
                    src TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    rel TEXT NOT NULL,
                    props JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (src, dst, rel)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS locus_kg_nodes_label_scope "
                "ON locus_kg_nodes(label, memory_scope)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS locus_kg_edges_src_rel "
                "ON locus_kg_edges(src, rel)"
            )
        self._initialized = True

    def _merge_node(
        self,
        cursor: Any,
        *,
        node_id: str,
        label: str,
        props: dict[str, Any],
        memory_scope: str = "",
    ) -> None:
        if not node_id:
            return
        cursor.execute(
            """
            INSERT INTO locus_kg_nodes (id, label, memory_scope, props)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                label = EXCLUDED.label,
                memory_scope = EXCLUDED.memory_scope,
                props = locus_kg_nodes.props || EXCLUDED.props,
                updated_at = now()
            """,
            (node_id, label, memory_scope, json.dumps(props, default=_json_default)),
        )

    def _merge_edge(
        self, cursor: Any, *, src: str, dst: str, rel: str, props: dict[str, Any] | None = None
    ) -> None:
        if not src or not dst:
            return
        cursor.execute(
            """
            INSERT INTO locus_kg_edges (src, dst, rel, props)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (src, dst, rel) DO UPDATE SET
                props = locus_kg_edges.props || EXCLUDED.props,
                updated_at = now()
            """,
            (src, dst, rel, json.dumps(props or {}, default=_json_default)),
        )

    def record_run(
        self, *, run_id: str, title: str, agent: str | None, workflow: str | None
    ) -> None:
        if not self.enabled:
            return
        try:
            self._ensure()
            with self._connect() as connection, connection.cursor() as cursor:
                self._merge_node(
                    cursor, node_id=run_id, label="WorkflowRun", props={"title": title}
                )
                if agent:
                    self._merge_node(
                        cursor, node_id=f"agent:{agent}", label="Agent", props={"name": agent}
                    )
                    self._merge_edge(cursor, src=run_id, dst=f"agent:{agent}", rel="EXECUTED_BY")
                if workflow:
                    self._merge_node(
                        cursor,
                        node_id=f"workflow:{workflow}",
                        label="Workflow",
                        props={"name": workflow},
                    )
                    self._merge_edge(cursor, src=run_id, dst=f"workflow:{workflow}", rel="PART_OF")
        except Exception:  # noqa: BLE001
            return

    def project_memory_summary(self, *, projection: dict[str, Any]) -> None:
        if not self.enabled:
            return
        owner = projection.get("owner") if isinstance(projection.get("owner"), dict) else {}
        memory = projection.get("memory") if isinstance(projection.get("memory"), dict) else {}
        topics = projection.get("topics") if isinstance(projection.get("topics"), list) else []
        evidences = (
            projection.get("evidences") if isinstance(projection.get("evidences"), list) else []
        )
        if not owner or not memory:
            return
        owner_id = str(owner.get("id") or "")
        memory_id = str(memory.get("id") or "")
        scope = str(memory.get("memory_scope") or "session")
        try:
            self._ensure()
            with self._connect() as connection, connection.cursor() as cursor:
                self._merge_node(
                    cursor,
                    node_id=owner_id,
                    label="KnowledgeOwner",
                    memory_scope=str(owner.get("memory_scope") or scope),
                    props={
                        "name": str(owner.get("name") or ""),
                        "owner_type": str(owner.get("type") or "Owner"),
                        "memory_scope": str(owner.get("memory_scope") or scope),
                    },
                )
                self._merge_node(
                    cursor,
                    node_id=memory_id,
                    label="KnowledgeMemory",
                    memory_scope=scope,
                    props={
                        "content": str(memory.get("content") or ""),
                        "kind": str(memory.get("kind") or "memory-consolidation"),
                        "bucket_id": str(memory.get("bucket_id") or ""),
                        "session_id": str(memory.get("session_id") or ""),
                        "memory_scope": scope,
                        "candidate_kind": str(memory.get("candidate_kind") or "promotion"),
                        "source_count": int(memory.get("source_count") or 0),
                        "created_at": str(memory.get("at") or memory.get("created_at") or ""),
                    },
                )
                self._merge_edge(
                    cursor,
                    src=owner_id,
                    dst=memory_id,
                    rel="OWNS_MEMORY",
                    props={"memory_scope": scope},
                )
                for item in evidences:
                    if not isinstance(item, dict) or not str(item.get("id") or ""):
                        continue
                    ev_id = str(item.get("id"))
                    self._merge_node(
                        cursor,
                        node_id=ev_id,
                        label="MemoryEvidence",
                        memory_scope=scope,
                        props={
                            "name": str(item.get("name") or item.get("id") or ""),
                            "bucket_id": str(
                                item.get("bucket_id") or memory.get("bucket_id") or ""
                            ),
                            "memory_scope": str(item.get("memory_scope") or scope),
                        },
                    )
                    self._merge_edge(cursor, src=memory_id, dst=ev_id, rel="DERIVED_FROM")
                for item in topics:
                    if not isinstance(item, dict) or not str(item.get("id") or ""):
                        continue
                    t_id = str(item.get("id"))
                    weight = int(item.get("weight") or 0)
                    self._merge_node(
                        cursor,
                        node_id=t_id,
                        label="KnowledgeTopic",
                        props={"name": str(item.get("name") or ""), "weight": weight},
                    )
                    self._merge_edge(
                        cursor,
                        src=memory_id,
                        dst=t_id,
                        rel="MENTIONS_TOPIC",
                        props={"weight": weight},
                    )
                    self._merge_edge(cursor, src=owner_id, dst=t_id, rel="RELATES_TO_TOPIC")
        except Exception:  # noqa: BLE001
            return

    _CAUSAL_LABELS = (
        "CausalAssembly",
        "CausalColumn",
        "CausalBeliefSnapshot",
        "CausalBelief",
        "CausalConfidenceSample",
        "CausalOutcome",
    )

    def project_causal_assembly(self, *, projection: dict[str, Any]) -> bool:
        """Relational mirror of :meth:`Neo4jRunGraph.project_causal_assembly`.

        Replaces the assembly's previous projection (same labels / relation names as
        the Cypher model) inside one transaction; returns False on any failure.
        """
        if not self.enabled:
            return False
        assembly = (
            projection.get("assembly") if isinstance(projection.get("assembly"), dict) else {}
        )
        if not assembly:
            return False
        assembly_id = str(assembly.get("assembly_id") or assembly.get("id") or "").strip()
        if not assembly_id:
            return False
        assembly_node_id = f"causal-assembly-node:{assembly_id}"

        def _items(key: str) -> list[dict[str, Any]]:
            raw = projection.get(key)
            return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

        def _props(item: dict[str, Any], *exclude: str) -> dict[str, Any]:
            return {k: v for k, v in item.items() if k not in exclude}

        try:
            self._ensure()
            with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM locus_kg_edges
                    WHERE src IN (
                        SELECT id FROM locus_kg_nodes
                        WHERE label = ANY(%s) AND props->>'assembly_id' = %s
                    ) OR dst IN (
                        SELECT id FROM locus_kg_nodes
                        WHERE label = ANY(%s) AND props->>'assembly_id' = %s
                    )
                    """,
                    (list(self._CAUSAL_LABELS), assembly_id, list(self._CAUSAL_LABELS), assembly_id),
                )
                cursor.execute(
                    "DELETE FROM locus_kg_nodes WHERE label = ANY(%s) AND props->>'assembly_id' = %s",
                    (list(self._CAUSAL_LABELS), assembly_id),
                )
                self._merge_node(
                    cursor,
                    node_id=assembly_node_id,
                    label="CausalAssembly",
                    props={
                        "assembly_id": assembly_id,
                        "id": str(assembly.get("id") or f"causal-assembly:{assembly_id}"),
                        "updated_at": float(assembly.get("updated_at") or 0.0),
                        "column_count": int(assembly.get("column_count") or 0),
                        "belief_snapshot_count": int(assembly.get("belief_snapshot_count") or 0),
                        "belief_count": int(assembly.get("belief_count") or 0),
                        "confidence_sample_count": int(
                            assembly.get("confidence_sample_count") or 0
                        ),
                        "outcome_count": int(assembly.get("outcome_count") or 0),
                    },
                )
                for column in _items("columns"):
                    node_id = str(column.get("id") or "")
                    self._merge_node(
                        cursor,
                        node_id=node_id,
                        label="CausalColumn",
                        props={**_props(column), "assembly_id": assembly_id},
                    )
                    self._merge_edge(cursor, src=assembly_node_id, dst=node_id, rel="HAS_COLUMN")
                for snapshot in _items("belief_snapshots"):
                    node_id = str(snapshot.get("id") or "")
                    self._merge_node(
                        cursor,
                        node_id=node_id,
                        label="CausalBeliefSnapshot",
                        props={**_props(snapshot, "column_node_id"), "assembly_id": assembly_id},
                    )
                    self._merge_edge(
                        cursor,
                        src=str(snapshot.get("column_node_id") or ""),
                        dst=node_id,
                        rel="HAS_BELIEF_SNAPSHOT",
                    )
                for belief in _items("beliefs"):
                    node_id = str(belief.get("id") or "")
                    self._merge_node(
                        cursor,
                        node_id=node_id,
                        label="CausalBelief",
                        props={**_props(belief, "snapshot_id"), "assembly_id": assembly_id},
                    )
                    self._merge_edge(
                        cursor,
                        src=str(belief.get("snapshot_id") or ""),
                        dst=node_id,
                        rel="HAS_BELIEF",
                    )
                for sample in _items("confidence_samples"):
                    node_id = str(sample.get("id") or "")
                    self._merge_node(
                        cursor,
                        node_id=node_id,
                        label="CausalConfidenceSample",
                        props={**_props(sample, "column_node_id"), "assembly_id": assembly_id},
                    )
                    self._merge_edge(
                        cursor,
                        src=str(sample.get("column_node_id") or ""),
                        dst=node_id,
                        rel="HAS_CONFIDENCE_SAMPLE",
                    )
                for outcome in _items("outcomes"):
                    node_id = str(outcome.get("id") or "")
                    self._merge_node(
                        cursor,
                        node_id=node_id,
                        label="CausalOutcome",
                        props={**_props(outcome), "assembly_id": assembly_id},
                    )
                    self._merge_edge(cursor, src=assembly_node_id, dst=node_id, rel="HAS_OUTCOME")
                for rel, key in (("SUPPORTED_BY", "support_edges"), ("DISSENTED_BY", "dissent_edges")):
                    for edge in _items(key):
                        self._merge_edge(
                            cursor,
                            src=str(edge.get("outcome_id") or ""),
                            dst=str(edge.get("column_id") or ""),
                            rel=rel,
                        )
            return True
        except Exception:  # noqa: BLE001
            return False

    def query_memory_context(
        self, *, bucket_id: str, memory_scope: str, query_text: str = "", limit: int = 10
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"memories": [], "topics": [], "relations": []}
        owner_id = f"owner:{str(bucket_id or '').strip()}"
        bounded = max(1, int(limit))
        query = str(query_text or "").strip().lower()
        memory_rows: list[dict[str, Any]] = []
        topic_rows: list[dict[str, Any]] = []
        try:
            self._ensure()
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT n.id, n.props
                    FROM locus_kg_edges e
                    JOIN locus_kg_nodes n ON n.id = e.dst
                    WHERE e.src = %s AND e.rel = 'OWNS_MEMORY'
                      AND n.label = 'KnowledgeMemory'
                      AND n.props->>'memory_scope' = %s
                      AND (%s = '' OR lower(n.props->>'content') LIKE %s)
                    ORDER BY n.props->>'created_at' DESC
                    LIMIT %s
                    """,
                    (owner_id, str(memory_scope or "session"), query, f"%{query}%", bounded),
                )
                for node_id, props in cursor.fetchall():
                    props = _safe_json_loads(props) or {}
                    memory_rows.append(
                        {
                            "id": str(node_id or ""),
                            "content": str(props.get("content") or ""),
                            "kind": str(props.get("kind") or "memory-consolidation"),
                            "candidate_kind": str(props.get("candidate_kind") or "promotion"),
                            "bucket_id": str(props.get("bucket_id") or bucket_id),
                            "session_id": str(props.get("session_id") or bucket_id),
                            "source_count": int(props.get("source_count") or 0),
                            "tier": "world-graph",
                            "at": str(props.get("created_at") or ""),
                        }
                    )
                cursor.execute(
                    """
                    SELECT n.id, n.props
                    FROM locus_kg_edges e
                    JOIN locus_kg_nodes n ON n.id = e.dst
                    WHERE e.src = %s AND e.rel = 'RELATES_TO_TOPIC'
                      AND n.label = 'KnowledgeTopic'
                    ORDER BY (n.props->>'weight')::int DESC NULLS LAST, n.props->>'name' ASC
                    LIMIT %s
                    """,
                    (owner_id, bounded),
                )
                for node_id, props in cursor.fetchall():
                    props = _safe_json_loads(props) or {}
                    topic_rows.append(
                        {
                            "id": str(node_id or ""),
                            "name": str(props.get("name") or ""),
                            "weight": int(props.get("weight") or 0),
                        }
                    )
        except Exception:  # noqa: BLE001
            return {"memories": [], "topics": [], "relations": []}
        relations = [
            {"type": "RELATES_TO_TOPIC", "from": owner_id, "to": str(t.get("id") or "")}
            for t in topic_rows
            if str(t.get("id") or "")
        ]
        return {"memories": memory_rows, "topics": topic_rows, "relations": relations}
