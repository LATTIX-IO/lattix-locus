"""Durable run state for externally driven agent runtimes (LOCUS-361, LOCUS-352/354).

One SQLite database (WAL) holds, per run:

* ``locus_run_state`` -- the :class:`~locus_runtime.harness.runtime_controller.RunController`
  state (envelope, transcript, plan, budgets used, verification, end state):
  the same content as the verified loop's JSON checkpoint;
* ``locus_action_ledger`` -- one row per tool call, written *before* the call
  runs (``started``) and after it (``done`` / ``asked``). On resume a ``done``
  call is replayed from the ledger and a ``started`` call (the process died
  while it ran) is never run again, so a side effect happens at most once.

The LangGraph checkpointer of the Deep Agents runtime lives in the same file
(its own ``checkpoints`` / ``writes`` tables); each owner keeps its own schema
(D-28 rule 4). Standard library only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DB_PATH_ENV = "LOCUS_RUNS_DB"
SCHEMA_VERSION = 1

ActionStatus = Literal["started", "done", "asked", "interrupted"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS locus_run_state (
        run_id TEXT PRIMARY KEY,
        runtime TEXT NOT NULL,
        status TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        saved_at REAL NOT NULL,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS locus_action_ledger (
        run_id TEXT NOT NULL,
        call_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        status TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        PRIMARY KEY (run_id, call_id)
    )
    """,
)


def default_run_db_path(app_home: Path | None = None) -> Path:
    """``LOCUS_RUNS_DB``, else ``<app_home>/data/runs/runs.db``."""
    explicit = str(os.getenv(DB_PATH_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if app_home is None:
        from locus_runtime.win_toolchain import toolchain_app_home

        app_home = toolchain_app_home()
    return Path(app_home) / "data" / "runs" / "runs.db"


def open_sqlite(path: Path) -> sqlite3.Connection:
    """A connection in WAL mode with full fsync on commit (a ledger row must
    survive a crash that follows it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


@dataclass(frozen=True)
class LedgerEntry:
    call_id: str
    tool: str
    status: ActionStatus
    content: str


class RunStore:
    """Run state + action ledger in one SQLite file. Thread-safe."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = open_sqlite(self.path)
        with self._lock, self._conn:
            for statement in _SCHEMA:
                self._conn.execute(statement)

    # -- run state ---------------------------------------------------------------
    def save_state(self, run_id: str, runtime: str, status: str, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO locus_run_state(run_id, runtime, status, schema_version, saved_at, "
                "payload) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET "
                "runtime=excluded.runtime, status=excluded.status, "
                "schema_version=excluded.schema_version, saved_at=excluded.saved_at, "
                "payload=excluded.payload",
                (run_id, runtime, status, SCHEMA_VERSION, time.time(), text),
            )

    def load_state(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT runtime, schema_version, payload FROM locus_run_state WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        if int(row[1]) != SCHEMA_VERSION:
            raise ValueError(f"run {run_id!r}: unsupported run-state schema {row[1]}")
        payload = json.loads(row[2])
        if not isinstance(payload, dict):
            raise ValueError(f"run {run_id!r}: corrupt run state")
        state: dict[str, Any] = payload
        return state

    # -- action ledger -------------------------------------------------------------
    def action(self, run_id: str, call_id: str) -> LedgerEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tool, status, content FROM locus_action_ledger "
                "WHERE run_id = ? AND call_id = ?",
                (run_id, call_id),
            ).fetchone()
        if row is None:
            return None
        return LedgerEntry(call_id=call_id, tool=str(row[0]), status=row[1], content=str(row[2]))

    def mark_action(
        self, run_id: str, call_id: str, tool: str, status: ActionStatus, content: str = ""
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO locus_action_ledger(run_id, call_id, tool, status, content, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, call_id) DO UPDATE "
                "SET tool=excluded.tool, status=excluded.status, content=excluded.content, "
                "updated_at=excluded.updated_at",
                (run_id, call_id, tool, status, content, time.time()),
            )

    def actions(self, run_id: str) -> list[LedgerEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT call_id, tool, status, content FROM locus_action_ledger "
                "WHERE run_id = ? ORDER BY updated_at",
                (run_id,),
            ).fetchall()
        return [LedgerEntry(str(r[0]), str(r[1]), r[2], str(r[3])) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
