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
import re
import sqlite3
from dataclasses import dataclass
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
            db.execute("""CREATE TABLE IF NOT EXISTS task_dispatch_node (
                parent_run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt_key TEXT,
                reservation_id TEXT,
                artifact_digest TEXT,
                PRIMARY KEY (parent_run_id, node_id)
            )""")

    def _check(
        self, db: sqlite3.Connection, plan: CompiledTaskPlan, parent: GatewaySession,
    ) -> dict[str, sqlite3.Row]:
        self.ledger._authenticate(parent)
        validate_task_plan(plan.plan)
        if len(plan.digest) != 64 or _PROOF_DIGEST.fullmatch(plan.digest) is None:
            raise AdmissionDenied("invalid accepted plan digest")
        run_id = parent.caller.run_id
        stored = self.ledger._parent(db, run_id)
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
        if any(row["plan_digest"] != plan.digest for row in rows):
            raise AdmissionDenied("scheduler graph revision changed")
        return {str(row["node_id"]): row for row in rows}

    @staticmethod
    def _key(run_id: str, digest: str, node_id: str) -> str:
        raw = f"{run_id}\x00{digest}\x00{node_id}".encode("utf-8")
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
            existing = db.execute(
                "SELECT node_id,plan_digest FROM task_dispatch_node WHERE parent_run_id=?",
                (run_id,),
            ).fetchall()
            if existing and (
                len(existing) != len(plan.plan.nodes)
                or any(row["plan_digest"] != plan.digest for row in existing)
                or {row["node_id"] for row in existing} != {n.node_id for n in plan.plan.nodes}
            ):
                raise AdmissionDenied("scheduler cannot silently replace a graph")
            for node in plan.plan.nodes:
                db.execute(
                    "INSERT OR IGNORE INTO task_dispatch_node "
                    "(parent_run_id,node_id,plan_digest,state) VALUES (?,?,?,'pending')",
                    (run_id, node.node_id, plan.digest),
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
            key = self._key(parent.caller.run_id, plan.digest, node_id)
            db.execute(
                "UPDATE task_dispatch_node SET state='claimed',attempt_key=? "
                "WHERE parent_run_id=? AND node_id=? AND state='pending'",
                (key, parent.caller.run_id, node_id),
            )
            return NodeClaim(parent.caller.run_id, plan.digest, node_id, key, "claimed")

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
