"""Durable, fail-closed dispatch of verified task DAG nodes (LOCUS-413).

This supervisor only selects work and binds attempts to the existing pod
reservation ledger. It is NOT a policy enforcement point, runner, model router,
or evidence verifier. A worker receives the PodLease created by the Gateway;
the supervisor never grants capabilities from the proposed graph itself.

An uncertain admission is deliberately non-replayable. Recovery requires
external reconciliation, not automatic retry of an effectful child.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Protocol

from locus_runtime.gateway import GatewaySession
from locus_runtime.pod_reservations import (
    AdmissionDenied,
    PodLease,
    PodReservationLedger,
    ReservationRequest,
    ResourceBudget,
    ResourceReservation,
)
from locus_runtime.task_graph_contract import validate_task_plan
from locus_runtime.task_plan_compiler import CompiledTaskPlan

_PROOF_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_BUSY = frozenset({"claimed", "reserved", "active", "uncertain"})
_MAX_UNSTARTED_RECLAIMS = 3


class IndependentVerifier(Protocol):
    """Injected trusted verifier. Never accept this callback from a pod or LLM."""

    def verify(
        self, *, parent_run_id: str, plan_digest: str, node_id: str,
        child_run_id: str, artifact_digest: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class NodeClaim:
    parent_run_id: str
    plan_digest: str
    node_id: str
    attempt_key: str
    state: str
    reservation_id: str | None = None


class DurableTaskScheduler:
    """Small durable dispatch bridge across TaskPlan and PodReservationLedger.

    All coordinator transitions are serialized by the reservation ledger's
    SQLite BEGIN IMMEDIATE lock; no alternate budget or authorization path is
    introduced. The scheduler DB tables live beside the existing pod tables.
    """

    def __init__(self, ledger: PodReservationLedger, *, max_inflight: int = 4) -> None:
        if type(max_inflight) is not int or not 1 <= max_inflight <= 64:
            raise ValueError("max_inflight must be an integer in 1..64")
        self.ledger = ledger
        self.max_inflight = max_inflight
        with ledger._connect() as db:
            ledger._tx(db)
            db.execute("""CREATE TABLE IF NOT EXISTS task_dispatch_parent (
                parent_run_id TEXT PRIMARY KEY,
                plan_digest TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                max_inflight INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS task_dispatch_node (
                parent_run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt_key TEXT,
                attempt_epoch INTEGER NOT NULL DEFAULT 0,
                reservation_id TEXT,
                artifact_digest TEXT,
                PRIMARY KEY (parent_run_id, node_id)
            )""")
            # Existing unmerged development DBs may predate the recovery field.
            # The schema change is additive and guarded by BEGIN IMMEDIATE.
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(task_dispatch_node)")
            }
            if "attempt_epoch" not in columns:
                db.execute(
                    "ALTER TABLE task_dispatch_node ADD COLUMN "
                    "attempt_epoch INTEGER NOT NULL DEFAULT 0"
                )

    @staticmethod
    def _fingerprint(plan: CompiledTaskPlan) -> str:
        """Independent canonical binding of every trusted intent/graph field."""
        obj = plan.plan
        body = {
            "version": obj.contract_version,
            "intent": asdict(obj.intent),
            "parent": {
                "capabilities": sorted(obj.parent_authority.capabilities),
                "risk": obj.parent_authority.risk_ceiling,
                "budget": asdict(obj.parent_authority.budget),
            },
            "nodes": [
                {
                    "id": node.node_id,
                    "depends_on": sorted(node.depends_on),
                    "capabilities": sorted(node.authority.capabilities),
                    "risk": node.authority.risk_ceiling,
                    "budget": asdict(node.authority.budget),
                    "deliverables": node.deliverables,
                    "criteria": node.acceptance_criteria,
                }
                for node in sorted(obj.nodes, key=lambda n: n.node_id)
            ],
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _check(
        self, db: sqlite3.Connection, plan: CompiledTaskPlan, parent: GatewaySession,
    ) -> dict[str, sqlite3.Row]:
        self.ledger._authenticate(parent)
        validate_task_plan(plan.plan)
        if len(plan.digest) != 64 or _PROOF_DIGEST.fullmatch(plan.digest) is None:
            raise AdmissionDenied("invalid accepted plan digest")
        run_id = parent.caller.run_id
        stored = self.ledger._parent(db, run_id)
        supervisor = db.execute(
            "SELECT * FROM task_dispatch_parent WHERE parent_run_id=?", (run_id,)
        ).fetchone()
        if (
            supervisor is None
            or supervisor["plan_digest"] != plan.digest
            or supervisor["graph_fingerprint"] != self._fingerprint(plan)
            or supervisor["max_inflight"] != self.max_inflight
        ):
            raise AdmissionDenied("scheduler graph revision or capacity changed")
        if (
            stored["principal"] != parent.caller.principal
            or stored["area"] != plan.plan.intent.area
            or stored["plan_digest"] != plan.digest
            or plan.plan.intent.requester_id != parent.caller.principal
        ):
            raise AdmissionDenied("scheduler parent plan/identity mismatch")
        rows = db.execute(
            "SELECT * FROM task_dispatch_node WHERE parent_run_id=?", (run_id,)
        ).fetchall()
        if {row["node_id"] for row in rows} != {n.node_id for n in plan.plan.nodes}:
            raise AdmissionDenied("task graph has not been bound to durable scheduler")
        fingerprint = self._fingerprint(plan)
        if any(
            row["plan_digest"] != plan.digest
            or row["graph_fingerprint"] != fingerprint
            for row in rows
        ):
            raise AdmissionDenied("scheduler graph revision changed")
        return {str(row["node_id"]): row for row in rows}

    @staticmethod
    def _key(run_id: str, digest: str, node_id: str, epoch: int = 0) -> str:
        if type(epoch) is not int or not 0 <= epoch <= _MAX_UNSTARTED_RECLAIMS:
            raise AdmissionDenied("invalid task attempt epoch")
        raw = f"{run_id}\x00{digest}\x00{node_id}".encode("utf-8")
        if epoch:
            raw += f"\x00unstarted-reclaim:{epoch}".encode("ascii")
        return "dag-" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _ready_nodes(plan: CompiledTaskPlan, rows: dict[str, sqlite3.Row]) -> tuple[str, ...]:
        verified = {key for key, value in rows.items() if value["state"] == "verified"}
        return tuple(sorted(
            node.node_id for node in plan.plan.nodes
            if rows[node.node_id]["state"] == "pending"
            and set(node.depends_on).issubset(verified)
        ))

    def bind(
        self, plan: CompiledTaskPlan, parent: GatewaySession, *, max_tool_calls: int,
    ) -> None:
        """Register the trusted parent once; resume only the identical plan."""
        self.ledger.register_parent(plan, parent, max_tool_calls=max_tool_calls)
        run_id = parent.caller.run_id
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            self.ledger._parent(db, run_id)
            fingerprint = self._fingerprint(plan)
            supervisor = db.execute(
                "SELECT * FROM task_dispatch_parent WHERE parent_run_id=?", (run_id,)
            ).fetchone()
            if supervisor is not None and (
                supervisor["plan_digest"] != plan.digest
                or supervisor["graph_fingerprint"] != fingerprint
                or supervisor["max_inflight"] != self.max_inflight
            ):
                raise AdmissionDenied("scheduler cannot widen durable concurrency")
            db.execute(
                "INSERT OR IGNORE INTO task_dispatch_parent "
                "(parent_run_id,plan_digest,graph_fingerprint,max_inflight) VALUES (?,?,?,?)",
                (run_id, plan.digest, fingerprint, self.max_inflight),
            )
            existing = db.execute(
                "SELECT node_id,plan_digest,graph_fingerprint FROM task_dispatch_node "
                "WHERE parent_run_id=?",
                (run_id,),
            ).fetchall()
            if existing and (
                len(existing) != len(plan.plan.nodes)
                or any(
                    row["plan_digest"] != plan.digest
                    or row["graph_fingerprint"] != fingerprint for row in existing
                )
                or {row["node_id"] for row in existing} != {n.node_id for n in plan.plan.nodes}
            ):
                raise AdmissionDenied("scheduler cannot silently replace a graph")
            for node in plan.plan.nodes:
                db.execute(
                    "INSERT OR IGNORE INTO task_dispatch_node "
                    "(parent_run_id,node_id,plan_digest,graph_fingerprint,state) "
                    "VALUES (?,?,?,?,'pending')",
                    (run_id, node.node_id, plan.digest, fingerprint),
                )

    def ready(
        self, plan: CompiledTaskPlan, parent: GatewaySession,
    ) -> tuple[str, ...]:
        with self.ledger._connect() as db:
            rows = self._check(db, plan, parent)
            return self._ready_nodes(plan, rows)

    def claim_next(
        self, plan: CompiledTaskPlan, parent: GatewaySession,
    ) -> NodeClaim | None:
        """Claim exactly one eligible node across concurrent supervisor processes."""
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            rows = self._check(db, plan, parent)
            if sum(row["state"] in _BUSY for row in rows.values()) >= self.max_inflight:
                return None
            ready = self._ready_nodes(plan, rows)
            if not ready:
                return None
            node_id = ready[0]
            key = self._key(
                parent.caller.run_id, plan.digest, node_id,
                int(rows[node_id]["attempt_epoch"]),
            )
            db.execute(
                "UPDATE task_dispatch_node SET state='claimed',attempt_key=? "
                "WHERE parent_run_id=? AND node_id=? AND state='pending'",
                (key, parent.caller.run_id, node_id),
            )
            return NodeClaim(parent.caller.run_id, plan.digest, node_id, key, "claimed")

    def recover_unstarted(
        self, plan: CompiledTaskPlan, parent: GatewaySession,
    ) -> tuple[NodeClaim, ...]:
        """Reconstruct safe claims and requeue definitively unexecuted holds.

        The existing idempotency key is reused when a claim is not known to
        have launched work. Every ledger lookup and scheduler transition
        occurs in one SQLite write transaction. Canceled *unadmitted* holds
        get a new bounded attempt epoch; active/uncertain/closed attempts
        remain non-replayable. Call ledger.recover_expired() first if needed.
        """
        resumable: list[NodeClaim] = []
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            rows = self._check(db, plan, parent)
            for node_id in sorted(rows):
                row = rows[node_id]
                if row["state"] not in {"claimed", "reserved"}:
                    continue
                epoch = row["attempt_epoch"]
                key = row["attempt_key"]
                if (
                    type(epoch) is not int
                    or not isinstance(key, str)
                    or key != self._key(parent.caller.run_id, plan.digest, node_id, epoch)
                ):
                    raise AdmissionDenied("corrupt durable task attempt identity")
                pod = db.execute(
                    "SELECT * FROM pod_attempt WHERE parent_id=? AND attempt_key=?",
                    (parent.caller.run_id, key),
                ).fetchone()
                bound_id = row["reservation_id"]
                if pod is None and row["state"] == "claimed" and bound_id is None:
                    # No reservation or Gateway child exists. Original
                    # supervisor and recovering supervisor share this key.
                    resumable.append(NodeClaim(
                        parent.caller.run_id, plan.digest, node_id, key, "claimed",
                    ))
                    continue
                if pod is not None and (
                    pod["node_id"] != node_id
                    or pod["plan_digest"] != plan.digest
                    or bound_id not in (None, pod["id"])
                ):
                    raise AdmissionDenied("reservation identity does not match graph")
                state = pod["state"] if pod is not None else "missing"
                if state == "reserved" and pod is not None:
                    # Crash between successful ledger.reserve and the
                    # scheduler's reservation state update: restore the hold.
                    db.execute(
                        "UPDATE task_dispatch_node SET state='reserved',reservation_id=? "
                        "WHERE parent_run_id=? AND node_id=?",
                        (pod["id"], parent.caller.run_id, node_id),
                    )
                    resumable.append(NodeClaim(
                        parent.caller.run_id, plan.digest, node_id, key,
                        "reserved", str(pod["id"]),
                    ))
                elif state == "canceled" and pod is not None and not pod["child_run_id"]:
                    # An expired reserved hold never admitted a child; the
                    # old idempotency key is terminal and must not be reused.
                    if epoch < _MAX_UNSTARTED_RECLAIMS:
                        db.execute(
                            "UPDATE task_dispatch_node SET state='pending',"
                            "attempt_key=NULL,reservation_id=NULL,attempt_epoch=? "
                            "WHERE parent_run_id=? AND node_id=?",
                            (epoch + 1, parent.caller.run_id, node_id),
                        )
                    else:
                        db.execute(
                            "UPDATE task_dispatch_node SET state='blocked' "
                            "WHERE parent_run_id=? AND node_id=?",
                            (parent.caller.run_id, node_id),
                        )
                else:
                    # Admitting, active, closed, revoked, unknown or corrupted
                    # attempts might already have caused effects. Never retry.
                    db.execute(
                        "UPDATE task_dispatch_node SET state='uncertain' "
                        "WHERE parent_run_id=? AND node_id=?",
                        (parent.caller.run_id, node_id),
                    )
        return tuple(resumable)

    def _require_claim(
        self, db: sqlite3.Connection, plan: CompiledTaskPlan,
        parent: GatewaySession, claim: NodeClaim, states: frozenset[str],
    ) -> sqlite3.Row:
        rows = self._check(db, plan, parent)
        if (
            claim.parent_run_id != parent.caller.run_id
            or claim.plan_digest != plan.digest
            or claim.node_id not in rows
        ):
            raise AdmissionDenied("foreign task claim")
        row = rows[claim.node_id]
        if row["attempt_key"] != claim.attempt_key or row["state"] not in states:
            raise AdmissionDenied("task claim replay or invalid state")
        return row

    def reserve(
        self, plan: CompiledTaskPlan, parent: GatewaySession, claim: NodeClaim,
        *, budget: ResourceBudget, lease_seconds: int, pod_revision: str = "v1",
    ) -> ResourceReservation:
        """Idempotent budget hold; a crash before recording it is retry-safe."""
        with self.ledger._connect() as db:
            self._require_claim(db, plan, parent, claim, frozenset({"claimed", "reserved"}))
        request = ReservationRequest(
            node_id=claim.node_id,
            attempt_key=claim.attempt_key,
            pod_revision=pod_revision,
            requested=budget,
            lease_seconds=lease_seconds,
        )
        reserved = self.ledger.reserve(plan, parent, request)
        if reserved.state != "reserved":
            raise AdmissionDenied("attempt is no longer reservable")
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            current = self._require_claim(
                db, plan, parent, claim, frozenset({"claimed", "reserved"})
            )
            if current["reservation_id"] not in (None, reserved.reservation_id):
                raise AdmissionDenied("task reservation changed")
            db.execute(
                "UPDATE task_dispatch_node SET state='reserved',reservation_id=? "
                "WHERE parent_run_id=? AND node_id=?",
                (reserved.reservation_id, parent.caller.run_id, claim.node_id),
            )
        return reserved

    def admit(
        self, plan: CompiledTaskPlan, parent: GatewaySession, claim: NodeClaim,
        *, engine: str,
    ) -> PodLease:
        """Create attenuated child session using the single existing Gateway PEP."""
        if not isinstance(engine, str) or not engine.strip() or len(engine) > 128:
            raise AdmissionDenied("invalid agent engine")
        with self.ledger._connect() as db:
            record = self._require_claim(
                db, plan, parent, claim, frozenset({"reserved"})
            )
            reservation_id = str(record["reservation_id"] or "")
            if not reservation_id:
                raise AdmissionDenied("task has no reservation")
        # If the process dies after Gateway admission but before the state
        # update, retry is denied by ledger.admit. NEVER reissue this attempt.
        lease = self.ledger.admit(plan, parent, reservation_id, engine=engine)
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            self._require_claim(db, plan, parent, claim, frozenset({"reserved"}))
            db.execute(
                "UPDATE task_dispatch_node SET state='active' "
                "WHERE parent_run_id=? AND node_id=?",
                (parent.caller.run_id, claim.node_id),
            )
        return lease

    def verify_closed(
        self, plan: CompiledTaskPlan, parent: GatewaySession, claim: NodeClaim,
        *, artifact_digest: str, verifier: IndependentVerifier,
    ) -> None:
        """Promote only a closed child with matching durable evidence and external verification.

        The caller supplies a *trusted, independent* verifier implementation.
        A child or model must never choose the verifier or assert its own pass.
        The opaque artifact digest is the same string passed to PodLease.close.
        """
        if not isinstance(artifact_digest, str) or _PROOF_DIGEST.fullmatch(artifact_digest) is None:
            raise AdmissionDenied("artifact digest must be SHA-256 hex")
        with self.ledger._connect() as db:
            row = self._require_claim(
                db, plan, parent, claim, frozenset({"active"})
            )
            reservation_id = str(row["reservation_id"] or "")
            pod = self.ledger._row(db, reservation_id)
            if (
                pod["state"] != "closed"
                or pod["parent_id"] != parent.caller.run_id
                or pod["node_id"] != claim.node_id
                or pod["plan_digest"] != plan.digest
                or pod["evidence"] != hashlib.sha256(artifact_digest.encode()).hexdigest()
                or not pod["child_run_id"]
            ):
                raise AdmissionDenied("closed pod evidence not bound to task")
            child_run_id = str(pod["child_run_id"])
        # Never call third-party verifier code while holding the SQLite write
        # lock; its result is revalidated before the durable promotion.
        try:
            accepted = verifier.verify(
                parent_run_id=parent.caller.run_id,
                plan_digest=plan.digest,
                node_id=claim.node_id,
                child_run_id=child_run_id,
                artifact_digest=artifact_digest,
            ) is True
        except Exception as exc:
            raise AdmissionDenied("independent verifier unavailable") from exc
        if not accepted:
            raise AdmissionDenied("independent verification rejected artifact")
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            self._require_claim(db, plan, parent, claim, frozenset({"active"}))
            pod = self.ledger._row(db, reservation_id)
            if (
                pod["state"] != "closed"
                or pod["evidence"] != hashlib.sha256(artifact_digest.encode()).hexdigest()
                or pod["child_run_id"] != child_run_id
            ):
                raise AdmissionDenied("closed pod changed during verification")
            db.execute(
                "UPDATE task_dispatch_node SET state='verified',artifact_digest=? "
                "WHERE parent_run_id=? AND node_id=?",
                (artifact_digest, parent.caller.run_id, claim.node_id),
            )

    def reconcile_interrupted(
        self, plan: CompiledTaskPlan, parent: GatewaySession,
    ) -> tuple[str, ...]:
        """Quarantine ambiguous admissions instead of silently replaying work."""
        quarantined: list[str] = []
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            rows = self._check(db, plan, parent)
            for node_id, row in rows.items():
                reservation_id = row["reservation_id"]
                if row["state"] not in {"reserved", "active"} or not reservation_id:
                    continue
                pod = self.ledger._row(db, str(reservation_id))
                if pod["state"] in {"admitting", "active", "uncertain", "revoked"} and (
                    row["state"] == "reserved" or pod["state"] != "active"
                ):
                    db.execute(
                        "UPDATE task_dispatch_node SET state='uncertain' "
                        "WHERE parent_run_id=? AND node_id=?",
                        (parent.caller.run_id, node_id),
                    )
                    quarantined.append(node_id)
        return tuple(sorted(quarantined))
