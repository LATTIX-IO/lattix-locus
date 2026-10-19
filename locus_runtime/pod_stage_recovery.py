"""LOCUS-466: operator-controlled, fail-closed recovery of orphaned Docker pods.

A fresh supervisor process can fence dead dispatchers using the existing OS
action lock, prove container origin from immutable ledger + Docker metadata,
remove *only* matching pods and leave receipt/reservation budgets uncertain.
Unknown/unavailable daemon state is NEVER interpreted as successful cleanup.
No staged files are inspected, deleted or promoted by the recovery controller.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import sys
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from locus_runtime.action_fencing import action_lock
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_bindings import PodStageBindings, _sha
from locus_runtime.pod_stage_docker import (
    _local_socket, _stage_proof, _trusted_local_binary,
)
from locus_runtime.pod_sandbox_broker import _require_content_addressed_image

logger = logging.getLogger(__name__)
_NAME = re.compile(r"locus-pod-[0-9a-f]{32}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_INSPECT = 262144
_Result = Literal["busy", "absent-observed", "removed", "needs-review"]


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    stage_id_sha256: str
    outcome: _Result
    # This is NOT proof that a delayed Docker RPC can never start a container.
    # Reservations always remain held for explicit human reconciliation.
    reservation_held: bool = True


def _private_ledger(path: Path) -> None:
    """Require a private supervisor-controlled ledger and directory on disk."""
    if sys.platform != "linux" or os.geteuid() == 0:
        raise AdmissionDenied("recovery requires an unprivileged Linux supervisor")
    if not path.is_absolute():
        raise AdmissionDenied("ledger path must be absolute")
    try:
        parent = path.parent.resolve(strict=True)
        owner = parent.stat()
        record = path.lstat()
    except OSError as exc:
        raise AdmissionDenied("durable pod ledger unavailable") from exc
    if (
        parent != path.parent
        or owner.st_uid != os.geteuid()
        or stat.S_IMODE(owner.st_mode) != 0o700
        or not stat.S_ISREG(record.st_mode)
        or record.st_uid != os.geteuid()
        or stat.S_IMODE(record.st_mode) & 0o022
    ):
        raise AdmissionDenied("pod ledger ownership or private parent invalid")


def _validate_origin(db: sqlite3.Connection, stage_id: str) -> sqlite3.Row:
    """Cross-check all identities from the same durable authoritative ledger."""
    row = db.execute(
        "SELECT l.*,s.id AS sid,s.reservation_id AS stage_reservation,"
        "s.binding_sha256,s.source_sha256,s.staged_path,s.state AS stage_state,"
        "s.action_id AS stage_action,s.parent_run_id,s.child_run_id,"
        "g.generation,g.binding_sha256 AS generation_binding,"
        "g.reservation_id AS generation_reservation,"
        "a.id AS action_present,a.reservation_id AS action_reservation,"
        "a.child_run_id AS action_child,a.state AS action_state,"
        "a.fingerprint AS action_fingerprint,a.gateway_audit_id,"
        "p.id AS pod_present,p.parent_id AS pod_parent,p.state AS pod_state "
        "FROM pod_stage_docker_launch l "
        "JOIN pod_stage_binding s ON s.id=l.stage_id "
        "JOIN pod_stage_exec_generation g ON g.stage_id=s.id "
        "JOIN pod_action_receipt a ON a.id=l.action_id "
        "JOIN pod_attempt p ON p.id=s.reservation_id "
        "WHERE l.stage_id=?", (stage_id,),
    ).fetchone()
    if row is None:
        raise AdmissionDenied("stage recovery provenance is incomplete")
    stage = db.execute(
        "SELECT * FROM pod_stage_binding WHERE id=?", (stage_id,),
    ).fetchone()
    if (
        row["stage_reservation"] != row["action_reservation"]
        or row["stage_reservation"] != row["generation_reservation"]
        or row["stage_reservation"] != row["pod_present"]
        or row["stage_action"] != row["action_id"]
        or row["child_run_id"] != row["action_child"]
        or row["parent_run_id"] != row["pod_parent"]
        or row["binding_sha256"] != row["generation_binding"]
        or not _HASH.fullmatch(row["binding_sha256"])
        or row["binding_sha256"] != PodStageBindings._fingerprint(stage)
    ):
        raise AdmissionDenied("recovery lineage no longer matches sealed StageLease")
    matching_audit = db.execute(
        "SELECT receipt_seal_sha256 FROM pod_stage_audit "
        "WHERE stage_id=? AND event='receipt-linked'", (stage_id,),
    ).fetchall()
    expected = _sha({
        "stage_id": stage_id, "action_id": row["action_id"],
        "action_fingerprint": row["action_fingerprint"],
        "gateway_audit_id": row["gateway_audit_id"],
    })
    if (len(matching_audit) != 1
        or matching_audit[0]["receipt_seal_sha256"] != expected):
        raise AdmissionDenied("recovery receipt integrity was substituted")
    if (
        not _NAME.fullmatch(row["container_name"])
        or not _HASH.fullmatch(row["launch_sha256"])
        or not _HASH.fullmatch(row["stage_proof"])
        or row["generation"] is None
    ):
        raise AdmissionDenied("recovery launch descriptor is invalid")
    _require_content_addressed_image(row["image_ref"])
    derived_proof = _stage_proof(
        stage_id=stage_id, binding=row["binding_sha256"],
        generation=row["generation"], reservation=row["stage_reservation"],
        name=row["container_name"], image=row["image_ref"],
    )
    if derived_proof != row["stage_proof"]:
        raise AdmissionDenied("Docker container proof does not match StageLease")
    if row["action_state"] not in {
        "prepared", "dispatching", "uncertain", "succeeded"
    } or row["state"] not in {
        "prepared", "dispatching", "uncertain", "finished"
    }:
        raise AdmissionDenied("unrecognized stage recovery lifecycle")
    return cast(sqlite3.Row, row)


def _docker(*, binary: str, socket: str, params: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    """Constrained host command; never inherit user Docker contexts or secrets."""
    with tempfile.TemporaryDirectory(prefix="locus-recovery-docker-") as config:
        try:
            completed = subprocess.run(
                [binary, "--host", socket, *params],
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True, check=False,
                timeout=8,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "HOME": config, "DOCKER_CONFIG": config,
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdmissionDenied("Docker recovery daemon response unavailable") from exc
    if len(completed.stdout) > _MAX_INSPECT or len(completed.stderr) > 8192:
        raise AdmissionDenied("Docker recovery response exceeded bound")
    return completed


def _inspect(binary: str, socket: str, name: str) -> dict[str, Any] | None:
    command = _docker(
        binary=binary, socket=socket,
        params=("container", "inspect", "--format", "{{json .}}", name),
    )
    if command.returncode:
        error = command.stderr.decode("utf-8", errors="replace")
        if command.returncode == 1 and (
            "No such object: " + name in error
            or "No such container: " + name in error
        ):
            return None
        raise AdmissionDenied("Docker container inspection failed or was ambiguous")
    try:
        obj = json.loads(command.stdout.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise AdmissionDenied("Docker container inspection is not canonical JSON") from exc
    if not isinstance(obj, dict):
        raise AdmissionDenied("Docker container inspection shape invalid")
    return obj


def _verify_container(obj: dict[str, Any], row: sqlite3.Row) -> str:
    """Prove exact name, daemon labels, image and stage-only OS boundary."""
    ident = obj.get("Id")
    if not isinstance(ident, str) or not _HASH.fullmatch(ident):
        raise AdmissionDenied("Docker container identity malformed")
    cfg, host = obj.get("Config"), obj.get("HostConfig")
    mounts = obj.get("Mounts")
    if not isinstance(cfg, dict) or not isinstance(host, dict) or not isinstance(mounts, list):
        raise AdmissionDenied("Docker containment metadata unavailable")
    labels = cfg.get("Labels")
    if (
        obj.get("Name") != "/" + row["container_name"]
        or not isinstance(labels, dict)
        or labels.get("io.lattix.locus.stage-only") != "v1"
        or labels.get("io.lattix.locus.stage-proof") != row["stage_proof"]
        or cfg.get("Image") != row["image_ref"]
        or host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or host.get("Binds") not in (None, [])
        or not isinstance(host.get("CapDrop"), list)
        or "ALL" not in [str(x).upper() for x in host["CapDrop"]]
        or not isinstance(host.get("SecurityOpt"), list)
        or "no-new-privileges" not in host["SecurityOpt"]
    ):
        raise AdmissionDenied("Docker recovery provenance or isolation mismatch")
    stage_seen = 0
    for mount in mounts:
        if not isinstance(mount, dict):
            raise AdmissionDenied("Docker mount evidence malformed")
        if (
            mount.get("Type") == "bind"
            and mount.get("Source") == row["staged_path"]
            and mount.get("Destination") == "/workspace"
            and mount.get("RW") is True
        ):
            stage_seen += 1
        elif mount.get("Type") == "tmpfs" and mount.get("Destination") == "/tmp":
            continue
        else:
            raise AdmissionDenied("Docker container has an unapproved mount")
    if stage_seen != 1:
        raise AdmissionDenied("staged-only Docker mount absent or ambiguous")
    return ident


def _audit_attempt(
    ledger: PodReservationLedger, stage_id: str, *,
    outcome: _Result,
) -> None:
    """Atomic, durable uncertain-state fencing; NEVER release held budgets."""
    with ledger._connect() as db:
        ledger._tx(db)
        row = _validate_origin(db, stage_id)
        if row["state"] == "finished" and row["action_state"] == "succeeded":
            return
        proof = _sha({
            "stage": stage_id, "action": row["action_id"],
            "binding": row["binding_sha256"],
            "launch": row["launch_sha256"], "outcome": outcome,
        })
        db.execute(
            "CREATE TABLE IF NOT EXISTS pod_stage_recovery_audit ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT,"
            "stage_id TEXT NOT NULL REFERENCES pod_stage_binding(id),"
            "outcome TEXT NOT NULL,proof_sha256 TEXT NOT NULL,stamp INTEGER NOT NULL)"
        )
        last = db.execute(
            "SELECT outcome,proof_sha256 FROM pod_stage_recovery_audit "
            "WHERE stage_id=? ORDER BY sequence DESC LIMIT 1",
            (stage_id,),
        ).fetchone()
        if last is None or (last["outcome"], last["proof_sha256"]) != (outcome, proof):
            db.execute(
                "INSERT INTO pod_stage_recovery_audit(stage_id,outcome,proof_sha256,stamp)"
                " VALUES (?,?,?,?)",
                (stage_id, outcome, proof, ledger._now()),
            )
        if row["action_state"] in {"prepared", "dispatching"}:
            db.execute(
                "UPDATE pod_action_receipt SET state='uncertain',outcome_digest=? "
                "WHERE id=? AND state=?",
                (proof, row["action_id"], row["action_state"]),
            )
            db.execute(
                "INSERT INTO pod_action_audit(action_id,event,note_digest) "
                "VALUES (?,'uncertain',?)", (row["action_id"], proof),
            )
        if row["state"] in {"prepared", "dispatching"}:
            db.execute(
                "UPDATE pod_stage_docker_launch SET state='uncertain',stamp=? "
                "WHERE stage_id=?", (ledger._now(), stage_id),
            )
        if row["pod_state"] == "active":
            # uncertain is a _HELD state; the full unspent reservation remains
            # charged to the parent until separate operator reconciliation.
            db.execute(
                "UPDATE pod_attempt SET state='uncertain' WHERE id=? AND state='active'",
                (row["stage_reservation"],),
            )
            db.execute(
                "UPDATE parent_budget SET version=version+1 WHERE run_id=?",
                (row["parent_run_id"],),
            )
            ledger._event(
                db, row["parent_run_id"], row["stage_reservation"],
                "uncertain", "docker_restart_reconciliation",
            )


class StageDockerRecovery:
    """Explicit trusted host recovery hook, invoked before resumed dispatch."""

    def __init__(
        self, ledger: PodReservationLedger, *,
        docker_path: Path = Path("/usr/bin/docker"),
        socket_path: Path = Path("/var/run/docker.sock"),
    ) -> None:
        if not isinstance(ledger, PodReservationLedger):
            raise AdmissionDenied("trusted durable ledger required")
        _private_ledger(Path(ledger.path))
        self.ledger = ledger
        self.docker_path = docker_path
        self.socket_path = socket_path

    def recover_pending(self) -> tuple[RecoveryReport, ...]:
        with self.ledger._connect() as db:
            try:
                rows = db.execute(
                    "SELECT l.stage_id,l.action_id FROM pod_stage_docker_launch l "
                    "JOIN pod_action_receipt a ON a.id=l.action_id "
                    "WHERE NOT (l.state='finished' AND a.state='succeeded') "
                    "ORDER BY l.stage_id",
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise AdmissionDenied("stage Docker ledger schema missing") from exc
        reports: list[RecoveryReport] = []
        for candidate in rows:
            stage_id, action_id = candidate["stage_id"], candidate["action_id"]
            hashed = hashlib.sha256(stage_id.encode("ascii")).hexdigest()
            with action_lock(self.ledger.path, action_id) as acquired:
                if not acquired:
                    reports.append(RecoveryReport(hashed, "busy"))
                    continue
                try:
                    with self.ledger._connect() as db:
                        row = _validate_origin(db, stage_id)
                        if row["state"] == "finished" and row["action_state"] == "succeeded":
                            continue
                        if row["socket_uri"] != _local_socket(self.socket_path):
                            raise AdmissionDenied("Docker recovery endpoint changed")
                        docker, _ = _trusted_local_binary(self.docker_path)
                        name, socket = row["container_name"], row["socket_uri"]
                        present = _inspect(docker, socket, name)
                        if present is None:
                            result: _Result = "absent-observed"
                        else:
                            container_id = _verify_container(present, row)
                            removed = _docker(
                                binary=docker, socket=socket,
                                params=("container", "rm", "--force", container_id),
                            )
                            if removed.returncode != 0 or _inspect(docker, socket, name) is not None:
                                raise AdmissionDenied("Docker orphan teardown not confirmed")
                            result = "removed"
                    _audit_attempt(self.ledger, stage_id, outcome=result)
                except (AdmissionDenied, OSError, ValueError, RuntimeError) as exc:
                    # Never return a positive cleanup report on uncertainty.
                    # Reconciliation still invalidates the action and pod so
                    # neither can be reused while daemon state is unknown.
                    logger.warning("stage Docker cleanup not independently verified: %s",
                                   type(exc).__qualname__)
                    try:
                        _audit_attempt(self.ledger, stage_id, outcome="needs-review")
                    except (AdmissionDenied, OSError, sqlite3.Error):
                        logger.error("stage Docker recovery ledger integrity failed")
                    result = "needs-review"
                reports.append(RecoveryReport(hashed, result))
        return tuple(reports)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile staged Docker pods before resuming Locus dispatch",
    )
    parser.add_argument("--ledger", required=True, help="private supervisor SQLite path")
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--socket", default="/var/run/docker.sock")
    parser.add_argument("--watch", action="store_true", help="continuous recovery guardian")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not 0.2 <= args.poll_seconds <= 30.0:
        return 2
    try:
        path = Path(args.ledger)
        _private_ledger(path)
        recovery = StageDockerRecovery(
            PodReservationLedger(path),
            docker_path=Path(args.docker), socket_path=Path(args.socket),
        )
        if args.watch:
            # This mode must be independently supervised by the host OS
            # (e.g. Restart=always). It continues to search for Docker
            # containers created after an earlier absent observation.
            last: tuple[RecoveryReport, ...] | None = None
            while True:
                current = recovery.recover_pending()
                if current != last:
                    print(json.dumps([
                        {"stage_sha256": r.stage_id_sha256, "outcome": r.outcome,
                         "reservation_held": r.reservation_held} for r in current
                    ], sort_keys=True), flush=True)
                    last = current
                time.sleep(args.poll_seconds)
        results = recovery.recover_pending()
    except KeyboardInterrupt:
        return 0
    except (AdmissionDenied, OSError, RuntimeError) as exc:
        logger.error("recovery refused: %s", type(exc).__qualname__)
        return 2
    # Only opaque hashes and enum states are emitted, never paths/commands.
    print(json.dumps([
        {"stage_sha256": r.stage_id_sha256, "outcome": r.outcome,
         "reservation_held": r.reservation_held} for r in results
    ], sort_keys=True))
    return 2 if any(r.outcome == "needs-review" for r in results) else (
        3 if any(r.outcome == "busy" for r in results) else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
