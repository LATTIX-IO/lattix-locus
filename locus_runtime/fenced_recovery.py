"""Exclusive, OS-fenced recovery of orphaned local pod action dispatches.

A lock-free timeout or stale heartbeat does NOT prove an action is dead. The
controller may mark a dispatch uncertain only after acquiring the same
kernel-held per-action lock that the trusted callback broker holds until
completion. This never replays, approves, or completes any external effect.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from locus_runtime.action_fencing import action_lock
from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.gateway import GatewaySession
from locus_runtime.pod_reservations import AdmissionDenied


@dataclass(frozen=True, slots=True)
class FencedRecoveryResult:
    action_id: str
    verdict: str  # uncertain | active | ineligible | already_terminal
    reason: str


class FencedActionRecovery:
    """Local-only trusted controller; no durable multi-host fencing claim."""

    def __init__(self, receipts: PodActionReceipts) -> None:
        self.receipts = receipts

    def _verify_supervisor(self, supervisor: GatewaySession) -> str:
        if not isinstance(supervisor, GatewaySession):
            raise AdmissionDenied("registered supervisor session required")
        reg = supervisor.gateway._authenticate(supervisor.caller)
        if reg is None or not reg.can_delegate:
            raise AdmissionDenied("supervisor is not authenticated for recovery")
        return hashlib.sha256(supervisor.caller.token.encode("utf-8")).hexdigest()

    def inspect(self, supervisor: GatewaySession, *, max_actions: int = 128) -> tuple[str, ...]:
        """List this exact supervisor's dispatches, including unsupported legacy ones."""
        token_digest = self._verify_supervisor(supervisor)
        if type(max_actions) is not int or not 1 <= max_actions <= 4096:
            raise ValueError("bounded recovery batch required")
        with self.receipts.reservations._connect() as db:
            rows = db.execute("""SELECT r.id FROM pod_action_receipt AS r
                JOIN pod_attempt AS pod ON r.reservation_id=pod.id
                WHERE pod.parent_id=? AND r.supervisor_token_digest=?
                  AND r.state='dispatching'
                ORDER BY r.id LIMIT ?""",
                (supervisor.caller.run_id, token_digest, max_actions)).fetchall()
            return tuple(row["id"] for row in rows)

    def recover(self, supervisor: GatewaySession,
                *, action_ids: Iterable[str] | None = None,
                max_actions: int = 128) -> tuple[FencedRecoveryResult, ...]:
        """Fenced state transition; never re-executes an action.

        Live callbacks retain their OS lock, so a concurrent recovery scan
        returns 'active'. Uninstrumented, historical dispatches are refused:
        they cannot prove they held this lock when a side effect occurred.
        """
        token_digest = self._verify_supervisor(supervisor)
        ids = tuple(action_ids) if action_ids is not None else self.inspect(
            supervisor, max_actions=max_actions
        )
        if len(ids) > max_actions or len(ids) != len(set(ids)):
            raise AdmissionDenied("invalid or oversized recovery batch")
        results: list[FencedRecoveryResult] = []
        for action_id in ids:
            if not isinstance(action_id, str) or not action_id or len(action_id) > 256:
                raise AdmissionDenied("invalid action identifier")
            # Authenticate and bind action BEFORE accessing a lock derived
            # from caller input; never disclose another tenant's liveness.
            with self.receipts.reservations._connect() as db:
                row = db.execute("""SELECT r.state,r.supervisor_token_digest,
                        pod.parent_id FROM pod_action_receipt AS r
                    JOIN pod_attempt AS pod ON pod.id=r.reservation_id
                    WHERE r.id=?""", (action_id,)).fetchone()
                if (row is None or row["parent_id"] != supervisor.caller.run_id
                    or row["supervisor_token_digest"] != token_digest):
                    raise AdmissionDenied("foreign action recovery refused")
            with action_lock(self.receipts.reservations.path, action_id) as owned:
                if not owned:
                    results.append(FencedRecoveryResult(action_id, "active", "os_lock_held"))
                    continue
                # Recheck authority and state AFTER acquiring the kernel lock,
                # atomically with update; a previous broker may have finished.
                with self.receipts.reservations._connect() as db:
                    self.receipts.reservations._tx(db)
                    row = db.execute("""SELECT r.state,r.supervisor_token_digest,
                            pod.parent_id,
                            EXISTS(SELECT 1 FROM pod_action_audit AS audit
                                WHERE audit.action_id=r.id
                                  AND audit.event='fence_guarded') AS guarded
                        FROM pod_action_receipt AS r
                        JOIN pod_attempt AS pod ON pod.id=r.reservation_id
                        WHERE r.id=?""", (action_id,)).fetchone()
                    if (row is None or row["parent_id"] != supervisor.caller.run_id
                        or row["supervisor_token_digest"] != token_digest):
                        raise AdmissionDenied("foreign action recovery refused")
                    if row["state"] != "dispatching":
                        results.append(FencedRecoveryResult(action_id,
                            "already_terminal", "no_current_dispatch"))
                    elif not row["guarded"]:
                        results.append(FencedRecoveryResult(action_id,
                            "ineligible", "legacy_dispatch_without_os_fence"))
                    else:
                        db.execute("""UPDATE pod_action_receipt SET state='uncertain'
                            WHERE id=? AND state='dispatching'""", (action_id,))
                        db.execute("""INSERT INTO pod_action_audit(action_id,event)
                            VALUES (?,?)""", (action_id, "fenced_uncertain"))
                        results.append(FencedRecoveryResult(action_id,
                            "uncertain", "exclusive_os_fence_verified"))
        return tuple(results)
