"""LOCUS-465 A: durable StageLease identity for authenticated pod attempts.

Supervisor-only *provenance and fencing* contract. Does not launch Docker,
grant writable mounts, promote artifacts, or give a model filesystem authority.
All transitions share the existing reservation and receipt SQLite transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, PodReservationLedger
from locus_runtime.pod_workspace_staging import (
    WorkspaceSnapshot,
    _assert_parent_outside_workspace,
    _open_anchored_directory,
    _require_no_follow,
)

_VERSION = 2
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ENGINE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}\Z", re.ASCII)
_STAGE_PREFIX = ".locus-pod-stage-"
_STATES = frozenset({"bound", "receipt-linked", "retired"})


@dataclass(frozen=True, slots=True, repr=False)
class StageLease:
    """Audit-facing identifier; NEVER a bearer credential or mount permit."""

    stage_id: str
    reservation_id: str
    parent_run_id: str
    child_run_id: str
    node_id: str
    attempt_key: str
    plan_digest: str
    pod_revision: str
    engine_id: str
    graph_fingerprint: str
    engine_pins_sha256: str
    source_sha256: str
    capability_sha256: str
    binding_sha256: str
    source_path: str
    staged_path: str
    state: str
    action_id: str | None


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def _capability_sha256(lease: PodLease) -> str:
    """Fingerprint the trusted Gateway child envelope, not a caller claim."""
    caps = lease._session.capabilities
    if caps.write_roots:
        # Stage binding is metadata-only. No original host writable grants are
        # admitted here until a per-stage capability transition is implemented.
        raise AdmissionDenied("stage lease cannot grant writable host scopes")
    try:
        return _sha({
            "tools": sorted(caps.allowed_tools),
            "read_roots": list(caps.read_roots),
            "write_roots": list(caps.write_roots),
            "executables": list(caps.allowed_executables),
            "egress": list(caps.allowed_egress_hosts),
            "apps": list(caps.allowed_apps),
            "denied_apps": list(caps.denied_apps),
            "risk": caps.max_risk_class.name if caps.max_risk_class is not None else None,
            "max_actions": caps.max_actions,
            "max_tool_calls": caps.max_tool_calls,
            # Usage counters advance after PodLease.settle; only the immutable
            # *ceilings* define what the child is authorized to consume.
            "budget_limits": (
                {
                    "max_tokens": caps.budget.max_tokens,
                    "max_duration_seconds": caps.budget.max_duration_seconds,
                    "max_cost_usd": caps.budget.max_cost_usd,
                }
                if caps.budget is not None else None
            ),
            "autonomy": caps.autonomy_tier,
            "classification": caps.data_classification,
            "profile": caps.runtime_profile,
        })
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise AdmissionDenied("invalid child capability fingerprint") from exc


def _snapshot_identity(
    source: Path, stage: Path,
) -> tuple[tuple[int, int], tuple[int, int, int, int]]:
    """Pin root inodes and establish that stage is outside original source.

    Use O_NOFOLLOW *at every ancestor*, not just a preflight path comparison.
    The stage must be owned by this supervisor and owner-only at admission.
    """
    _require_no_follow()
    if (
        not source.is_absolute() or not stage.is_absolute()
        or len(str(source)) > 4096 or len(str(stage)) > 4096
        or not stage.name.startswith(_STAGE_PREFIX)
        or stage.is_relative_to(source)
    ):
        raise AdmissionDenied("invalid isolated stage pathname")
    source_fd: int | None = None
    parent_fd: int | None = None
    stage_fd: int | None = None
    try:
        source_fd = _open_anchored_directory(source)
        parent_fd = _open_anchored_directory(stage.parent)
        _assert_parent_outside_workspace(source_fd, parent_fd)
        stage_fd = _open_anchored_directory(stage)
        parent_entry = os.stat(stage.name, dir_fd=parent_fd, follow_symlinks=False)
        staging_root = os.fstat(stage_fd)
        source_root = os.fstat(source_fd)
        if (
            not stat.S_ISDIR(parent_entry.st_mode)
            or (parent_entry.st_dev, parent_entry.st_ino) !=
               (staging_root.st_dev, staging_root.st_ino)
            or staging_root.st_uid != os.getuid()
            or stat.S_IMODE(staging_root.st_mode) != 0o700
            or (source_root.st_dev, source_root.st_ino) ==
               (staging_root.st_dev, staging_root.st_ino)
        ):
            raise AdmissionDenied("stage is not a private isolated directory")
        return (
            (source_root.st_dev, source_root.st_ino),
            (staging_root.st_dev, staging_root.st_ino,
             staging_root.st_ctime_ns, staging_root.st_mtime_ns),
        )
    except (OSError, ValueError) as exc:
        raise AdmissionDenied("stage path cannot be independently verified") from exc
    finally:
        for fd in (stage_fd, parent_fd, source_fd):
            if fd is not None:
                os.close(fd)


class PodStageBindings:
    """Immutable attempt->snapshot mapping, with optional prepared receipt link.

    The pod reservation/receipt tables and StageLease rows share the same
    BEGIN IMMEDIATE transaction, so concurrent binders cannot substitute a
    different stage, engine, authenticated child or output scope.
    """

    def __init__(self, ledger: PodReservationLedger, receipts: PodActionReceipts) -> None:
        if (
            not isinstance(ledger, PodReservationLedger)
            or not isinstance(receipts, PodActionReceipts)
            or ledger.path != receipts.reservations.path
        ):
            raise AdmissionDenied("stage bindings require a shared pod receipt ledger")
        self.ledger = ledger
        self.receipts = receipts
        with ledger._connect() as db:
            ledger._tx(db)
            db.execute("CREATE TABLE IF NOT EXISTS pod_stage_schema (version INTEGER NOT NULL)")
            row = db.execute("SELECT version FROM pod_stage_schema").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO pod_stage_schema(version) VALUES (?)", (_VERSION,)
                )
            elif row["version"] == 1:
                # Forward-only additive migration. Old stages stay
                # non-executable until independently registered for V2.
                db.execute("UPDATE pod_stage_schema SET version=2 WHERE version=1")
            elif row["version"] != _VERSION:
                raise RuntimeError("unsupported stage-lease schema; migration required")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_stage_binding (
                id TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL UNIQUE REFERENCES pod_attempt(id),
                parent_run_id TEXT NOT NULL,
                child_run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                attempt_key TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                pod_revision TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                engine_pins_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                capability_sha256 TEXT NOT NULL,
                binding_sha256 TEXT NOT NULL,
                source_path TEXT NOT NULL,
                staged_path TEXT NOT NULL,
                source_dev INTEGER NOT NULL,
                source_ino INTEGER NOT NULL,
                stage_dev INTEGER NOT NULL,
                stage_ino INTEGER NOT NULL,
                stage_ctime_ns INTEGER NOT NULL,
                stage_mtime_ns INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('bound','receipt-linked','retired')),
                action_id TEXT UNIQUE REFERENCES pod_action_receipt(id),
                created_at INTEGER NOT NULL
            )""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS pod_stage_inode_lookup "
                "ON pod_stage_binding(stage_dev,stage_ino)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS pod_stage_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                stage_id TEXT NOT NULL REFERENCES pod_stage_binding(id),
                event TEXT NOT NULL,
                stamp INTEGER NOT NULL,
                receipt_seal_sha256 TEXT
            )""")
            # Supervisor-only SQLite birthtime witness. It is never inside
            # the child-writable staging mount and not a bearer permit.
            db.execute("""CREATE TABLE IF NOT EXISTS pod_stage_exec_generation (
                stage_id TEXT PRIMARY KEY REFERENCES pod_stage_binding(id),
                reservation_id TEXT NOT NULL,
                binding_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                staged_path TEXT NOT NULL,
                generation TEXT NOT NULL UNIQUE,
                parent_dev INTEGER NOT NULL,
                parent_ino INTEGER NOT NULL,
                stage_dev INTEGER NOT NULL,
                stage_ino INTEGER NOT NULL,
                birth_ns INTEGER NOT NULL,
                mount_id INTEGER NOT NULL,
                stage_uid INTEGER NOT NULL
            )""")

    @staticmethod
    def _record(row: sqlite3.Row) -> StageLease:
        return StageLease(
            stage_id=row["id"],
            reservation_id=row["reservation_id"],
            parent_run_id=row["parent_run_id"],
            child_run_id=row["child_run_id"],
            node_id=row["node_id"],
            attempt_key=row["attempt_key"],
            plan_digest=row["plan_digest"],
            pod_revision=row["pod_revision"],
            engine_id=row["engine_id"],
            graph_fingerprint=row["graph_fingerprint"],
            engine_pins_sha256=row["engine_pins_sha256"],
            source_sha256=row["source_sha256"],
            capability_sha256=row["capability_sha256"],
            binding_sha256=row["binding_sha256"],
            source_path=row["source_path"],
            staged_path=row["staged_path"],
            state=row["state"],
            action_id=row["action_id"],
        )

    def _active(
        self, db: sqlite3.Connection, lease: PodLease,
    ) -> sqlite3.Row:
        pod = self.receipts._active_locked(db, lease)
        parent = self.receipts._supervisor
        if parent is None or parent.gateway is not lease._session.gateway:
            raise AdmissionDenied("stage binding requires authenticated supervisor")
        trusted = parent.gateway._authenticate(parent.caller)
        if (
            trusted is None or not trusted.can_delegate
            or pod["parent_id"] != parent.caller.run_id
            or pod["parent_id"] == lease.run_id
            or pod["child_run_id"] != lease.run_id
            or parent.caller.principal != lease._session.caller.principal
        ):
            raise AdmissionDenied("stage binding has no trusted parent authority")
        return pod

    @staticmethod
    def _sealed_engine(
        db: sqlite3.Connection, pod: sqlite3.Row, lease: PodLease,
    ) -> tuple[str, str]:
        run_id, node_id = pod["parent_id"], pod["node_id"]
        parent = db.execute(
            "SELECT plan_digest,graph_fingerprint,engine_pins_json FROM task_dispatch_parent "
            "WHERE parent_run_id=?", (run_id,)
        ).fetchone()
        nodes = db.execute(
            "SELECT node_id,pinned_engine_id,plan_digest,graph_fingerprint "
            "FROM task_dispatch_node "
            "WHERE parent_run_id=?", (run_id,)
        ).fetchall()
        if (
            parent is None or parent["plan_digest"] != pod["plan_digest"]
            or not isinstance(parent["graph_fingerprint"], str)
            or _DIGEST.fullmatch(parent["graph_fingerprint"]) is None
            or parent["engine_pins_json"] is None or not nodes
        ):
            raise AdmissionDenied("stage binding requires sealed scheduler engine pins")
        try:
            pins = json.loads(parent["engine_pins_json"])
            if (
                not isinstance(pins, list)
                or any(not isinstance(pair, list) or len(pair) != 2 for pair in pins)
                or any(
                    not isinstance(node, str) or not isinstance(engine, str)
                    or _ENGINE.fullmatch(engine) is None
                    for node, engine in pins
                )
                or pins != sorted(pins)
            ):
                raise ValueError("invalid pins")
            mapping = dict(pins)
            if len(mapping) != len(pins):
                raise ValueError("duplicated pins")
        except (ValueError, TypeError) as exc:
            raise AdmissionDenied("scheduler engine pins are not canonical") from exc
        if (
            set(mapping) != {row["node_id"] for row in nodes}
            or any(
                row["pinned_engine_id"] != mapping.get(row["node_id"])
                or row["plan_digest"] != pod["plan_digest"]
                or row["graph_fingerprint"] != parent["graph_fingerprint"]
                for row in nodes
            )
            or mapping.get(node_id) != lease._session.caller.engine
        ):
            raise AdmissionDenied("stage engine binding differs from durable scheduler")
        current = next(row for row in nodes if row["node_id"] == node_id)
        # Check the attempt-specific edge, not merely the parent-wide pin set.
        bound = db.execute(
            "SELECT state,reservation_id,attempt_key FROM task_dispatch_node "
            "WHERE parent_run_id=? AND node_id=?", (run_id, node_id)
        ).fetchone()
        if (
            current["pinned_engine_id"] != lease._session.caller.engine
            or bound is None or bound["state"] != "active"
            or bound["reservation_id"] != pod["id"]
            or bound["attempt_key"] != pod["attempt_key"]
        ):
            raise AdmissionDenied("stage attempt is not the claimed engine-bound node")
        # Caller-supplied engine and self-consistent live DB rows are not an
        # immutable historical revision: retain both exact original hashes.
        encoded = parent["engine_pins_json"]
        canonical = json.dumps(pins, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True)
        if encoded != canonical:
            raise AdmissionDenied("durable scheduler engine pins were recoded")
        return str(parent["graph_fingerprint"]), hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()

    def _require(
        self, db: sqlite3.Connection, lease: PodLease, stage_id: str,
        *, allow_retired: bool = False, mutable_stage: bool = False,
    ) -> sqlite3.Row:
        pod = self._active(db, lease)
        graph_digest, pins_digest = self._sealed_engine(db, pod, lease)
        row = db.execute(
            "SELECT * FROM pod_stage_binding WHERE id=?", (stage_id,)
        ).fetchone()
        if (
            row is None
            or row["reservation_id"] != pod["id"]
            or row["parent_run_id"] != pod["parent_id"]
            or row["child_run_id"] != lease.run_id
            or row["node_id"] != pod["node_id"]
            or row["attempt_key"] != pod["attempt_key"]
            or row["pod_revision"] != pod["revision"]
            or row["plan_digest"] != pod["plan_digest"]
            or row["engine_id"] != lease._session.caller.engine
            or row["graph_fingerprint"] != graph_digest
            or row["engine_pins_sha256"] != pins_digest
            or row["capability_sha256"] != _capability_sha256(lease)
            or row["state"] not in _STATES
            or (row["state"] == "retired" and not allow_retired)
        ):
            raise AdmissionDenied("stage binding is stale, foreign or revoked")
        if (
            (row["state"] == "receipt-linked" and row["action_id"] is None)
            or (row["state"] == "bound" and row["action_id"] is not None)
        ):
            raise AdmissionDenied("stage receipt linkage integrity changed")
        if row["action_id"] is not None:
            action = db.execute(
                "SELECT reservation_id,child_run_id,supervisor_token_digest,"
                "fingerprint,gateway_audit_id "
                "FROM pod_action_receipt WHERE id=?", (row["action_id"],)
            ).fetchone()
            parent = self.receipts._supervisor
            if (
                action is None or parent is None
                or action["reservation_id"] != lease.reservation_id
                or action["child_run_id"] != lease.run_id
                or action["supervisor_token_digest"] != hashlib.sha256(
                    parent.caller.token.encode("utf-8")
                ).hexdigest()
            ):
                raise AdmissionDenied("stage prepared receipt linkage was substituted")
            first_seal = db.execute(
                "SELECT receipt_seal_sha256 FROM pod_stage_audit "
                "WHERE stage_id=? AND event='receipt-linked'", (stage_id,),
            ).fetchall()
            seal_digest = _sha({
                "stage_id": stage_id, "action_id": row["action_id"],
                "action_fingerprint": action["fingerprint"],
                "gateway_audit_id": action["gateway_audit_id"],
            })
            if (
                len(first_seal) != 1
                or first_seal[0]["receipt_seal_sha256"] != seal_digest
            ):
                raise AdmissionDenied("original stage receipt seal was substituted")
        else:
            if db.execute(
                "SELECT 1 FROM pod_stage_audit "
                "WHERE stage_id=? AND event='receipt-linked' LIMIT 1",
                (stage_id,),
            ).fetchone() is not None:
                raise AdmissionDenied("stage receipt audit disagrees with binding")
        (source_dev, source_ino), (stage_dev, stage_ino, ctime_ns, mtime_ns) = (
            _snapshot_identity(Path(row["source_path"]), Path(row["staged_path"]))
        )
        if (
            (source_dev, source_ino, stage_dev, stage_ino) !=
            (row["source_dev"], row["source_ino"], row["stage_dev"], row["stage_ino"])
            or (
                not mutable_stage and
                (ctime_ns, mtime_ns) != (row["stage_ctime_ns"], row["stage_mtime_ns"])
            )
        ):
            raise AdmissionDenied("stage workspace identity changed after binding")
        if mutable_stage:
            from locus_runtime.pod_stage_generation import require_generation

            require_generation(db, row)
        expected = self._fingerprint(row)
        if expected != row["binding_sha256"]:
            raise AdmissionDenied("stage durable binding integrity mismatch")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _fingerprint(row: sqlite3.Row | dict[str, Any]) -> str:
        return _sha({
            key: row[key] for key in (
                "reservation_id", "parent_run_id", "child_run_id", "node_id",
                "attempt_key", "plan_digest", "pod_revision", "engine_id",
                "graph_fingerprint", "engine_pins_sha256",
                "source_sha256", "capability_sha256", "source_path", "staged_path",
                "source_dev", "source_ino", "stage_dev", "stage_ino",
                "stage_ctime_ns", "stage_mtime_ns",
            )
        })

    def bind(self, lease: PodLease, snapshot: WorkspaceSnapshot) -> StageLease:
        """Seal only a trusted snapshot for one active, engine-pinned child."""
        if not isinstance(snapshot, WorkspaceSnapshot):
            raise AdmissionDenied("trusted workspace snapshot required")
        if (
            not isinstance(snapshot.sha256, str)
            or _DIGEST.fullmatch(snapshot.sha256) is None
            or type(snapshot.files) is not int or snapshot.files < 0
            or type(snapshot.bytes) is not int or snapshot.bytes < 0
            or not isinstance(snapshot.source, Path)
            or not isinstance(snapshot.staged, Path)
        ):
            raise AdmissionDenied("invalid source snapshot metadata")
        source, stage = snapshot.source, snapshot.staged
        (source_dev, source_ino), (stage_dev, stage_ino, ctime_ns, mtime_ns) = (
            _snapshot_identity(source, stage)
        )
        scope = _capability_sha256(lease)
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            pod = self._active(db, lease)
            graph_digest, pins_digest = self._sealed_engine(db, pod, lease)
            data: dict[str, Any] = {
                "reservation_id": pod["id"],
                "parent_run_id": pod["parent_id"],
                "child_run_id": lease.run_id,
                "node_id": pod["node_id"],
                "attempt_key": pod["attempt_key"],
                "plan_digest": pod["plan_digest"],
                "pod_revision": pod["revision"],
                "engine_id": lease._session.caller.engine,
                "graph_fingerprint": graph_digest,
                "engine_pins_sha256": pins_digest,
                "source_sha256": snapshot.sha256,
                "capability_sha256": scope,
                "source_path": str(source),
                "staged_path": str(stage),
                "source_dev": source_dev,
                "source_ino": source_ino,
                "stage_dev": stage_dev,
                "stage_ino": stage_ino,
                "stage_ctime_ns": ctime_ns,
                "stage_mtime_ns": mtime_ns,
            }
            binding_hash = self._fingerprint(data)
            previous = db.execute(
                "SELECT * FROM pod_stage_binding WHERE reservation_id=?",
                (lease.reservation_id,),
            ).fetchone()
            if previous is not None:
                if previous["binding_sha256"] != binding_hash:
                    raise AdmissionDenied("stage lease cannot replace an existing binding")
                return self._record(self._require(db, lease, previous["id"]))
            # Prevent a different child from claiming the same mutable stage.
            # Device/inode values can be recycled by the filesystem after
            # a prior stage is deleted. Refuse only a still-existing live
            # directory with this identity, including another textual alias.
            # Historical tombstones must not permanently blacklist new stages.
            reused = db.execute(
                "SELECT staged_path FROM pod_stage_binding "
                "WHERE stage_dev=? AND stage_ino=?",
                (stage_dev, stage_ino),
            ).fetchall()
            for previous_stage in reused:
                old_fd: int | None = None
                old_path = Path(previous_stage["staged_path"])
                try:
                    # Do not confuse missing with the staging reader's
                    # generic O_NOFOLLOW permission denial.
                    os.lstat(old_path)
                except FileNotFoundError:
                    # Proven absent tombstone: filesystem may reuse its inode.
                    continue
                except OSError as exc:
                    raise AdmissionDenied("historical stage identity is unavailable") from exc
                try:
                    old_fd = _open_anchored_directory(old_path)
                    old_stat = os.fstat(old_fd)
                    if (old_stat.st_dev, old_stat.st_ino) == (stage_dev, stage_ino):
                        raise AdmissionDenied("stage directory already bound to a pod")
                except PermissionError as exc:
                    if isinstance(exc, AdmissionDenied):
                        raise
                    # Ambiguously present or inaccessible: fail closed.
                    raise AdmissionDenied("historical stage identity is inaccessible") from exc
                finally:
                    if old_fd is not None:
                        os.close(old_fd)
            stage_id = secrets.token_urlsafe(24)
            db.execute(
                "INSERT INTO pod_stage_binding "
                "(id,reservation_id,parent_run_id,child_run_id,node_id,attempt_key,"
                "plan_digest,pod_revision,engine_id,graph_fingerprint,"
                "engine_pins_sha256,source_sha256,capability_sha256,"
                "binding_sha256,source_path,staged_path,source_dev,source_ino,"
                "stage_dev,stage_ino,stage_ctime_ns,stage_mtime_ns,state,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'bound',?)",
                (
                    stage_id, data["reservation_id"], data["parent_run_id"],
                    data["child_run_id"], data["node_id"], data["attempt_key"],
                    data["plan_digest"], data["pod_revision"], data["engine_id"],
                    graph_digest, pins_digest, data["source_sha256"],
                    data["capability_sha256"],
                    binding_hash, data["source_path"], data["staged_path"],
                    source_dev, source_ino, stage_dev, stage_ino,
                    ctime_ns, mtime_ns, self.ledger._now(),
                ),
            )
            db.execute(
                "INSERT INTO pod_stage_audit(stage_id,event,stamp) VALUES (?,?,?)",
                (stage_id, "bound", self.ledger._now()),
            )
            return self._record(self._require(db, lease, stage_id))

    def inspect(self, lease: PodLease, stage_id: str) -> StageLease:
        """Re-attest stage identity; a database record is NOT a mount grant."""
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            return self._record(self._require(db, lease, stage_id))

    def make_executable(self, lease: PodLease, stage_id: str) -> str:
        """Seal an immutable Linux inode-birth witness outside the stage.

        Only a still-pristine, V1-fresh authenticated stage can be registered.
        This does NOT grant a mount, prepare a receipt or launch a process.
        """
        from locus_runtime.pod_stage_generation import stage_witness

        with self.ledger._connect() as db:
            self.ledger._tx(db)
            stage = self._require(db, lease, stage_id)
            if stage["state"] != "bound" or stage["action_id"] is not None:
                raise AdmissionDenied("executable stage must precede receipt binding")
            witness = stage_witness(Path(stage["staged_path"]))
            if (witness[2], witness[3]) != (stage["stage_dev"], stage["stage_ino"]):
                raise AdmissionDenied("executable stage root was substituted")
            previous = db.execute(
                "SELECT generation FROM pod_stage_exec_generation WHERE stage_id=?",
                (stage_id,),
            ).fetchone()
            if previous is not None:
                return str(previous["generation"])
            generation = secrets.token_hex(32)
            db.execute(
                "INSERT INTO pod_stage_exec_generation "
                "(stage_id,reservation_id,binding_sha256,source_sha256,staged_path,"
                "generation,parent_dev,parent_ino,stage_dev,stage_ino,birth_ns,"
                "mount_id,stage_uid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    stage_id, stage["reservation_id"], stage["binding_sha256"],
                    stage["source_sha256"], stage["staged_path"], generation,
                    *witness,
                ),
            )
            db.execute(
                "INSERT INTO pod_stage_audit(stage_id,event,stamp) VALUES (?,?,?)",
                (stage_id, "executable-generation", self.ledger._now()),
            )
            return generation

    def inspect_executable(self, lease: PodLease, stage_id: str) -> StageLease:
        """V2 attestation remains valid after a child modifies stage files."""
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            return self._record(
                self._require(db, lease, stage_id, mutable_stage=True)
            )

    def link_prepared_receipt(
        self, lease: PodLease, stage_id: str, action_id: str,
    ) -> StageLease:
        """One-use exact receipt association, before dispatch, never a launch."""
        if not isinstance(action_id, str) or len(action_id) > 128 or not action_id:
            raise AdmissionDenied("prepared action receipt identifier required")
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            stage = self._require(db, lease, stage_id)
            action = db.execute(
                "SELECT * FROM pod_action_receipt WHERE id=?", (action_id,)
            ).fetchone()
            supervisor = self.receipts._supervisor
            if (
                action is None or action["reservation_id"] != lease.reservation_id
                or action["child_run_id"] != lease.run_id
                or action["state"] != "prepared"
                or supervisor is None
                or action["supervisor_token_digest"] != hashlib.sha256(
                    supervisor.caller.token.encode("utf-8")
                ).hexdigest()
            ):
                raise AdmissionDenied("prepared action does not belong to stage pod")
            if stage["state"] == "receipt-linked":
                if stage["action_id"] != action_id:
                    raise AdmissionDenied("stage has a different prepared receipt")
                return self._record(stage)
            if stage["state"] != "bound":
                raise AdmissionDenied("stage is not eligible for receipt binding")
            db.execute(
                "UPDATE pod_stage_binding SET state='receipt-linked',action_id=? "
                "WHERE id=? AND state='bound'",
                (action_id, stage_id),
            )
            seal_digest = _sha({
                "stage_id": stage_id, "action_id": action_id,
                "action_fingerprint": action["fingerprint"],
                "gateway_audit_id": action["gateway_audit_id"],
            })
            db.execute(
                "INSERT INTO pod_stage_audit"
                "(stage_id,event,stamp,receipt_seal_sha256) VALUES (?,?,?,?)",
                (stage_id, "receipt-linked", self.ledger._now(), seal_digest),
            )
            return self._record(self._require(db, lease, stage_id))

    def retire(self, lease: PodLease, stage_id: str) -> StageLease:
        """Mark stage non-reusable; does NOT remove or promote any files."""
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            executable = db.execute(
                "SELECT 1 FROM pod_stage_exec_generation WHERE stage_id=?",
                (stage_id,),
            ).fetchone() is not None
            stage = self._require(
                db, lease, stage_id, allow_retired=True,
                mutable_stage=executable,
            )
            if stage["state"] != "retired":
                db.execute(
                    "UPDATE pod_stage_binding SET state='retired' WHERE id=?",
                    (stage_id,),
                )
                db.execute(
                    "INSERT INTO pod_stage_audit(stage_id,event,stamp) VALUES (?,?,?)",
                    (stage_id, "retired", self.ledger._now()),
                )
            return self._record(
                db.execute("SELECT * FROM pod_stage_binding WHERE id=?", (stage_id,)).fetchone()
            )

    def audit(self, lease: PodLease, stage_id: str) -> tuple[str, ...]:
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            executable = db.execute(
                "SELECT 1 FROM pod_stage_exec_generation WHERE stage_id=?",
                (stage_id,),
            ).fetchone() is not None
            self._require(
                db, lease, stage_id, allow_retired=True, mutable_stage=executable,
            )
            return tuple(
                row["event"] for row in db.execute(
                    "SELECT event FROM pod_stage_audit WHERE stage_id=? ORDER BY sequence",
                    (stage_id,),
                )
            )
