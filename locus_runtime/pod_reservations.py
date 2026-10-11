"""Durable, transactional parent-budget accounting for ephemeral pod admission.

LOCUS-433: SQLite WAL + BEGIN IMMEDIATE serializes competing admissions across
threads/processes. This is an admission/ledger primitive, NOT an agent runner.
The gateway remains the PEP; model-produced TaskPlans do not confer authority.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from locus_runtime.gateway import BudgetFigures, Capabilities, GatewayDecision, GatewaySession, RiskClass
from locus_runtime.task_graph_contract import TaskNode, validate_task_plan
from locus_runtime.task_plan_compiler import CompiledTaskPlan

_FIELDS = ("actions", "tool_calls", "tokens", "wall_seconds", "spend_microunits")
_HELD = frozenset({"reserved", "admitting", "active", "uncertain", "revoked"})
_TERMINAL = frozenset({"closed", "canceled"})
_SCHEMA_VERSION = 1


class AdmissionDenied(PermissionError):
    """Admission lacks budget, authority, active lease or valid provenance."""


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    actions: int
    tool_calls: int
    tokens: int
    wall_seconds: int
    spend_microunits: int

    def __post_init__(self) -> None:
        if any(type(getattr(self, k)) is not int or getattr(self, k) < 0 for k in _FIELDS):
            raise ValueError("resource budgets require nonnegative integer units")

    def plus(self, other: ResourceBudget) -> ResourceBudget:
        return ResourceBudget(**{k: getattr(self, k) + getattr(other, k) for k in _FIELDS})

    def subtract(self, other: ResourceBudget) -> ResourceBudget:
        if any(getattr(other, k) > getattr(self, k) for k in _FIELDS):
            raise AdmissionDenied("usage exceeds reservation or total resource limit")
        return ResourceBudget(**{k: getattr(self, k) - getattr(other, k) for k in _FIELDS})

    @classmethod
    def zero(cls) -> ResourceBudget:
        # Wall time is also accounted as a nonnegative integer; the zero vector
        # represents an aggregate, not an admissible individual reservation.
        return cls._from_dict({k: 0 for k in _FIELDS})

    @classmethod
    def _from_dict(cls, values: dict[str, int]) -> ResourceBudget:
        obj = object.__new__(cls)
        for key in _FIELDS:
            object.__setattr__(obj, key, values[key])
        return obj

    def leq(self, other: ResourceBudget) -> bool:
        return all(getattr(self, k) <= getattr(other, k) for k in _FIELDS)


def _decode_budget(raw: str) -> ResourceBudget:
    obj = json.loads(raw)
    if not isinstance(obj, dict) or set(obj) != set(_FIELDS):
        raise AdmissionDenied("invalid durable budget record")
    values = {k: obj[k] for k in _FIELDS}
    if any(type(v) is not int or v < 0 for v in values.values()):
        raise AdmissionDenied("invalid durable budget units")
    return ResourceBudget._from_dict(values)


def _encode_budget(budget: ResourceBudget) -> str:
    return json.dumps(asdict(budget), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    node_id: str
    attempt_key: str
    pod_revision: str
    requested: ResourceBudget
    lease_seconds: int

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in
               (self.node_id, self.attempt_key, self.pod_revision)):
            raise ValueError("node, attempt and pod revision must be bounded and nonblank")
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 86400:
            raise ValueError("lease_seconds must be in 1..86400")
        if self.requested.wall_seconds == 0:
            raise ValueError("requested wall_seconds must be positive")
        if self.requested.tool_calls > self.requested.actions:
            raise ValueError("requested tool_calls cannot exceed actions")
        if self.requested.wall_seconds > self.lease_seconds:
            raise ValueError("requested execution duration exceeds lease")


@dataclass(frozen=True, slots=True)
class ResourceReservation:
    reservation_id: str
    parent_run_id: str
    node_id: str
    attempt_key: str
    plan_digest: str
    state: str
    requested: ResourceBudget
    used: ResourceBudget
    expires_at: int
    child_run_id: str | None


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    limits: ResourceBudget
    used: ResourceBudget
    held: ResourceBudget
    available: ResourceBudget
    version: int


class PodLease:
    """Trusted supervisor handle; only this adapter checks durable lease on use.

    Never give workers the raw GatewaySession, gateway object or this Python
    object's internals as a sandbox/security barrier; use a confined worker
    with a brokered action protocol in the execution increment.
    """

    def __init__(self, ledger: PodReservationLedger, reservation_id: str,
                 session: GatewaySession) -> None:
        self._ledger = ledger
        self._reservation_id = reservation_id
        self._session = session

    @property
    def reservation_id(self) -> str:
        return self._reservation_id

    @property
    def run_id(self) -> str:
        return self._session.caller.run_id

    def authorize(self, **kwargs: object) -> GatewayDecision:
        self._ledger.require_active(self._reservation_id)
        return self._session.authorize(**kwargs)

    def settle(self, total: ResourceBudget) -> ResourceReservation:
        record = self._ledger.settle(self._reservation_id, total)
        self._session.report_budget(BudgetFigures(
            tokens_used=total.tokens, max_tokens=record.requested.tokens,
            duration_used_seconds=total.wall_seconds,
            max_duration_seconds=record.requested.wall_seconds,
            cost_used_usd=total.spend_microunits / 1_000_000,
            max_cost_usd=record.requested.spend_microunits / 1_000_000,
        ))
        return record

    def close(self, *, total: ResourceBudget, evidence: str) -> ResourceReservation:
        result = self._ledger.finish(self._reservation_id, total, evidence=evidence)
        self._session.close()
        return result


class PodReservationLedger:
    """Single-device durable reservation authority; no automatic execution."""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = str(path)
        self._clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL)")
            version = db.execute("SELECT version FROM schema_meta").fetchone()
            if version and version["version"] != _SCHEMA_VERSION:
                raise RuntimeError("unsupported reservation-ledger schema; migration required")
            if version is None:
                db.execute("INSERT INTO schema_meta(version) VALUES (?)", (_SCHEMA_VERSION,))
            db.execute("""CREATE TABLE IF NOT EXISTS parent_budget (
                run_id TEXT PRIMARY KEY, principal TEXT NOT NULL, area TEXT NOT NULL,
                plan_digest TEXT NOT NULL, limits TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_attempt (
                id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES parent_budget(run_id),
                node_id TEXT NOT NULL, attempt_key TEXT NOT NULL, revision TEXT NOT NULL,
                fingerprint TEXT NOT NULL, plan_digest TEXT NOT NULL,
                requested TEXT NOT NULL, used TEXT NOT NULL,
                state TEXT NOT NULL, expires_at INTEGER NOT NULL, child_run_id TEXT,
                evidence TEXT, UNIQUE(parent_id, attempt_key)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS pod_attempt_parent ON pod_attempt(parent_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id TEXT NOT NULL, reservation_id TEXT, event TEXT NOT NULL,
                stamp INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
            )""")

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _tx(db: sqlite3.Connection) -> None:
        db.execute("BEGIN IMMEDIATE")

    def _event(self, db: sqlite3.Connection, parent: str, reservation: str | None,
               kind: str, note: str = "") -> None:
        db.execute("INSERT INTO pod_audit(parent_id,reservation_id,event,stamp,note) VALUES (?,?,?,?,?)",
                   (parent, reservation, kind, self._now(), note[:128]))

    @staticmethod
    def _authenticate(parent: GatewaySession) -> None:
        # Gateway owns the PEP and registration; do not mistake the model's
        # requested parent identity or its claimed capabilities for authority.
        if parent.gateway._authenticate(parent.caller) is None:
            raise AdmissionDenied("parent gateway identity is invalid or revoked")

    def register_parent(self, compiled: CompiledTaskPlan, parent: GatewaySession,
                        *, max_tool_calls: int) -> LedgerSnapshot:
        self._authenticate(parent)
        registered = parent.gateway._authenticate(parent.caller)
        if registered is None or not registered.can_delegate:
            raise AdmissionDenied("parent gateway session cannot delegate")
        trusted_caps = registered.capabilities
        if type(max_tool_calls) is not int or max_tool_calls <= 0:
            raise AdmissionDenied("parent tool-call limit must be finite")
        if trusted_caps.max_tool_calls > 0 and max_tool_calls > trusted_caps.max_tool_calls:
            raise AdmissionDenied("parent tool-call reservation exceeds gateway limit")
        validate_task_plan(compiled.plan)
        if compiled.plan.intent.requester_id != parent.caller.principal:
            raise AdmissionDenied("plan requester does not match authenticated principal")
        if not compiled.digest or len(compiled.digest) != 64:
            raise AdmissionDenied("unrecognized plan digest")
        budget = compiled.plan.parent_authority.budget
        limits = ResourceBudget(budget.actions, max_tool_calls, budget.tokens,
                                budget.wall_seconds, budget.spend_microunits)
        if limits.actions <= 0 or limits.tokens <= 0:
            raise AdmissionDenied("parent must have finite positive action and token limits")
        if trusted_caps.max_actions is not None and limits.actions > trusted_caps.max_actions:
            raise AdmissionDenied("parent plan widens gateway action limit")
        if trusted_caps.budget is not None:
            parent_budget = trusted_caps.budget
            if (parent_budget.max_tokens is not None
                and limits.tokens > parent_budget.max_tokens):
                raise AdmissionDenied("parent plan widens gateway token limit")
            if (parent_budget.max_duration_seconds is not None
                and limits.wall_seconds > parent_budget.max_duration_seconds):
                raise AdmissionDenied("parent plan widens gateway duration limit")
            if (parent_budget.max_cost_usd is not None
                and limits.spend_microunits / 1_000_000 > parent_budget.max_cost_usd):
                raise AdmissionDenied("parent plan widens gateway spend limit")
        with self._connect() as db:
            self._tx(db)
            existing = db.execute("SELECT * FROM parent_budget WHERE run_id=?",
                                  (parent.caller.run_id,)).fetchone()
            if existing is None:
                db.execute("""INSERT INTO parent_budget
                    (run_id,principal,area,plan_digest,limits) VALUES (?,?,?,?,?)""",
                    (parent.caller.run_id, parent.caller.principal, compiled.plan.intent.area,
                     compiled.digest, _encode_budget(limits)))
                self._event(db, parent.caller.run_id, None, "parent_registered")
            elif (existing["principal"], existing["area"], existing["plan_digest"],
                  existing["limits"]) != (
                parent.caller.principal, compiled.plan.intent.area, compiled.digest,
                _encode_budget(limits)
            ) or existing["revoked"]:
                raise AdmissionDenied("parent run identity, scope or plan revision changed")
        return self.snapshot(parent.caller.run_id)

    def _parent(self, db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        parent = db.execute("SELECT * FROM parent_budget WHERE run_id=?", (run_id,)).fetchone()
        if parent is None or parent["revoked"]:
            raise AdmissionDenied("unknown or revoked parent")
        return parent

    def _totals(self, db: sqlite3.Connection, run_id: str) -> tuple[ResourceBudget, ResourceBudget]:
        used = ResourceBudget.zero()
        held = ResourceBudget.zero()
        rows = db.execute("SELECT state,requested,used FROM pod_attempt WHERE parent_id=?",
                          (run_id,)).fetchall()
        for row in rows:
            request, consumed = _decode_budget(row["requested"]), _decode_budget(row["used"])
            used = used.plus(consumed)
            if row["state"] in _HELD:
                held = held.plus(request.subtract(consumed))
        return used, held

    def snapshot(self, run_id: str) -> LedgerSnapshot:
        with self._connect() as db:
            parent = db.execute("SELECT * FROM parent_budget WHERE run_id=?", (run_id,)).fetchone()
            if parent is None:
                raise AdmissionDenied("unknown parent")
            limit = _decode_budget(parent["limits"])
            used, held = self._totals(db, run_id)
            return LedgerSnapshot(limit, used, held, limit.subtract(used.plus(held)),
                                  int(parent["version"]))

    def _row(self, db: sqlite3.Connection, reservation_id: str) -> sqlite3.Row:
        record = db.execute("SELECT * FROM pod_attempt WHERE id=?", (reservation_id,)).fetchone()
        if record is None:
            raise AdmissionDenied("unknown reservation")
        return record

    @staticmethod
    def _as_reservation(row: sqlite3.Row) -> ResourceReservation:
        return ResourceReservation(row["id"], row["parent_id"], row["node_id"],
            row["attempt_key"], row["plan_digest"], row["state"],
            _decode_budget(row["requested"]), _decode_budget(row["used"]),
            row["expires_at"], row["child_run_id"])

    def get(self, reservation_id: str) -> ResourceReservation:
        with self._connect() as db:
            return self._as_reservation(self._row(db, reservation_id))

    def _verify_plan(self, compiled: CompiledTaskPlan, parent: GatewaySession,
                     request: ReservationRequest) -> TaskNode:
        self._authenticate(parent)
        validate_task_plan(compiled.plan)
        if compiled.plan.intent.requester_id != parent.caller.principal:
            raise AdmissionDenied("cross-principal plan")
        node = next((n for n in compiled.plan.nodes if n.node_id == request.node_id), None)
        if node is None:
            raise AdmissionDenied("node absent from accepted plan")
        nb = node.authority.budget
        if not request.requested.leq(ResourceBudget(nb.actions, nb.actions, nb.tokens,
                                                     nb.wall_seconds, nb.spend_microunits)):
            raise AdmissionDenied("node-specific reservation ceiling exceeded")
        if request.requested.actions == 0 or request.requested.tokens == 0:
            raise AdmissionDenied("pod requires positive action and token caps")
        if not node.authority.capabilities.issubset(parent.capabilities.allowed_tools):
            raise AdmissionDenied("plan node tools are not authorized by parent gateway")
        if RiskClass[node.authority.risk_ceiling] > (
            parent.capabilities.max_risk_class or RiskClass.R3
        ):
            raise AdmissionDenied("node risk exceeds gateway ceiling")
        return node

    def reserve(self, compiled: CompiledTaskPlan, parent: GatewaySession,
                request: ReservationRequest) -> ResourceReservation:
        self._verify_plan(compiled, parent, request)
        digest = hashlib.sha256(json.dumps({
            "plan": compiled.digest, "node": request.node_id, "rev": request.pod_revision,
            "requested": asdict(request.requested), "lease": request.lease_seconds,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        run_id = parent.caller.run_id
        with self._connect() as db:
            self._tx(db)
            state = self._parent(db, run_id)
            if state["principal"] != parent.caller.principal or state["plan_digest"] != compiled.digest:
                raise AdmissionDenied("parent plan binding changed")
            existing = db.execute("SELECT * FROM pod_attempt WHERE parent_id=? AND attempt_key=?",
                                  (run_id, request.attempt_key)).fetchone()
            if existing is not None:
                if existing["fingerprint"] != digest:
                    raise AdmissionDenied("idempotency collision with different request")
                return self._as_reservation(existing)
            used, held = self._totals(db, run_id)
            limit = _decode_budget(state["limits"])
            if not request.requested.leq(limit.subtract(used.plus(held))):
                raise AdmissionDenied("parent's available budget is exhausted")
            rid = secrets.token_urlsafe(24)
            db.execute("""INSERT INTO pod_attempt
                (id,parent_id,node_id,attempt_key,revision,fingerprint,plan_digest,requested,used,state,expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, run_id, request.node_id, request.attempt_key, request.pod_revision,
                 digest, compiled.digest, _encode_budget(request.requested),
                 _encode_budget(ResourceBudget.zero()), "reserved", self._now() + request.lease_seconds))
            db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?", (run_id,))
            self._event(db, run_id, rid, "reserved")
            return self._as_reservation(self._row(db, rid))

    def _transition(self, reservation_id: str, expected: frozenset[str], next_state: str,
                    *, note: str = "") -> ResourceReservation:
        with self._connect() as db:
            self._tx(db)
            row = self._row(db, reservation_id)
            if row["state"] not in expected:
                raise AdmissionDenied("reservation lifecycle transition refused")
            db.execute("UPDATE pod_attempt SET state=? WHERE id=?", (next_state, reservation_id))
            db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                       (row["parent_id"],))
            self._event(db, row["parent_id"], reservation_id, next_state, note)
            return self._as_reservation(self._row(db, reservation_id))

    def admit(self, compiled: CompiledTaskPlan, parent: GatewaySession,
              reservation_id: str, *, engine: str) -> PodLease:
        self._authenticate(parent)
        with self._connect() as db:
            self._tx(db)
            row = self._row(db, reservation_id)
            state = self._parent(db, parent.caller.run_id)
            if (row["parent_id"] != parent.caller.run_id
                or state["plan_digest"] != compiled.digest
                or row["state"] != "reserved"
                or row["expires_at"] <= self._now()):
                raise AdmissionDenied("reservation is not available to this parent/plan")
            node = next((n for n in compiled.plan.nodes if n.node_id == row["node_id"]), None)
            if node is None:
                raise AdmissionDenied("reserved node not present in plan")
            db.execute("UPDATE pod_attempt SET state='admitting' WHERE id=?", (reservation_id,))
            db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                       (row["parent_id"],))
            self._event(db, row["parent_id"], reservation_id, "admitting")
        request = _decode_budget(row["requested"])
        ceiling = RiskClass[node.authority.risk_ceiling]
        # Trusted code derives every child limit; the model does not supply an
        # authorization session, executable or unrestricted capability set.
        caps: Capabilities = replace(parent.capabilities,
            allowed_tools=frozenset(node.authority.capabilities),
            max_risk_class=ceiling,
            max_actions=request.actions,
            max_tool_calls=request.tool_calls,
            budget=BudgetFigures(tokens_used=0, max_tokens=request.tokens,
                duration_used_seconds=0, max_duration_seconds=request.wall_seconds,
                cost_used_usd=0, max_cost_usd=request.spend_microunits / 1_000_000))
        child: GatewaySession | None = None
        try:
            child = parent.gateway.open_child_session(parent,
                run_id=f"{parent.caller.run_id}:{row['node_id']}:{reservation_id[:12]}",
                engine=engine, capabilities=caps)
            with self._connect() as db:
                self._tx(db)
                current = self._row(db, reservation_id)
                self._parent(db, parent.caller.run_id)
                if current["state"] != "admitting" or current["expires_at"] <= self._now():
                    raise AdmissionDenied("admission changed while creating gateway session")
                db.execute("UPDATE pod_attempt SET state='active',child_run_id=? WHERE id=?",
                           (child.caller.run_id, reservation_id))
                db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                           (parent.caller.run_id,))
                self._event(db, parent.caller.run_id, reservation_id, "active")
            return PodLease(self, reservation_id, child)
        except Exception:
            if child is not None:
                child.close()
            # A definitively refused gateway child admission had no launched
            # session, so its hold may be released. A child created before an
            # ambiguous persistence failure retains its full hold for manual
            # reconciliation; do not blindly retry potentially started work.
            try:
                outcome = "canceled" if child is None else "uncertain"
                self._transition(reservation_id, frozenset({"admitting"}), outcome,
                                 note="gateway_refused" if child is None else "admission_failure_reconcile")
            except AdmissionDenied:
                pass
            raise

    def require_active(self, reservation_id: str) -> ResourceReservation:
        with self._connect() as db:
            row = self._row(db, reservation_id)
            self._parent(db, row["parent_id"])
            if row["state"] != "active" or row["expires_at"] <= self._now():
                raise AdmissionDenied("pod lease expired, revoked or not active")
            return self._as_reservation(row)

    def settle(self, reservation_id: str, cumulative: ResourceBudget) -> ResourceReservation:
        with self._connect() as db:
            self._tx(db)
            row = self._row(db, reservation_id)
            self._parent(db, row["parent_id"])
            if row["state"] != "active" or row["expires_at"] <= self._now():
                raise AdmissionDenied("only live admitted pods may settle usage")
            requested, old = _decode_budget(row["requested"]), _decode_budget(row["used"])
            if (not old.leq(cumulative) or not cumulative.leq(requested)
                or cumulative.tool_calls > cumulative.actions):
                raise AdmissionDenied("usage must be monotone and within reservation")
            db.execute("UPDATE pod_attempt SET used=? WHERE id=?", (_encode_budget(cumulative), reservation_id))
            db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                       (row["parent_id"],))
            self._event(db, row["parent_id"], reservation_id, "usage_settled")
            return self._as_reservation(self._row(db, reservation_id))

    def finish(self, reservation_id: str, total: ResourceBudget, *, evidence: str) -> ResourceReservation:
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 256:
            raise AdmissionDenied("final evidence/reconciliation record is required")
        with self._connect() as db:
            self._tx(db)
            row = self._row(db, reservation_id)
            if row["state"] not in ("active", "uncertain", "revoked"):
                raise AdmissionDenied("cannot finish an unexecuted or completed attempt")
            old, requested = _decode_budget(row["used"]), _decode_budget(row["requested"])
            if (not old.leq(total) or not total.leq(requested)
                or total.tool_calls > total.actions):
                raise AdmissionDenied("invalid final usage or double spending")
            db.execute("UPDATE pod_attempt SET state='closed',used=?,evidence=? WHERE id=?",
                       (_encode_budget(total), evidence, reservation_id))
            db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                       (row["parent_id"],))
            self._event(db, row["parent_id"], reservation_id, "closed", evidence)
            return self._as_reservation(self._row(db, reservation_id))

    def cancel_reserved(self, reservation_id: str) -> ResourceReservation:
        return self._transition(reservation_id, frozenset({"reserved"}), "canceled")

    def recover_expired(self) -> tuple[str, ...]:
        """Never release an expired active/admitting attempt automatically."""
        changed: list[str] = []
        with self._connect() as db:
            self._tx(db)
            rows = db.execute("""SELECT * FROM pod_attempt WHERE expires_at <= ?
                AND state IN ('reserved','admitting','active')""", (self._now(),)).fetchall()
            for row in rows:
                state = "canceled" if row["state"] == "reserved" else "uncertain"
                db.execute("UPDATE pod_attempt SET state=? WHERE id=?", (state, row["id"]))
                db.execute("UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                           (row["parent_id"],))
                self._event(db, row["parent_id"], row["id"], state, "lease_expired")
                changed.append(row["id"])
        return tuple(changed)

    def revoke_parent(self, parent: GatewaySession) -> None:
        self._authenticate(parent)
        with self._connect() as db:
            self._tx(db)
            record = self._parent(db, parent.caller.run_id)
            if record["principal"] != parent.caller.principal:
                raise AdmissionDenied("wrong parent identity")
            db.execute("UPDATE parent_budget SET revoked=1,version=version+1 WHERE run_id=?",
                       (parent.caller.run_id,))
            for row in db.execute("""SELECT * FROM pod_attempt WHERE parent_id=?
                AND state IN ('reserved','admitting','active')""", (parent.caller.run_id,)):
                state = "canceled" if row["state"] == "reserved" else "revoked"
                db.execute("UPDATE pod_attempt SET state=? WHERE id=?", (state, row["id"]))
                self._event(db, parent.caller.run_id, row["id"], state, "parent_revoked")
            self._event(db, parent.caller.run_id, None, "parent_revoked")
        parent.close()

    def audit_events(self, run_id: str) -> tuple[tuple[str, str, int], ...]:
        with self._connect() as db:
            return tuple((row["event"], row["reservation_id"] or "", row["stamp"])
                         for row in db.execute("""SELECT event,reservation_id,stamp FROM pod_audit
                           WHERE parent_id=? ORDER BY sequence""", (run_id,)))
