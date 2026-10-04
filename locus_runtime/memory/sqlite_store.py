"""Embedded long-term memory: SQLite + FTS5 + sqlite-vec (LOCUS-387).

The desktop / local profile's default :class:`~locus_runtime.memory.contract.LongTermMemoryStore`.
One SQLite file under the app home, owned by the memory module (D-28 rule 4), with
no server to run:

* ``locus_long_term_memory`` -- entries (content, metadata, optional embedding as a
  little-endian float32 blob plus its model and dimensions, embedding status).
* ``locus_long_term_memory_fts`` -- an FTS5 external-content index over ``content``,
  kept in sync by triggers: keyword search that works with no embedding model.
* ``locus_memory_consolidation_queue`` -- the same promotion queue as the Postgres store.
* ``locus_memory_collections`` -- the collections this store holds (the default
  ``Personal`` collection is registered at first run, see :mod:`.bootstrap`).

Search is hybrid: FTS5 (BM25) keyword hits and, when an embedding model answers,
cosine-nearest semantic hits (sqlite-vec's ``vec_distance_cosine``; a pure-Python
fallback when the extension cannot load), fused by reciprocal rank. Entries are
written without waiting for an embedding (status ``pending``);
:meth:`SQLiteLongTermMemoryStore.backfill_embeddings` embeds them later, so a
missing embedding model degrades semantic search only and never turns memory off.

Security: SQL is parameterized throughout (the only interpolated identifiers are
module constants). The file is created owner-only (0600 in a 0700 directory it
creates; on Windows it inherits the per-user app-home ACL). ``secure_delete`` is on
so forgotten content is overwritten. Content, queries and metadata are never logged.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
import sqlite3
import stat
import struct
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from locus_runtime.memory.contract import (
    MAX_CONTENT_CHARS,
    MAX_IDENTIFIER_CHARS,
    MAX_QUERY_CHARS,
    MAX_RESULTS,
    PORT_VERSION,
    Embedder,
    EmbedderStatus,
    EmbeddingUnavailable,
)

logger = logging.getLogger(__name__)

#: Schema version of this store (``PRAGMA user_version``). Bump with a migration step.
SCHEMA_VERSION = 1
#: Metadata stored with an entry is capped; larger metadata keeps scalar fields only.
MAX_METADATA_BYTES = 64 * 1024
#: Embedding dimensions accepted from an embedder.
MIN_DIMENSIONS = 8
MAX_DIMENSIONS = 4096
#: Without sqlite-vec, the Python fallback scores at most this many recent entries.
PYTHON_VECTOR_SCAN_LIMIT = 5000
#: Reciprocal-rank-fusion constant (Cormack et al. 2009).
RRF_K = 60
#: Fixed text used to check that the embedding model answers (no user content).
EMBEDDING_PROBE_TEXT = "locus memory embedding probe"

_ENTRY_COLUMNS = (
    "id, bucket_id, session_id, memory_scope, source, task_id, content, metadata, created_at"
)
_CANDIDATE_COLUMNS = (
    "id, entry_id, bucket_id, session_id, memory_scope, source, task_id, "
    "candidate_kind, status, created_at, updated_at, content, metadata"
)
_FILTERS = (
    "(? IS NULL OR m.bucket_id = ?) AND (? IS NULL OR m.session_id = ?) "
    "AND (? IS NULL OR m.memory_scope = ?)"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS locus_memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locus_long_term_memory (
    id TEXT PRIMARY KEY,
    bucket_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    memory_scope TEXT NOT NULL,
    source TEXT NOT NULL,
    task_id TEXT,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    embedding BLOB,
    embedding_model TEXT,
    embedding_dimensions INTEGER,
    embedding_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS locus_ltm_bucket_idx
    ON locus_long_term_memory (bucket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS locus_ltm_session_idx
    ON locus_long_term_memory (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS locus_ltm_scope_idx
    ON locus_long_term_memory (memory_scope, created_at DESC);
CREATE INDEX IF NOT EXISTS locus_ltm_embedding_idx
    ON locus_long_term_memory (embedding_status, created_at DESC);
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
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS locus_mcq_status_idx
    ON locus_memory_consolidation_queue (status, created_at DESC);
CREATE INDEX IF NOT EXISTS locus_mcq_bucket_idx
    ON locus_memory_consolidation_queue (bucket_id, created_at DESC);
CREATE TABLE IF NOT EXISTS locus_memory_collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS locus_long_term_memory_fts USING fts5(
    content,
    content='locus_long_term_memory',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS locus_ltm_fts_insert AFTER INSERT ON locus_long_term_memory BEGIN
    INSERT INTO locus_long_term_memory_fts (rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS locus_ltm_fts_delete AFTER DELETE ON locus_long_term_memory BEGIN
    INSERT INTO locus_long_term_memory_fts (locus_long_term_memory_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS locus_ltm_fts_update AFTER UPDATE OF content
ON locus_long_term_memory BEGIN
    INSERT INTO locus_long_term_memory_fts (locus_long_term_memory_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO locus_long_term_memory_fts (rowid, content) VALUES (new.rowid, new.content);
END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identifier(value: Any, *, field: str, allow_empty: bool = False) -> str:
    text = str(value if value is not None else "").strip()
    if not text and not allow_empty:
        raise ValueError(f"memory {field} is required")
    if len(text) > MAX_IDENTIFIER_CHARS:
        raise ValueError(f"memory {field} exceeds {MAX_IDENTIFIER_CHARS} characters")
    return text


def _optional_filter(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, field=field, allow_empty=True)


def _bounded_limit(limit: int, *, default: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_RESULTS))


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _encode_metadata(entry: dict[str, Any]) -> str:
    """Entry metadata, without the content (stored once, in its own column), capped."""
    metadata = {key: value for key, value in entry.items() if key != "content"}
    encoded = json.dumps(metadata, default=_json_default)
    if len(encoded.encode("utf-8")) <= MAX_METADATA_BYTES:
        return encoded
    scalars = {
        key: value
        for key, value in metadata.items()
        if isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 1000)
    }
    scalars["metadata_truncated"] = True
    encoded = json.dumps(scalars, default=_json_default)
    if len(encoded.encode("utf-8")) <= MAX_METADATA_BYTES:
        return encoded
    return json.dumps({"metadata_truncated": True})


def _decode_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def _valid_vector(vector: Any) -> list[float] | None:
    if not isinstance(vector, list) or not (MIN_DIMENSIONS <= len(vector) <= MAX_DIMENSIONS):
        return None
    try:
        values = [float(item) for item in vector]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in values):
        return None
    return values


def _cosine_distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm == 0.0:
        return 1.0
    return 1.0 - dot / norm


def _fts_query(text: str) -> str:
    """An FTS5 MATCH expression of quoted terms (OR); user text never becomes syntax."""
    terms: list[str] = []
    for term in re.findall(r"\w+", text.lower()):
        if len(term) >= 2 and term not in terms:
            terms.append(term)
        if len(terms) >= 16:
            break
    return " OR ".join(f'"{term}"' for term in terms)


def _like_terms(text: str) -> list[str]:
    terms = [term for term in re.findall(r"\w+", text.lower()) if len(term) >= 3][:12]
    escaped = [term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in terms]
    return [f"%{term}%" for term in escaped]


def load_vector_extension(connection: sqlite3.Connection) -> str:
    """Load sqlite-vec into ``connection``; its version, or ``""`` when unavailable.

    Extension loading is switched on only for the load call itself.
    """
    try:
        module = importlib.import_module("sqlite_vec")
    except ImportError:
        return ""
    try:
        connection.enable_load_extension(True)
        try:
            module.load(connection)
        finally:
            connection.enable_load_extension(False)
        row = connection.execute("SELECT vec_version()").fetchone()
    except (AttributeError, OSError, sqlite3.Error) as exc:
        logger.warning("memory.sqlite_vec_unavailable error=%s", type(exc).__name__)
        return ""
    return str(row[0]) if row else ""


def prepare_private_file(path: Path) -> None:
    """Create ``path`` (and its directory, if missing) readable by the owner only.

    POSIX: the directory is created 0700 when this call creates it (an existing
    directory is left alone) and the file is 0600. Windows: both inherit the
    per-user ACL of the app home (``%LOCALAPPDATA%``), like the other app-home data.
    A symlink in place of the file is refused.
    """
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            parent.chmod(stat.S_IRWXU)
    if path.is_symlink():
        raise OSError(f"refusing to open a memory store through a symlink: {path.name}")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


class SQLiteLongTermMemoryStore:
    """:class:`~locus_runtime.memory.contract.LongTermMemoryStore` on one SQLite file."""

    store_kind = "sqlite"
    port_version = PORT_VERSION
    #: Keyword search answers with no embedding model (FTS5, or LIKE without FTS5).
    keyword_search_enabled = True

    def __init__(
        self,
        path: str,
        *,
        embedder: Embedder | None = None,
        load_extension: bool = False,
        on_pending: Callable[[], None] | None = None,
    ) -> None:
        self.path = str(path or "").strip()
        self.enabled = bool(self.path)
        self.embedder = embedder
        self.on_pending = on_pending
        self._load_extension = load_extension
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._initialized = False
        self.fts_enabled = False
        self.vector_extension_version = ""

    @property
    def backend_label(self) -> str:
        """What actually serves search: sqlite-vec only when it was loaded."""
        vectors = "sqlite-vec" if self.vector_extension_version else "Python vectors"
        return f"SQLite (FTS5 + {vectors})"

    # -- identity / status --------------------------------------------------------
    @property
    def embedding_model(self) -> str:
        return self.embedder.model if self.embedder is not None else ""

    def semantic_status(self) -> EmbedderStatus:
        if self.embedder is None:
            return EmbedderStatus(state="disabled", model="", reason="no embedder configured")
        return self.embedder.status()

    @property
    def vector_enabled(self) -> bool:
        """Semantic search can run now (an embedding model has answered)."""
        return self.enabled and self.semantic_status().ready

    def status(self) -> tuple[str, str]:
        if not self.enabled:
            return "disabled", "no memory store path configured"
        if self.healthcheck():
            return "connected", ""
        return "degraded", "the SQLite memory store could not be opened"

    def describe(self) -> dict[str, Any]:
        """Status facts for the memory endpoints (no content, no file path)."""
        semantic = self.semantic_status()
        pending = 0
        if self.enabled:
            try:
                pending = self.pending_embeddings()
            except sqlite3.Error:
                pending = 0
        return {
            "store": self.store_kind,
            "port_version": self.port_version,
            "keyword_search": "fts5" if self.fts_enabled else "like",
            "vector_extension": self.vector_extension_version or "unavailable",
            "semantic_search": semantic.state,
            "semantic_search_reason": semantic.reason,
            "embedding_model": semantic.model,
            "pending_embeddings": pending,
        }

    # -- connection -----------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError("SQLite memory store is not enabled")
        if self._connection is None:
            db_path = Path(self.path)
            prepare_private_file(db_path)
            connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if self._load_extension:
                self.vector_extension_version = load_vector_extension(connection)
            self._connection = connection
        return self._connection

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
                self._initialized = False

    def healthcheck(self) -> bool:
        if not self.enabled:
            return False
        try:
            self.initialize()
            with self._db() as connection:
                connection.execute("SELECT 1").fetchone()
        except (OSError, sqlite3.Error, RuntimeError):
            return False
        return True

    def initialize(self) -> None:
        """Create or migrate the schema. Idempotent; safe on every start."""
        if not self.enabled or self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            connection = self._connect()
            connection.executescript(_SCHEMA)
            try:
                connection.executescript(_FTS_SCHEMA)
                self.fts_enabled = True
            except sqlite3.OperationalError as exc:
                # FTS5 missing from this SQLite build: keyword search uses LIKE.
                logger.warning("memory.fts5_unavailable error=%s", type(exc).__name__)
                self.fts_enabled = False
            connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
            connection.execute(
                "INSERT INTO locus_memory_meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
            self._initialized = True

    # -- rows -----------------------------------------------------------------------
    @staticmethod
    def _row_to_entry(row: tuple[Any, ...]) -> dict[str, Any]:
        metadata = _decode_json_object(row[7])
        payload = dict(metadata)
        payload.update(
            {
                "id": str(row[0]),
                "bucket_id": str(row[1]),
                "session_id": str(row[2]),
                "memory_scope": str(row[3]),
                "source": str(row[4]),
                "task_id": str(row[5] or ""),
                "content": str(row[6]),
                "metadata": metadata,
                "at": str(row[8] or ""),
                "tier": "long-term",
            }
        )
        return payload

    @staticmethod
    def _row_to_candidate(row: tuple[Any, ...]) -> dict[str, Any]:
        metadata = _decode_json_object(row[12])
        payload = dict(metadata)
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
                "metadata": metadata,
                "created_at": str(row[9] or ""),
                "updated_at": str(row[10] or ""),
            }
        )
        return payload

    @staticmethod
    def _filter_params(
        bucket_id: str | None, session_id: str | None, memory_scope: str | None
    ) -> tuple[str | None, ...]:
        bucket = _optional_filter(bucket_id, field="bucket_id")
        session = _optional_filter(session_id, field="session_id")
        scope = _optional_filter(memory_scope, field="memory_scope")
        return (bucket, bucket, session, session, scope, scope)

    # -- entries --------------------------------------------------------------------
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
        params = self._filter_params(bucket_id, session_id, memory_scope)
        with self._db() as connection:
            rows = connection.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM locus_long_term_memory m WHERE {_FILTERS} "
                "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
                (*params, _bounded_limit(limit, default=100)),
            ).fetchall()
        return [self._row_to_entry(row) for row in reversed(rows)]

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
        if not isinstance(entry, dict):
            entry = {"raw": entry}
        content = str(entry.get("content") or json.dumps(entry, default=_json_default))
        content = content[:MAX_CONTENT_CHARS]
        entry_id = _identifier(entry.get("id") or uuid4(), field="id")
        values = (
            entry_id,
            _identifier(bucket_id, field="bucket_id"),
            _identifier(session_id, field="session_id"),
            _identifier(memory_scope, field="memory_scope"),
            _identifier(source, field="source"),
            _identifier(task_id, field="task_id", allow_empty=True) or None,
            content,
            _encode_metadata(entry),
        )
        now = _now()
        with self._db() as connection:
            # A changed content invalidates its embedding (re-embedded by the backfill).
            connection.execute(
                """
                INSERT INTO locus_long_term_memory (
                    id, bucket_id, session_id, memory_scope, source, task_id, content,
                    metadata, embedding_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    bucket_id = excluded.bucket_id,
                    session_id = excluded.session_id,
                    memory_scope = excluded.memory_scope,
                    source = excluded.source,
                    task_id = excluded.task_id,
                    metadata = excluded.metadata,
                    embedding = CASE WHEN locus_long_term_memory.content = excluded.content
                        THEN locus_long_term_memory.embedding ELSE NULL END,
                    embedding_model = CASE WHEN locus_long_term_memory.content = excluded.content
                        THEN locus_long_term_memory.embedding_model ELSE NULL END,
                    embedding_dimensions = CASE
                        WHEN locus_long_term_memory.content = excluded.content
                        THEN locus_long_term_memory.embedding_dimensions ELSE NULL END,
                    embedding_status = CASE WHEN locus_long_term_memory.content = excluded.content
                        THEN locus_long_term_memory.embedding_status ELSE 'pending' END,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (*values, now, now),
            )
        self._notify_pending()

    def clear_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        if not any((bucket_id, session_id, memory_scope)):
            return  # never an unfiltered delete
        self.initialize()
        params = self._filter_params(bucket_id, session_id, memory_scope)
        with self._db() as connection:
            connection.execute(f"DELETE FROM locus_long_term_memory AS m WHERE {_FILTERS}", params)

    # -- search ---------------------------------------------------------------------
    def _keyword_hits(
        self, connection: sqlite3.Connection, query: str, params: tuple[str | None, ...], limit: int
    ) -> list[tuple[Any, ...]]:
        if self.fts_enabled:
            expression = _fts_query(query)
            if not expression:
                return []
            rows: list[tuple[Any, ...]] = connection.execute(
                f"SELECT {', '.join('m.' + c.strip() for c in _ENTRY_COLUMNS.split(','))} "
                "FROM locus_long_term_memory_fts f "
                "JOIN locus_long_term_memory m ON m.rowid = f.rowid "
                f"WHERE locus_long_term_memory_fts MATCH ? AND {_FILTERS} "
                "ORDER BY bm25(locus_long_term_memory_fts), m.created_at DESC LIMIT ?",
                (expression, *params, limit),
            ).fetchall()
            return rows
        terms = _like_terms(query)
        if not terms:
            return []
        match = " OR ".join(["m.content LIKE ? ESCAPE '\\'"] * len(terms))
        score = " + ".join(["(m.content LIKE ? ESCAPE '\\')"] * len(terms))
        rows = connection.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM locus_long_term_memory m "
            f"WHERE {_FILTERS} AND ({match}) "
            f"ORDER BY ({score}) DESC, m.created_at DESC LIMIT ?",
            (*params, *terms, *terms, limit),
        ).fetchall()
        return rows

    def _embed_one(self, text: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            vectors = self.embedder.embed([text])
        except EmbeddingUnavailable:
            return None
        return _valid_vector(vectors[0]) if vectors else None

    def _semantic_hits(
        self,
        connection: sqlite3.Connection,
        vector: list[float],
        params: tuple[str | None, ...],
        limit: int,
    ) -> list[tuple[tuple[Any, ...], float]]:
        """``(row, cosine distance)`` nearest first, for entries embedded by this model."""
        model = self.embedding_model
        if self.vector_extension_version:
            rows = connection.execute(
                f"SELECT {_ENTRY_COLUMNS}, vec_distance_cosine(m.embedding, ?) AS distance "
                "FROM locus_long_term_memory m "
                "WHERE m.embedding IS NOT NULL AND m.embedding_model = ? "
                f"AND m.embedding_dimensions = ? AND {_FILTERS} "
                "ORDER BY distance ASC, m.created_at DESC LIMIT ?",
                (_pack_vector(vector), model, len(vector), *params, limit),
            ).fetchall()
            return [(tuple(row[:9]), float(row[9])) for row in rows]
        rows = connection.execute(
            f"SELECT {_ENTRY_COLUMNS}, m.embedding FROM locus_long_term_memory m "
            "WHERE m.embedding IS NOT NULL AND m.embedding_model = ? "
            f"AND m.embedding_dimensions = ? AND {_FILTERS} "
            "ORDER BY m.created_at DESC LIMIT ?",
            (model, len(vector), *params, PYTHON_VECTOR_SCAN_LIMIT),
        ).fetchall()
        scored = [
            (tuple(row[:9]), _cosine_distance(vector, _unpack_vector(bytes(row[9]))))
            for row in rows
        ]
        scored.sort(key=lambda item: item[1])
        return scored[:limit]

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
        query = str(query_text or "").strip()[:MAX_QUERY_CHARS]
        if not query:
            return self.get_entries(
                bucket_id=bucket_id, session_id=session_id, memory_scope=memory_scope, limit=limit
            )
        self.initialize()
        bounded = _bounded_limit(limit, default=10)
        candidates = min(MAX_RESULTS, max(bounded * 4, 50))
        params = self._filter_params(bucket_id, session_id, memory_scope)
        # Embed the query outside the database lock (a network call to the engine).
        vector = self._embed_one(query)
        with self._db() as connection:
            keyword = self._keyword_hits(connection, query, params, candidates)
            semantic = self._semantic_hits(connection, vector, params, candidates) if vector else []
        return self._fuse(keyword, semantic, bounded)

    def _fuse(
        self,
        keyword: list[tuple[Any, ...]],
        semantic: list[tuple[tuple[Any, ...], float]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Reciprocal-rank fusion of keyword and semantic hits."""
        scores: dict[str, float] = {}
        rows: dict[str, tuple[Any, ...]] = {}
        sources: dict[str, set[str]] = {}
        similarity: dict[str, float] = {}
        for rank, row in enumerate(keyword):
            key = str(row[0])
            rows[key] = row
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(key, set()).add("keyword")
        for rank, (row, distance) in enumerate(semantic):
            key = str(row[0])
            rows.setdefault(key, row)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(key, set()).add("semantic")
            similarity[key] = 1.0 - distance
        ordered = sorted(scores, key=lambda key: scores[key], reverse=True)[:limit]
        results: list[dict[str, Any]] = []
        for key in ordered:
            entry = self._row_to_entry(rows[key])
            entry["score"] = round(scores[key], 6)
            found = sources[key]
            entry["match"] = "hybrid" if len(found) == 2 else next(iter(found))
            if key in similarity:
                entry["similarity"] = round(similarity[key], 6)
            results.append(entry)
        return results

    def find_similar_entries(
        self,
        text: str,
        *,
        bucket_id: str | None = None,
        memory_scope: str | None = None,
        threshold: float = 0.92,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        normalized = str(text or "").strip()[:MAX_CONTENT_CHARS]
        if not normalized:
            return []
        self.initialize()
        vector = self._embed_one(normalized)
        if vector is None:
            return []
        params = self._filter_params(bucket_id, None, memory_scope)
        with self._db() as connection:
            hits = self._semantic_hits(connection, vector, params, _bounded_limit(limit, default=5))
        results: list[dict[str, Any]] = []
        for row, distance in hits:
            similarity = 1.0 - distance
            if similarity > float(threshold):
                entry = self._row_to_entry(row)
                entry["similarity"] = similarity
                results.append(entry)
        return results

    # -- embeddings backfill ----------------------------------------------------------
    def _notify_pending(self) -> None:
        if self.on_pending is None:
            return
        try:
            self.on_pending()
        except Exception:  # noqa: BLE001 - a wake-up hook must never fail a write
            logger.exception("memory.backfill_notify_error")

    def pending_embeddings(self) -> int:
        """Entries not yet embedded (or failed) by the current model.

        Embedding (or failing) an entry records the model, and a content change
        clears it, so "pending" is simply "``embedding_model`` is not the current one".
        """
        if not self.enabled:
            return 0
        self.initialize()
        with self._db() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM locus_long_term_memory WHERE embedding_model IS NOT ?",
                (self.embedding_model,),
            ).fetchone()
        return int(row[0]) if row else 0

    def backfill_embeddings(self, *, batch_size: int = 32) -> int:
        """Embed up to ``batch_size`` pending entries (newest first); returns how many.

        Returns 0 without raising when no embedding model answers; the entries stay
        pending and keyword search keeps serving them.
        """
        if not self.enabled or self.embedder is None or not self.embedding_model:
            return 0
        self.initialize()
        model = self.embedding_model
        with self._db() as connection:
            rows = connection.execute(
                "SELECT id, content FROM locus_long_term_memory WHERE embedding_model IS NOT ? "
                "ORDER BY created_at DESC LIMIT ?",
                (model, max(1, min(int(batch_size), MAX_RESULTS))),
            ).fetchall()
        if not rows:
            if self.embedder.status().state == "pending_embedding_model":
                # Nothing to embed: probe with a fixed text (never user content) so
                # the status reports semantic search ready as soon as a model answers.
                try:
                    self.embedder.embed([EMBEDDING_PROBE_TEXT])
                except EmbeddingUnavailable:
                    pass
            return 0
        try:
            vectors = self.embedder.embed([str(row[1]) for row in rows])
        except EmbeddingUnavailable:
            return 0
        updated = 0
        now = _now()
        with self._db() as connection:
            for (entry_id, content), raw in zip(rows, vectors, strict=False):
                vector = _valid_vector(raw)
                if vector is None:
                    connection.execute(
                        "UPDATE locus_long_term_memory SET embedding_status = 'failed', "
                        "embedding_model = ?, updated_at = ? WHERE id = ? AND content = ?",
                        (model, now, entry_id, content),
                    )
                    continue
                cursor = connection.execute(
                    "UPDATE locus_long_term_memory SET embedding = ?, embedding_model = ?, "
                    "embedding_dimensions = ?, embedding_status = 'ready', updated_at = ? "
                    "WHERE id = ? AND content = ?",
                    (_pack_vector(vector), model, len(vector), now, entry_id, content),
                )
                updated += max(0, cursor.rowcount)
        return updated

    # -- consolidation queue --------------------------------------------------------
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
        if not isinstance(entry, dict):
            entry = {"raw": entry}
        entry_id = _identifier(entry.get("id") or uuid4(), field="id")
        content = str(entry.get("content") or json.dumps(entry, default=_json_default))
        metadata = dict(entry)
        metadata.setdefault("queued_for_consolidation", True)
        metadata.setdefault("candidate_kind", candidate_kind)
        now = _now()
        with self._db() as connection:
            connection.execute(
                """
                INSERT INTO locus_memory_consolidation_queue (
                    id, entry_id, bucket_id, session_id, memory_scope, source, task_id,
                    candidate_kind, status, content, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    bucket_id = excluded.bucket_id,
                    session_id = excluded.session_id,
                    memory_scope = excluded.memory_scope,
                    source = excluded.source,
                    task_id = excluded.task_id,
                    candidate_kind = excluded.candidate_kind,
                    status = 'pending',
                    content = excluded.content,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                """,
                (
                    f"consolidation:{entry_id}",
                    entry_id,
                    _identifier(bucket_id, field="bucket_id"),
                    _identifier(session_id, field="session_id"),
                    _identifier(memory_scope, field="memory_scope"),
                    _identifier(source, field="source"),
                    _identifier(task_id, field="task_id", allow_empty=True) or None,
                    _identifier(candidate_kind or "promotion", field="candidate_kind"),
                    content[:MAX_CONTENT_CHARS],
                    _encode_metadata(metadata),
                    now,
                    now,
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
        bucket = _optional_filter(bucket_id, field="bucket_id")
        scope = _optional_filter(memory_scope, field="memory_scope")
        state = _optional_filter(status, field="status")
        with self._db() as connection:
            rows = connection.execute(
                f"SELECT {_CANDIDATE_COLUMNS} FROM locus_memory_consolidation_queue "
                "WHERE (? IS NULL OR bucket_id = ?) AND (? IS NULL OR memory_scope = ?) "
                "AND (? IS NULL OR status = ?) ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (bucket, bucket, scope, scope, state, state, _bounded_limit(limit, default=100)),
            ).fetchall()
        return [self._row_to_candidate(row) for row in reversed(rows)]

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
        identifier = _identifier(candidate_id, field="candidate_id")
        state = _identifier(status or "pending", field="status")
        with self._db() as connection:
            if isinstance(extra_metadata, dict) and extra_metadata:
                row = connection.execute(
                    "SELECT metadata FROM locus_memory_consolidation_queue WHERE id = ?",
                    (identifier,),
                ).fetchone()
                if row is None:
                    return
                merged = _decode_json_object(row[0])
                merged.update(extra_metadata)
                connection.execute(
                    "UPDATE locus_memory_consolidation_queue SET status = ?, metadata = ?, "
                    "updated_at = ? WHERE id = ?",
                    (state, _encode_metadata(merged), _now(), identifier),
                )
            else:
                connection.execute(
                    "UPDATE locus_memory_consolidation_queue SET status = ?, updated_at = ? "
                    "WHERE id = ?",
                    (state, _now(), identifier),
                )

    def count_consolidation_candidates(self, *, status: str = "pending") -> int:
        if not self.enabled:
            return 0
        self.initialize()
        with self._db() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM locus_memory_consolidation_queue WHERE status = ?",
                (_identifier(status or "pending", field="status"),),
            ).fetchone()
        return int(row[0]) if row else 0

    # -- collections ------------------------------------------------------------------
    def ensure_collection(self, collection_id: str, *, name: str, description: str = "") -> bool:
        """Register a collection if absent; ``True`` when this call created it. Idempotent."""
        if not self.enabled:
            return False
        self.initialize()
        with self._db() as connection:
            cursor = connection.execute(
                "INSERT INTO locus_memory_collections (id, name, description, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (
                    _identifier(collection_id, field="collection_id"),
                    _identifier(name, field="collection name"),
                    str(description or "")[:MAX_CONTENT_CHARS],
                    _now(),
                ),
            )
            return cursor.rowcount > 0

    def list_collections(self) -> list[dict[str, str]]:
        if not self.enabled:
            return []
        self.initialize()
        with self._db() as connection:
            rows = connection.execute(
                "SELECT id, name, description, created_at FROM locus_memory_collections "
                "ORDER BY created_at, id"
            ).fetchall()
        return [
            {
                "id": str(row[0]),
                "name": str(row[1]),
                "description": str(row[2]),
                "created_at": str(row[3]),
            }
            for row in rows
        ]

    def remove_collection(self, collection_id: str) -> None:
        if not self.enabled:
            return
        self.initialize()
        with self._db() as connection:
            connection.execute(
                "DELETE FROM locus_memory_collections WHERE id = ?",
                (_identifier(collection_id, field="collection_id"),),
            )
