"""Fail-closed write-ahead action receipts for already-admitted ephemeral pods.

This is an in-process trusted-supervisor primitive, NOT a new tool executor.
Every action is PEP-authorized and written durably before side effects start.
Ambiguous starts never replay without explicit supervisor reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, TypeVar, cast

from locus_runtime.action_fencing import action_lock
from locus_runtime.gateway import GatewaySession
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, PodReservationLedger

_ACTION_RECEIPT_SCHEMA_VERSION = 1
_T = TypeVar("_T")


class ActionReplayDenied(AdmissionDenied):
    """An external effect may already have happened and must not be replayed."""


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: str
    reservation_id: str
    idempotency_key: str
    fingerprint: str
    gateway_audit_id: str
    state: str
    child_run_id: str
    outcome_digest: str | None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_digest(value: object) -> str:
    """Bounded canonical result fingerprint; never writes the raw payload."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False, default=repr)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise AdmissionDenied("unserializable action outcome must be reconciled") from exc
    if len(encoded.encode("utf-8")) > 1_048_576:
        raise AdmissionDenied("action outcome exceeds fingerprint size limit")
    return _sha(encoded)


def _valid_key(key: str) -> str:
    if not isinstance(key, str) or not (1 <= len(key) <= 160) or not key.isascii():
        raise ValueError("bounded ASCII idempotency key required")
    if any(not (c.isalnum() or c in "-._:") for c in key):
        raise ValueError("idempotency key contains unsupported characters")
    return key


class PodActionReceipts:
    """Durable receipt authority using the *same SQLite DB* as pod reservations.

    Never expose this object, the PodLease or a raw GatewaySession to untrusted
    worker code. Only the trusted tool broker may pass execution callbacks.
    """

    def __init__(self, reservations: PodReservationLedger,
                 *, supervisor: GatewaySession | None = None) -> None:
        self.reservations = reservations
        # Only a trusted supervisor may authorize *new* action intents.
        # Read/recovery APIs can reopen the database without this handle.
        self._supervisor = supervisor
        self._initialize()

    def _initialize(self) -> None:
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            db.execute("""CREATE TABLE IF NOT EXISTS pod_action_schema (
                version INTEGER NOT NULL
            )""")
            version = db.execute("SELECT version FROM pod_action_schema").fetchone()
            if version is None:
                db.execute("INSERT INTO pod_action_schema(version) VALUES (?)",
                           (_ACTION_RECEIPT_SCHEMA_VERSION,))
            elif version["version"] != _ACTION_RECEIPT_SCHEMA_VERSION:
                raise RuntimeError("unsupported action receipt schema; migration required")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_action_receipt (
                id TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL REFERENCES pod_attempt(id),
                idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                gateway_audit_id TEXT NOT NULL,
                supervisor_token_digest TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                child_run_id TEXT NOT NULL,
                state TEXT NOT NULL,
                outcome_digest TEXT,
                UNIQUE(reservation_id, idempotency_key)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_action_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id TEXT NOT NULL REFERENCES pod_action_receipt(id),
                event TEXT NOT NULL,
                note_digest TEXT
            )""")

    @staticmethod
    def _record(row: sqlite3.Row) -> ActionRecord:
        return ActionRecord(
            row["id"], row["reservation_id"], row["idempotency_key"],
            row["fingerprint"], row["gateway_audit_id"], row["state"],
            row["child_run_id"], row["outcome_digest"],
        )

    @staticmethod
    def _row(db: sqlite3.Connection, action_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM pod_action_receipt WHERE id=?",
                         (action_id,)).fetchone()
        if row is None:
            raise AdmissionDenied("unknown action receipt")
        return cast(sqlite3.Row, row)

    def _active_locked(self, db: sqlite3.Connection, lease: PodLease) -> sqlite3.Row:
        if not isinstance(lease, PodLease) or lease._ledger.path != self.reservations.path:
            raise AdmissionDenied("unrecognized pod lease")
        row = db.execute("SELECT * FROM pod_attempt WHERE id=?",
                         (lease.reservation_id,)).fetchone()
        if row is None:
            raise AdmissionDenied("unknown pod attempt")
        parent = db.execute("SELECT revoked FROM parent_budget WHERE run_id=?",
                            (row["parent_id"],)).fetchone()
        if (parent is None or parent["revoked"] or row["state"] != "active"
            or row["expires_at"] <= self.reservations._now()
            or row["child_run_id"] != lease.run_id):
            raise AdmissionDenied("action requires the current admitted pod lease")
        # Prevent use of revoked/delegated sessions even before an action
        # reaches the gateway. Gateway itself remains the final PEP.
        if lease._session.gateway._authenticate(lease._session.caller) is None:
            raise AdmissionDenied("gateway pod identity was revoked")
        return cast(sqlite3.Row, row)

    def prepare(self, lease: PodLease, *, idempotency_key: str,
                authorization: Mapping[str, Any]) -> ActionRecord:
        """Authorize through Locus PEP, then persist the intent and audit ID.

        The mapping is used only for immediate gateway authorization; no raw
        command, user input, tool arguments or other sensitive data is stored.
        """
        _valid_key(idempotency_key)
        if not isinstance(authorization, Mapping) or not authorization:
            raise AdmissionDenied("missing action authorization request")
        # Preflight gateway check; the reservation/session is rechecked inside
        # the transaction to avoid a revoked or expired lease TOCTOU.
        decision = lease.authorize(**dict(authorization))
        if not decision.allowed:
            raise AdmissionDenied("action was not allowed by Zero Trust gateway")
        if not decision.audit_id or not decision.fingerprint:
            raise AdmissionDenied("missing signed action provenance")
        # GatewayDecision is freshly process-signed, but its fingerprint
        # includes an ephemeral HMAC key. The durable action identity is
        # independent of that per-process key and recomputable after restart.
        digest = _sha(json.dumps([
            decision.action_kind, decision.tool, decision.target,
            decision.args_digest, lease.run_id,
        ], separators=(",", ":"), ensure_ascii=True))
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            pod = self._active_locked(db, lease)
            supervisor = self._supervisor
            if supervisor is None or supervisor.gateway is not lease._session.gateway:
                raise AdmissionDenied("missing bound trusted supervisor identity")
            trusted = supervisor.gateway._authenticate(supervisor.caller)
            if (trusted is None or not trusted.can_delegate
                or pod["parent_id"] != supervisor.caller.run_id):
                raise AdmissionDenied("unregistered or foreign supervising authority")
            supervisor_digest = _sha(supervisor.caller.token)
            old = db.execute("""SELECT * FROM pod_action_receipt
                WHERE reservation_id=? AND idempotency_key=?""",
                (lease.reservation_id, idempotency_key)).fetchone()
            if old is not None:
                if old["fingerprint"] != digest or old["child_run_id"] != lease.run_id:
                    raise ActionReplayDenied("same idempotency key has different action")
                return self._record(old)
            max_actions = self.reservations._as_reservation(pod).requested.actions
            count_row = db.execute("""SELECT COUNT(*) FROM pod_action_receipt
                WHERE reservation_id=?""", (lease.reservation_id,)).fetchone()
            if count_row is None:
                raise AdmissionDenied("missing action count for reservation")
            if int(count_row[0]) >= max_actions:
                raise AdmissionDenied("action intent ceiling exhausted")
            if decision.action_kind in ("tool_call", "mcp_tool_call"):
                tool_count_row = db.execute("""SELECT COUNT(*) FROM pod_action_receipt
                    WHERE reservation_id=? AND action_kind IN ('tool_call','mcp_tool_call')""",
                    (lease.reservation_id,)).fetchone()
                if tool_count_row is None:
                    raise AdmissionDenied("tool call accounting unavailable")
                max_tools = self.reservations._as_reservation(pod).requested.tool_calls
                if int(tool_count_row[0]) >= max_tools:
                    raise AdmissionDenied("tool call intent ceiling exhausted")
            action_id = secrets.token_urlsafe(24)
            db.execute("""INSERT INTO pod_action_receipt
                (id,reservation_id,idempotency_key,fingerprint,gateway_audit_id,
                 supervisor_token_digest,action_kind,child_run_id,state)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (action_id, lease.reservation_id, idempotency_key, digest,
                 decision.audit_id, supervisor_digest, decision.action_kind,
                 lease.run_id, "prepared"))
            db.execute("""INSERT INTO pod_action_audit(action_id,event)
                VALUES (?,?)""", (action_id, "prepared"))
            return self._record(self._row(db, action_id))

    def begin_dispatch(self, lease: PodLease, action_id: str) -> ActionRecord:
        """Legacy/manual dispatch; never eligible for automated crash recovery."""
        return self._begin_dispatch(lease, action_id, guarded=False)

    def _begin_dispatch(self, lease: PodLease, action_id: str,
                        *, guarded: bool) -> ActionRecord:
        """Write dispatch intent, including OS-fence eligibility atomically."""
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            self._active_locked(db, lease)
            row = self._row(db, action_id)
            if row["reservation_id"] != lease.reservation_id or row["child_run_id"] != lease.run_id:
                raise AdmissionDenied("cross-pod action receipt")
            if row["state"] != "prepared":
                raise ActionReplayDenied("action may have dispatched already")
            db.execute("UPDATE pod_action_receipt SET state='dispatching' WHERE id=?", (action_id,))
            db.execute("INSERT INTO pod_action_audit(action_id,event) VALUES (?,?)",
                       (action_id, "dispatching"))
            if guarded:
                # Only the trusted execute_once broker enters this path
                # while holding a kernel lock for the entire callback.
                db.execute("INSERT INTO pod_action_audit(action_id,event) VALUES (?,?)",
                           (action_id, "fence_guarded"))
            return self._record(self._row(db, action_id))

    def _complete(self, action_id: str, state: Literal["succeeded", "uncertain"],
                  *, evidence: str) -> ActionRecord:
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            row = self._row(db, action_id)
            if row["state"] != "dispatching":
                raise ActionReplayDenied("action already completed or reconciled")
            receipt = _sha(evidence)
            db.execute("""UPDATE pod_action_receipt
                SET state=?,outcome_digest=? WHERE id=?""", (state, receipt, action_id))
            db.execute("""INSERT INTO pod_action_audit(action_id,event,note_digest)
                VALUES (?,?,?)""", (action_id, state, receipt))
            return self._record(self._row(db, action_id))

    def execute_once(self, lease: PodLease, *, idempotency_key: str,
                     authorization: Mapping[str, Any],
                     invoke: Callable[[], _T]) -> tuple[ActionRecord, _T]:
        """Trusted broker only: at-most-once *invocation*, not exactly-once effect."""
        intent = self.prepare(lease, idempotency_key=idempotency_key,
                              authorization=authorization)
        # Kernel mutex is held across both the write-ahead dispatch and the
        # entire callback/result receipt. Another local recovery controller
        # cannot mistake live work for a crashed process.
        with action_lock(self.reservations.path, intent.action_id) as acquired:
            if not acquired:
                raise ActionReplayDenied("another live broker owns this action")
            self._begin_dispatch(lease, intent.action_id, guarded=True)
            try:
                output = invoke()
                digest = _result_digest(output)
            except BaseException as exc:
                # Never retry ambiguous provider results automatically.
                self._complete(intent.action_id, "uncertain",
                               evidence=type(exc).__qualname__)
                raise
            receipt = self._complete(intent.action_id, "succeeded", evidence=digest)
            return receipt, output

    def get(self, action_id: str) -> ActionRecord:
        with self.reservations._connect() as db:
            return self._record(self._row(db, action_id))

    def recover_ambiguous(self) -> tuple[str, ...]:
        """Report possibly orphaned dispatches; NEVER alter a live callback.

        A broker opening the shared DB cannot distinguish a crashed worker
        from another live worker. Retain 'dispatching' and the replay block;
        a later exclusive recovery controller must prove liveness/fencing.
        """
        with self.reservations._connect() as db:
            rows = db.execute("""SELECT id FROM pod_action_receipt
                WHERE state='dispatching' ORDER BY id""").fetchall()
            return tuple(row["id"] for row in rows)

    def reconcile(self, parent: GatewaySession, action_id: str,
                  *, external_receipt: str) -> ActionRecord:
        """Trusted supervisor records a *reference*, not proof of outcome.

        This does not authorize a replay or assert a verified external effect.
        Future integration must validate provider-signed receipts independently.
        """
        if not isinstance(external_receipt, str) or not external_receipt.strip():
            raise ValueError("external evidence reference required")
        trusted = parent.gateway._authenticate(parent.caller)
        if trusted is None or not trusted.can_delegate:
            raise AdmissionDenied("reconciliation requires registered supervisor")
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            row = self._row(db, action_id)
            pod = db.execute("SELECT parent_id FROM pod_attempt WHERE id=?",
                             (row["reservation_id"],)).fetchone()
            if (pod is None or pod["parent_id"] != parent.caller.run_id
                or row["supervisor_token_digest"] != _sha(parent.caller.token)):
                raise AdmissionDenied("supervisor token is not the recorded parent authority")
            if row["state"] != "uncertain":
                raise ActionReplayDenied("only uncertain actions can be reconciled")
            digest = _sha(external_receipt)
            db.execute("""UPDATE pod_action_receipt
                SET state='reconciled',outcome_digest=? WHERE id=?""",
                (digest, action_id))
            db.execute("""INSERT INTO pod_action_audit(action_id,event,note_digest)
                VALUES (?,?,?)""", (action_id, "reconciled", digest))
            return self._record(self._row(db, action_id))

    def audit(self, action_id: str) -> tuple[tuple[str, str | None], ...]:
        with self.reservations._connect() as db:
            return tuple((row["event"], row["note_digest"]) for row in db.execute(
                "SELECT event,note_digest FROM pod_action_audit WHERE action_id=? ORDER BY sequence",
                (action_id,)))
