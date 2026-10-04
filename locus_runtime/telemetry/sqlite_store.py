"""The local SQLite telemetry store: always-on ``TelemetrySink`` and ``TelemetryReader``.

One file under the app home (``<app_home>/data/telemetry/telemetry.db``), WAL
mode, written only from the batch span processor's worker thread (the agent
never waits on it). Schema is versioned with ``PRAGMA user_version`` and
migrated forward step by step (:data:`MIGRATIONS`); a file from a newer Locus
is never written to.

Retention (O-09): content payloads are cleared after ``payload_retention_days``
(default 90); spans, events and scores are deleted after
``span_retention_days`` (default 365). Audit records are not kept here: the
audit log stays the record of what happened (P11), telemetry is diagnostic.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from locus_runtime.telemetry import semconv as sc
from locus_runtime.telemetry.contract import (
    DEFAULT_PAYLOAD_RETENTION_DAYS,
    DEFAULT_SPAN_RETENTION_DAYS,
    PORT_VERSION,
    DestinationClass,
    Percentiles,
    RunPage,
    RunQuery,
    RunSummary,
    ScoreRecord,
    SpanEventRecord,
    SpanNode,
    SpanRecord,
    TelemetrySummary,
    TraceView,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_DAY_NS = 86_400 * 1_000_000_000
_MAX_TRACE_SPANS = 5000
_MAX_LATENCY_SAMPLES = 100_000

#: Forward migrations: ``MIGRATIONS[n]`` takes a database from version n-1 to n.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS spans (
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            parent_span_id TEXT,
            run_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'INTERNAL',
            operation TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'UNSET',
            status_message TEXT NOT NULL DEFAULT '',
            start_ns INTEGER NOT NULL,
            end_ns INTEGER NOT NULL,
            duration_ms REAL NOT NULL DEFAULT 0,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT '',
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            error_type TEXT NOT NULL DEFAULT '',
            attributes TEXT NOT NULL DEFAULT '{}',
            payload TEXT,
            PRIMARY KEY (trace_id, span_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS spans_run ON spans(run_id)",
        "CREATE INDEX IF NOT EXISTS spans_start ON spans(start_ns)",
        "CREATE INDEX IF NOT EXISTS spans_op_start ON spans(operation, start_ns)",
        """
        CREATE TABLE IF NOT EXISTS span_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            name TEXT NOT NULL,
            time_ns INTEGER NOT NULL,
            attributes TEXT NOT NULL DEFAULT '{}',
            payload TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS span_events_span ON span_events(trace_id, span_id)",
        "CREATE INDEX IF NOT EXISTS span_events_time ON span_events(time_ns)",
        """
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            value REAL,
            label TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            comment TEXT NOT NULL DEFAULT '',
            time_ns INTEGER NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS scores_unique ON scores(trace_id, span_id, name, time_ns)",
        "CREATE INDEX IF NOT EXISTS scores_run ON scores(run_id)",
        "CREATE INDEX IF NOT EXISTS scores_time ON scores(time_ns)",
    ),
}


class TelemetryStoreError(RuntimeError):
    """The store cannot be used (unwritable file, schema from a newer Locus, ...)."""


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def _loads(text: Any) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def percentiles(values: Sequence[float]) -> Percentiles:
    """Nearest-rank p50/p90/p99."""
    ordered = sorted(values)
    if not ordered:
        return Percentiles()

    def rank(p: float) -> float:
        index = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
        return round(ordered[index], 3)

    return Percentiles(count=len(ordered), p50=rank(0.5), p90=rank(0.9), p99=rank(0.99))


class SqliteTelemetryStore:
    """Local span store (see the module docstring)."""

    name = "sqlite"
    port_version = PORT_VERSION
    destination: DestinationClass = "local"

    def __init__(
        self,
        path: str | Path,
        *,
        payload_retention_days: int = DEFAULT_PAYLOAD_RETENTION_DAYS,
        span_retention_days: int = DEFAULT_SPAN_RETENTION_DAYS,
        prune_interval_seconds: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.payload_retention_days = int(payload_retention_days)
        self.span_retention_days = max(int(span_retention_days), int(payload_retention_days))
        self._prune_interval = float(prune_interval_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._migrated = False
        self._last_prune = 0.0

    # -- connection / schema -------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def schema_version(self) -> int:
        if not self.path.exists():
            return 0
        with self._connect() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Create or upgrade the schema; returns the version. Idempotent."""
        with self._lock:
            if self._migrated:
                return SCHEMA_VERSION
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise TelemetryStoreError(
                        f"telemetry schema {version} is newer than this Locus ({SCHEMA_VERSION})"
                    )
                for target in range(version + 1, SCHEMA_VERSION + 1):
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in MIGRATIONS[target]:
                            conn.execute(statement)
                        conn.execute(f"PRAGMA user_version = {int(target)}")
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
            self._migrated = True
            return SCHEMA_VERSION

    def _ready(self) -> None:
        if not self._migrated:
            self.migrate()

    # -- sink ------------------------------------------------------------------------
    def export(self, spans: list[SpanRecord]) -> bool:
        if not spans:
            return True
        try:
            self._ready()
            with self._lock, self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for record in spans:
                        self._write_span(conn, record)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        except Exception:  # noqa: BLE001 - the contract: never raise into the processor
            logger.exception("telemetry.sqlite_write_error")
            return False
        self._maybe_prune()
        return True

    def _write_span(self, conn: sqlite3.Connection, record: SpanRecord) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO spans (
                trace_id, span_id, parent_span_id, run_id, name, kind, operation, status,
                status_message, start_ns, end_ns, duration_ms, provider, model, tool,
                input_tokens, output_tokens, cost_usd, error_type, attributes, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.trace_id,
                record.span_id,
                record.parent_span_id,
                record.run_id,
                record.name,
                record.kind,
                record.operation,
                record.status,
                record.status_message,
                record.start_ns,
                record.end_ns,
                record.duration_ms,
                record.provider,
                record.model,
                record.tool,
                record.input_tokens,
                record.output_tokens,
                record.cost_usd,
                record.error_type,
                _dumps(record.attributes),
                _dumps(record.payload) if record.payload else None,
            ),
        )
        conn.execute(
            "DELETE FROM span_events WHERE trace_id = ? AND span_id = ?",
            (record.trace_id, record.span_id),
        )
        for event in record.events:
            conn.execute(
                "INSERT INTO span_events (trace_id, span_id, name, time_ns, attributes, payload)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.trace_id,
                    record.span_id,
                    event.name,
                    event.time_ns,
                    _dumps(event.attributes),
                    _dumps(event.payload) if event.payload else None,
                ),
            )
            if event.name == sc.GEN_AI_EVALUATION_EVENT:
                self._write_score(conn, record, event)

    @staticmethod
    def _write_score(conn: sqlite3.Connection, record: SpanRecord, event: SpanEventRecord) -> None:
        attrs = event.attributes
        value = attrs.get(sc.GEN_AI_EVALUATION_SCORE_VALUE)
        conn.execute(
            "INSERT OR IGNORE INTO scores (trace_id, span_id, run_id, name, value, label, source,"
            " comment, time_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.trace_id,
                record.span_id,
                str(attrs.get(sc.LOCUS_RUN_ID) or record.run_id or ""),
                str(attrs.get(sc.GEN_AI_EVALUATION_NAME) or ""),
                float(value) if isinstance(value, (int, float)) else None,
                str(attrs.get(sc.GEN_AI_EVALUATION_SCORE_LABEL) or ""),
                str(attrs.get(sc.LOCUS_SCORE_SOURCE) or ""),
                str(attrs.get(sc.GEN_AI_EVALUATION_EXPLANATION) or ""),
                event.time_ns,
            ),
        )

    def shutdown(self) -> None:
        return None

    # -- retention ---------------------------------------------------------------------
    def _maybe_prune(self) -> None:
        now = self._clock()
        if now - self._last_prune < self._prune_interval:
            return
        self._last_prune = now
        try:
            self.prune()
        except Exception:  # noqa: BLE001 - retention retries at the next interval
            logger.exception("telemetry.sqlite_prune_error")

    def prune(self, now_ns: int | None = None) -> dict[str, int]:
        """Clear payloads older than the payload retention; delete expired spans."""
        self._ready()
        now = int(now_ns if now_ns is not None else self._clock() * 1e9)
        payload_cutoff = now - self.payload_retention_days * _DAY_NS
        span_cutoff = now - self.span_retention_days * _DAY_NS
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                payloads = conn.execute(
                    "UPDATE spans SET payload = NULL WHERE payload IS NOT NULL AND end_ns < ?",
                    (payload_cutoff,),
                ).rowcount
                payloads += conn.execute(
                    "UPDATE span_events SET payload = NULL WHERE payload IS NOT NULL"
                    " AND time_ns < ?",
                    (payload_cutoff,),
                ).rowcount
                events = conn.execute(
                    "DELETE FROM span_events WHERE time_ns < ?", (span_cutoff,)
                ).rowcount
                spans = conn.execute("DELETE FROM spans WHERE end_ns < ?", (span_cutoff,)).rowcount
                scores = conn.execute(
                    "DELETE FROM scores WHERE time_ns < ?", (span_cutoff,)
                ).rowcount
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return {"payloads_cleared": payloads, "spans": spans, "events": events, "scores": scores}

    # -- reader --------------------------------------------------------------------------
    def _row_to_span(self, row: sqlite3.Row, events: list[SpanEventRecord]) -> SpanRecord:
        return SpanRecord(
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            name=row["name"],
            kind=row["kind"],
            operation=row["operation"],
            run_id=row["run_id"],
            status=row["status"] if row["status"] in {"UNSET", "OK", "ERROR"} else "UNSET",
            status_message=row["status_message"],
            start_ns=int(row["start_ns"]),
            end_ns=int(row["end_ns"]),
            provider=row["provider"],
            model=row["model"],
            tool=row["tool"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            error_type=row["error_type"],
            attributes=_loads(row["attributes"]) or {},
            payload=_loads(row["payload"]),
            events=events,
        )

    @staticmethod
    def _score(row: sqlite3.Row) -> ScoreRecord:
        return ScoreRecord(
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            run_id=row["run_id"],
            name=row["name"],
            value=row["value"],
            label=row["label"],
            source=row["source"],
            comment=row["comment"],
            time_ns=int(row["time_ns"]),
        )

    def _scores_for(self, conn: sqlite3.Connection, run_ids: Sequence[str]) -> list[ScoreRecord]:
        if not run_ids:
            return []
        marks = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"SELECT * FROM scores WHERE run_id IN ({marks}) ORDER BY time_ns", tuple(run_ids)
        ).fetchall()
        return [self._score(r) for r in rows]

    def list_runs(self, query: RunQuery) -> RunPage:
        self._ready()
        where = ["operation = ?"]
        params: list[Any] = [sc.OP_INVOKE_AGENT]
        if query.since_ns is not None:
            where.append("start_ns >= ?")
            params.append(query.since_ns)
        if query.until_ns is not None:
            where.append("start_ns < ?")
            params.append(query.until_ns)
        if query.status:
            where.append("status = ?")
            params.append(query.status.upper())
        if query.end_state:
            where.append(f"json_extract(attributes, '$.\"{sc.LOCUS_END_STATE}\"') = ?")
            params.append(query.end_state)
        if query.runtime:
            where.append(f"json_extract(attributes, '$.\"{sc.LOCUS_RUNTIME}\"') = ?")
            params.append(query.runtime)
        clause = " AND ".join(where)
        with self._connect() as conn:
            total = int(
                conn.execute(f"SELECT COUNT(*) FROM spans WHERE {clause}", params).fetchone()[0]
            )
            rows = conn.execute(
                f"SELECT * FROM spans WHERE {clause} ORDER BY start_ns DESC LIMIT ? OFFSET ?",
                [*params, query.limit, query.offset],
            ).fetchall()
            run_ids = sorted({str(r["run_id"]) for r in rows if r["run_id"]})
            aggregates = self._aggregates(conn, run_ids)
            scores = self._scores_for(conn, run_ids)
        runs = []
        for row in rows:
            attrs = _loads(row["attributes"]) or {}
            agg = aggregates.get(str(row["run_id"]), {})
            verified = attrs.get(sc.LOCUS_VERIFIED)
            runs.append(
                RunSummary(
                    run_id=str(row["run_id"]),
                    trace_id=row["trace_id"],
                    name=row["name"],
                    agent=str(attrs.get(sc.GEN_AI_AGENT_NAME) or ""),
                    runtime=str(attrs.get(sc.LOCUS_RUNTIME) or ""),
                    provider=row["provider"],
                    model=row["model"],
                    status=row["status"],
                    end_state=str(attrs.get(sc.LOCUS_END_STATE) or ""),
                    verified=verified if isinstance(verified, bool) else None,
                    start_ns=int(row["start_ns"]),
                    end_ns=int(row["end_ns"]),
                    duration_ms=float(row["duration_ms"]),
                    model_calls=int(agg.get("model_calls") or 0),
                    tool_calls=int(agg.get("tool_calls") or 0),
                    errors=int(agg.get("errors") or 0),
                    input_tokens=int(agg.get("input_tokens") or 0),
                    output_tokens=int(agg.get("output_tokens") or 0),
                    cost_usd=round(float(agg.get("cost_usd") or 0.0), 6),
                    scores=[s for s in scores if s.run_id == row["run_id"]],
                )
            )
        return RunPage(runs=runs, total=total, limit=query.limit, offset=query.offset)

    @staticmethod
    def _aggregates(conn: sqlite3.Connection, run_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not run_ids:
            return {}
        marks = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"""
            SELECT run_id,
                   SUM(CASE WHEN operation = ? THEN 1 ELSE 0 END) AS model_calls,
                   SUM(CASE WHEN operation = ? THEN 1 ELSE 0 END) AS tool_calls,
                   SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN operation = ? THEN COALESCE(input_tokens, 0) ELSE 0 END)
                       AS input_tokens,
                   SUM(CASE WHEN operation = ? THEN COALESCE(output_tokens, 0) ELSE 0 END)
                       AS output_tokens,
                   SUM(CASE WHEN operation = ? THEN COALESCE(cost_usd, 0) ELSE 0 END) AS cost_usd
            FROM spans WHERE run_id IN ({marks}) GROUP BY run_id
            """,
            (sc.OP_CHAT, sc.OP_EXECUTE_TOOL, sc.OP_CHAT, sc.OP_CHAT, sc.OP_CHAT, *run_ids),
        ).fetchall()
        return {str(r["run_id"]): dict(r) for r in rows}

    def trace(self, run_id: str) -> TraceView | None:
        """Every span of the trace(s) the run belongs to, as a tree."""
        self._ready()
        with self._connect() as conn:
            trace_ids = [
                str(r[0])
                for r in conn.execute(
                    "SELECT trace_id, MIN(start_ns) AS first FROM spans WHERE run_id = ?"
                    " GROUP BY trace_id ORDER BY first",
                    (run_id,),
                ).fetchall()
            ]
            if not trace_ids:
                return None
            marks = ",".join("?" for _ in trace_ids)
            rows = conn.execute(
                f"SELECT * FROM spans WHERE trace_id IN ({marks}) ORDER BY start_ns LIMIT ?",
                (*trace_ids, _MAX_TRACE_SPANS),
            ).fetchall()
            event_rows = conn.execute(
                f"SELECT * FROM span_events WHERE trace_id IN ({marks}) ORDER BY time_ns, id",
                tuple(trace_ids),
            ).fetchall()
            scores = self._scores_for(conn, [run_id])
        events: dict[tuple[str, str], list[SpanEventRecord]] = {}
        for ev in event_rows:
            events.setdefault((ev["trace_id"], ev["span_id"]), []).append(
                SpanEventRecord(
                    name=ev["name"],
                    time_ns=int(ev["time_ns"]),
                    attributes=_loads(ev["attributes"]) or {},
                    payload=_loads(ev["payload"]),
                )
            )
        nodes: dict[tuple[str, str], SpanNode] = {}
        order: list[tuple[str, str]] = []
        for row in rows:
            key = (row["trace_id"], row["span_id"])
            nodes[key] = SpanNode(span=self._row_to_span(row, events.get(key, [])))
            order.append(key)
        roots: list[SpanNode] = []
        for key in order:
            node = nodes[key]
            parent = node.span.parent_span_id
            parent_node = nodes.get((key[0], parent)) if parent else None
            if parent_node is not None:
                parent_node.children.append(node)
            else:
                roots.append(node)
        return TraceView(
            run_id=run_id, trace_ids=trace_ids, roots=roots, span_count=len(rows), scores=scores
        )

    def summary(self, since_ns: int, until_ns: int) -> TelemetrySummary:
        self._ready()
        window = (since_ns, until_ns)
        with self._connect() as conn:

            def durations(operation: str) -> list[float]:
                return [
                    float(r[0])
                    for r in conn.execute(
                        "SELECT duration_ms FROM spans WHERE operation = ? AND start_ns >= ?"
                        " AND start_ns < ? ORDER BY start_ns DESC LIMIT ?",
                        (operation, *window, _MAX_LATENCY_SAMPLES),
                    ).fetchall()
                ]

            chat = conn.execute(
                "SELECT COUNT(*), SUM(COALESCE(input_tokens, 0)), SUM(COALESCE(output_tokens, 0)),"
                " SUM(COALESCE(cost_usd, 0)) FROM spans WHERE operation = ? AND start_ns >= ?"
                " AND start_ns < ?",
                (sc.OP_CHAT, *window),
            ).fetchone()
            tools = conn.execute(
                "SELECT COUNT(*) FROM spans WHERE operation = ? AND start_ns >= ? AND start_ns < ?",
                (sc.OP_EXECUTE_TOOL, *window),
            ).fetchone()
            errors = conn.execute(
                "SELECT operation, COUNT(*) FROM spans WHERE status = 'ERROR' AND start_ns >= ?"
                " AND start_ns < ? GROUP BY operation",
                window,
            ).fetchall()
            gates = conn.execute(
                "SELECT name, label, COUNT(*) FROM scores WHERE time_ns >= ? AND time_ns < ?"
                " GROUP BY name, label",
                window,
            ).fetchall()
            end_states = conn.execute(
                f"SELECT json_extract(attributes, '$.\"{sc.LOCUS_END_STATE}\"') AS state, COUNT(*)"
                " FROM spans WHERE operation = ? AND start_ns >= ? AND start_ns < ?"
                " GROUP BY state",
                (sc.OP_INVOKE_AGENT, *window),
            ).fetchall()
            run_latency = durations(sc.OP_INVOKE_AGENT)
            model_latency = durations(sc.OP_CHAT)
            tool_latency = durations(sc.OP_EXECUTE_TOOL)
        gate_outcomes: dict[str, dict[str, int]] = {}
        for name, label, count in gates:
            gate_outcomes.setdefault(str(name), {})[str(label or "unlabelled")] = int(count)
        return TelemetrySummary(
            since_ns=since_ns,
            until_ns=until_ns,
            runs=sum(int(count) for _state, count in end_states),
            model_calls=int(chat[0] or 0),
            tool_calls=int(tools[0] or 0),
            input_tokens=int(chat[1] or 0),
            output_tokens=int(chat[2] or 0),
            cost_usd=round(float(chat[3] or 0.0), 6),
            run_latency_ms=percentiles(run_latency),
            model_latency_ms=percentiles(model_latency),
            tool_latency_ms=percentiles(tool_latency),
            errors={str(op or "other"): int(count) for op, count in errors},
            gate_outcomes=gate_outcomes,
            end_states={str(state or "unknown"): int(count) for state, count in end_states},
        )
