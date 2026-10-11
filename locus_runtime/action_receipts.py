"""Fail-closed write-ahead action receipts for already-admitted ephemeral pods.

This is an in-process trusted-supervisor primitive, NOT a new tool executor.
Every action is PEP-authorized and written durably before side effects start.
Ambiguous starts never replay without explicit supervisor reconciliation.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, TypeVar, cast

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

    def __init__(self, reservations: PodReservationLedger) -> None:
        self.reservations = reservations
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
        digest = _sha("|".join((
            decision.action_kind, decision.tool, decision.fingerprint,
            decision.args_digest, lease.run_id,
        )))
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            pod = self._active_locked(db, lease)
            old = db.execute("""SELECT * FROM pod_action_receipt
                WHERE reservation_id=? AND idempotency_key=?""",
                (lease.reservation_id, idempotency_key)).fetchone()
            if old is not None:
                if old["fingerprint"] != digest or old["child_run_id"] != lease.run_id:
                    raise ActionReplayDenied("same idempotency key has different action")
                return self._record(old)
            max_actions = self.reservations._as_reservation(pod).requested.actions
            count = db.execute("""SELECT COUNT(*) FROM pod_action_receipt
                WHERE reservation_id=?""", (lease.reservation_id,)).fetchone()[0]
            if count >= max_actions:
                raise AdmissionDenied("action intent ceiling exhausted")
            action_id = secrets.token_urlsafe(24)
            db.execute("""INSERT INTO pod_action_receipt
                (id,reservation_id,idempotency_key,fingerprint,gateway_audit_id,
                 child_run_id,state) VALUES(?,?,?,?,?,?,?)""",
                (action_id, lease.reservation_id, idempotency_key, digest,
                 decision.audit_id, lease.run_id, "prepared"))
            db.execute("""INSERT INTO pod_action_audit(action_id,event)
                VALUES (?,?)""", (action_id, "prepared"))
            return self._record(self._row(db, action_id))

    def begin_dispatch(self, lease: PodLease, action_id: str) -> ActionRecord:
        """Write/commit dispatch intent before invoking any external effect."""
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
        self.begin_dispatch(lease, intent.action_id)
        try:
            output = invoke()
        except BaseException as exc:
            # The external effect may have completed before a provider timeout
            # or worker crash. Never replay; require out-of-band proof.
            self._complete(intent.action_id, "uncertain",
                           evidence=type(exc).__qualname__)
            raise
        receipt = self._complete(intent.action_id, "succeeded",
                                 evidence=repr(type(output)))
        return receipt, output

    def get(self, action_id: str) -> ActionRecord:
        with self.reservations._connect() as db:
            return self._record(self._row(db, action_id))

    def recover_ambiguous(self) -> tuple[str, ...]:
        """On restart, mark all interrupted dispatches uncertain, never replay."""
        with self.reservations._connect() as db:
            self.reservations._tx(db)
            rows = db.execute("""SELECT id FROM pod_action_receipt
                WHERE state='dispatching'""").fetchall()
            for row in rows:
                db.execute("UPDATE pod_action_receipt SET state='uncertain' WHERE id=?",
                           (row["id"],))
                db.execute("""INSERT INTO pod_action_audit(action_id,event)
                    VALUES (?,?)""", (row["id"], "uncertain"))
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
            if pod is None or pod["parent_id"] != parent.caller.run_id:
                raise AdmissionDenied("supervisor does not own action")
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
