"""LOCUS-469: independent terminal artifact escrow after the child lease closes.

Sealing occurs ONLY while the authenticated StageLease is live and the exact
Docker action succeeded. Review and approval later require only supervisor
WAL evidence, an independently copied inode in the private escrow root, and
a fresh, separately authenticated operator Gateway session. Neither the
agent stage nor the original workspace is writable by the child during review.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from locus_runtime.action_fencing import action_lock
from locus_runtime.gateway import GatewaySession, REASON_APPROVED, mask_secret_diff
from locus_runtime.pod_artifact_promotion import (
    _deny_source_submounts, _parent_fd, _read_file, _relative, _write_new,
)
from locus_runtime.pod_artifact_verification import (
    VerifiedArtifactManifest, verify_staged_artifacts,
)
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_workspace_staging import (
    _open_anchored_directory, staged_workspace,
)

_MAX_BYTES = 8 * 1024 * 1024
_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class EscrowRecord:
    escrow_id: str
    stage_id: str
    parent_run_id: str
    child_run_id: str
    manifest_sha256: str
    path: str
    payload_sha256: str
    state: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class EscrowPreview:
    escrow: EscrowRecord
    source_sha256: str
    before_sha256: str | None
    after_sha256: str
    before_mode: int | None
    after_mode: int
    diff: str
    truncated: bool


def _canonical_manifest(manifest: VerifiedArtifactManifest) -> str:
    fields = asdict(manifest)
    supplied = fields.pop("manifest_sha256", None)
    canonical = json.dumps(
        fields, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    )
    if hashlib.sha256(canonical.encode("ascii")).hexdigest() != supplied:
        raise AdmissionDenied("artifact verifier digest no longer matches manifest")
    if manifest.exit_code != 0 or len(manifest.changes) != 1:
        raise AdmissionDenied("detached V1 requires one successful changed artifact")
    return canonical


def _path_private(directory: Path) -> int:
    if not directory.is_absolute():
        raise AdmissionDenied("escrow directory must be absolute")
    fd = _open_anchored_directory(directory)
    metadata = os.fstat(fd)
    if (
        metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        os.close(fd)
        raise AdmissionDenied("escrow directory must be owner-private mode 0700")
    return fd


def _mode_root(ledger_path: str) -> Path:
    # Escrow root must be a supervisor-owned sibling of the WAL ledger,
    # outside any writable stage mount and outside the original source.
    root = Path(ledger_path).absolute().parent / ".locus-artifact-escrow"
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    with os.scandir(root.parent) as scanned:
        if not any(entry.name == root.name and not entry.is_symlink() for entry in scanned):
            raise AdmissionDenied("escrow root replaced by a symlink")
    fd = _path_private(root)
    os.close(fd)
    return root


def _source_integrity(source: str, expected_sha256: str, scratch: Path) -> None:
    with staged_workspace(source, staging_parent=scratch) as snapshot:
        if snapshot.sha256 != expected_sha256:
            raise AdmissionDenied("original workspace changed since detached seal")


class DetachedArtifactEscrow:
    """Supervisor-owned, versioned escrow with delayed human review."""

    def __init__(self, stages: PodStageBindings) -> None:
        if sys.platform != "linux" or not isinstance(stages, PodStageBindings):
            raise AdmissionDenied("detached escrow requires Linux supervisor authority")
        self.stages = stages
        self.ledger = stages.ledger
        self.root = _mode_root(self.ledger.path)
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            db.execute("""CREATE TABLE IF NOT EXISTS pod_artifact_escrow_schema (
                version INTEGER NOT NULL
            )""")
            ver = db.execute("SELECT version FROM pod_artifact_escrow_schema").fetchone()
            if ver is None:
                db.execute("INSERT INTO pod_artifact_escrow_schema VALUES (?)", (_SCHEMA,))
            elif ver["version"] != _SCHEMA:
                raise AdmissionDenied("unsupported detached artifact escrow schema")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_artifact_escrow (
                id TEXT PRIMARY KEY,
                stage_id TEXT NOT NULL UNIQUE REFERENCES pod_stage_binding(id),
                parent_run_id TEXT NOT NULL,
                child_run_id TEXT NOT NULL,
                parent_principal TEXT NOT NULL,
                parent_area TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                before_sha256 TEXT,
                before_mode INTEGER,
                before_dev INTEGER,
                before_ino INTEGER,
                after_sha256 TEXT NOT NULL,
                after_mode INTEGER NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                escrow_root_dev INTEGER NOT NULL,
                escrow_root_ino INTEGER NOT NULL,
                payload_dev INTEGER,
                payload_ino INTEGER,
                expires_at INTEGER NOT NULL,
                approval_expires_at INTEGER,
                approver_principal TEXT,
                gateway_audit_id TEXT,
                prepared_dev INTEGER,
                prepared_ino INTEGER,
                state TEXT NOT NULL CHECK(state IN (
                    'sealing','ready','approved','promoting','promoted',
                    'rolled_back','uncertain','expired'
                )),
                stamp INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_artifact_escrow_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                escrow_id TEXT NOT NULL REFERENCES pod_artifact_escrow(id),
                event TEXT NOT NULL,
                stamp INTEGER NOT NULL
            )""")

    def _row(self, escrow_id: str) -> sqlite3.Row:
        if (
            not isinstance(escrow_id, str)
            or len(escrow_id) != 40
            or any(c not in "0123456789abcdef" for c in escrow_id)
        ):
            raise AdmissionDenied("invalid detached escrow identifier")
        with self.ledger._connect() as db:
            row = db.execute(
                "SELECT * FROM pod_artifact_escrow WHERE id=?", (escrow_id,),
            ).fetchone()
        if row is None:
            raise AdmissionDenied("unknown detached artifact escrow")
        return cast(sqlite3.Row, row)

    def _record(self, row: sqlite3.Row) -> EscrowRecord:
        return EscrowRecord(
            str(row["id"]), str(row["stage_id"]),
            str(row["parent_run_id"]), str(row["child_run_id"]),
            str(row["manifest_sha256"]), str(row["relative_path"]),
            str(row["after_sha256"]), str(row["state"]), int(row["expires_at"]),
        )

    def _event(self, db: sqlite3.Connection, escrow_id: str, event: str) -> None:
        db.execute(
            "INSERT INTO pod_artifact_escrow_audit(escrow_id,event,stamp)"
            " VALUES (?,?,?)", (escrow_id, event, self.ledger._now()),
        )

    def _transition(self, escrow_id: str, before: str, after: str) -> None:
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            result = db.execute(
                "UPDATE pod_artifact_escrow SET state=?,stamp=? "
                "WHERE id=? AND state=?",
                (after, self.ledger._now(), escrow_id, before),
            )
            if result.rowcount != 1:
                raise AdmissionDenied("detached escrow state changed or replayed")
            self._event(db, escrow_id, after)

    def _payload(self, row: sqlite3.Row) -> tuple[bytes, str, int, int, int]:
        root_fd = _path_private(self.root)
        try:
            root_meta = os.fstat(root_fd)
            if (root_meta.st_dev, root_meta.st_ino) != (
                row["escrow_root_dev"], row["escrow_root_ino"],
            ):
                raise AdmissionDenied("escrow root inode changed")
            directory = os.open(
                str(row["id"]), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            try:
                info = os.fstat(directory)
                if (
                    info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                ):
                    raise AdmissionDenied("escrow attempt permissions changed")
                payload = _read_file(directory, "artifact")
                if (
                    payload is None
                    or payload[1] != row["after_sha256"]
                    or payload[2] != 0o400
                    or (payload[3], payload[4]) != (
                        row["payload_dev"], row["payload_ino"],
                    )
                ):
                    raise AdmissionDenied("escrow artifact inode or digest changed")
                return payload
            finally:
                os.close(directory)
        finally:
            os.close(root_fd)

    def _authorize_operator(
        self, operator: GatewaySession, row: sqlite3.Row,
    ) -> None:
        if not isinstance(operator, GatewaySession):
            raise AdmissionDenied("fresh trusted operator session required")
        registered = operator.gateway._authenticate(operator.caller)
        if (
            registered is None
            or not registered.can_delegate
            or operator.caller.principal != row["parent_principal"]
            or operator.caller.run_id == row["child_run_id"]
        ):
            raise AdmissionDenied("operator not authorized for escrow lineage")
        with self.ledger._connect() as db:
            parent = db.execute(
                "SELECT principal,area,plan_digest,revoked FROM parent_budget "
                "WHERE run_id=?", (row["parent_run_id"],),
            ).fetchone()
            stage = db.execute(
                "SELECT parent_run_id,child_run_id,plan_digest,"
                "source_sha256,binding_sha256 FROM pod_stage_binding "
                "WHERE id=?", (row["stage_id"],),
            ).fetchone()
        manifest = json.loads(str(row["manifest_json"]))
        if (
            parent is None or stage is None
            or parent["revoked"] or parent["principal"] != row["parent_principal"]
            or parent["area"] != row["parent_area"]
            or parent["plan_digest"] != manifest["plan_digest"]
            or stage["parent_run_id"] != row["parent_run_id"]
            or stage["child_run_id"] != row["child_run_id"]
            or stage["plan_digest"] != manifest["plan_digest"]
            or stage["source_sha256"] != row["source_sha256"]
            or stage["binding_sha256"] != manifest["binding_sha256"]
        ):
            raise AdmissionDenied("detached escrow parent identity revoked or changed")

    def _inspect(self, row: sqlite3.Row, *, eligible: tuple[str, ...]) -> None:
        if row["state"] not in eligible or row["expires_at"] <= self.ledger._now():
            raise AdmissionDenied("detached escrow not eligible or expired")
        canonical = str(row["manifest_json"])
        try:
            parsed = json.loads(canonical)
        except (ValueError, TypeError) as exc:
            raise AdmissionDenied("detached escrow manifest invalid") from exc
        if (
            json.dumps(parsed, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) != canonical
            or hashlib.sha256(canonical.encode("ascii")).hexdigest()
            != row["manifest_sha256"]
            or len(parsed["changes"]) != 1
            or parsed["changes"][0]["path"] != row["relative_path"]
            or parsed["changes"][0]["after_sha256"] != row["after_sha256"]
            or parsed["source_sha256"] != row["source_sha256"]
            or parsed["stage_id"] != row["stage_id"]
            or parsed["child_run_id"] != row["child_run_id"]
            or parsed["exit_code"] != 0
        ):
            raise AdmissionDenied("detached manifest evidence tampered")
        _relative(str(row["relative_path"]))
        _deny_source_submounts(str(row["source_path"]))
        self._payload(row)
        _source_integrity(str(row["source_path"]),
                          str(row["source_sha256"]), self.root)
        source_fd, name = _parent_fd(
            str(row["source_path"]), str(row["relative_path"]),
        )
        try:
            old = _read_file(source_fd, name)
            if (
                (old[1] if old else None) != row["before_sha256"]
                or (old[2] if old else None) != row["before_mode"]
                or (old[3] if old else None) != row["before_dev"]
                or (old[4] if old else None) != row["before_ino"]
            ):
                raise AdmissionDenied("original artifact changed since terminal escrow")
        finally:
            os.close(source_fd)

    def seal(
        self, lease: PodLease, stage_id: str, *,
        allowed_output_paths: frozenset[str], retention_seconds: int = 86400,
    ) -> EscrowRecord:
        """One-use authenticated terminal escrow, before closing PodLease."""
        if (
            not isinstance(lease, PodLease)
            or lease._ledger.path != self.ledger.path
            or type(retention_seconds) is not int
            or not 60 <= retention_seconds <= 7 * 86400
        ):
            raise AdmissionDenied("trusted active child lease and bounded TTL required")
        with action_lock(self.ledger.path, "artifact-escrow:" + stage_id) as acquired:
            if not acquired:
                raise AdmissionDenied("escrow sealing already in progress")
            manifest = verify_staged_artifacts(
                self.stages, lease, stage_id,
                allowed_output_paths=allowed_output_paths,
            )
            canonical = _canonical_manifest(manifest)
            if (
                len(manifest.allowed_output_paths) != 1
                or manifest.allowed_output_paths != (manifest.changes[0].path,)
                or manifest.changes[0].kind not in ("added", "modified")
            ):
                raise AdmissionDenied("detached V1 supports one explicit regular output")
            stage = self.stages.inspect_executable(lease, stage_id)
            target_path = manifest.changes[0].path
            _relative(target_path)
            source = Path(stage.source_path)
            stage_path = Path(stage.staged_path)
            root = self.root
            for candidate in (source, stage_path):
                if (
                    os.path.commonpath((str(root), str(candidate))) in
                    (str(root), str(candidate))
                ):
                    raise AdmissionDenied("escrow root intersects source or child stage")
            with self.ledger._connect() as db:
                parent = db.execute(
                    "SELECT principal,area FROM parent_budget WHERE run_id=?",
                    (stage.parent_run_id,),
                ).fetchone()
            if parent is None or parent["principal"] != lease._session.caller.principal:
                raise AdmissionDenied("trusted parent principal unavailable")
            _deny_source_submounts(stage.source_path)
            stage_fd, stage_name = _parent_fd(stage.staged_path, target_path)
            source_fd, source_name = _parent_fd(stage.source_path, target_path)
            try:
                payload = _read_file(stage_fd, stage_name)
                before = _read_file(source_fd, source_name)
                change = manifest.changes[0]
                if (
                    payload is None
                    or payload[1] != change.after_sha256
                    or payload[2] != change.after_mode
                    or len(payload[0]) > _MAX_BYTES
                    or (before[1] if before else None) != change.before_sha256
                    or (before[2] if before else None) != change.before_mode
                ):
                    raise AdmissionDenied("escrow cannot copy untrusted or drifting file")
            finally:
                os.close(stage_fd)
                os.close(source_fd)
            escrow_id = secrets.token_hex(20)
            now = self.ledger._now()
            root_fd = _path_private(root)
            try:
                root_stat = os.fstat(root_fd)
                with self.ledger._connect() as db:
                    self.ledger._tx(db)
                    if db.execute(
                        "SELECT 1 FROM pod_artifact_escrow WHERE stage_id=?",
                        (stage_id,),
                    ).fetchone():
                        raise AdmissionDenied("StageLease already has a terminal escrow")
                    db.execute("""INSERT INTO pod_artifact_escrow(
                        id,stage_id,parent_run_id,child_run_id,
                        parent_principal,parent_area,source_path,source_sha256,
                        relative_path,before_sha256,before_mode,before_dev,before_ino,
                        after_sha256,after_mode,manifest_sha256,manifest_json,
                        escrow_root_dev,escrow_root_ino,expires_at,state,stamp
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'sealing', ?)""",
                               (escrow_id, stage_id, stage.parent_run_id,
                                stage.child_run_id, parent["principal"], parent["area"],
                                stage.source_path, stage.source_sha256, target_path,
                                before[1] if before else None,
                                before[2] if before else None,
                                before[3] if before else None,
                                before[4] if before else None,
                                payload[1], payload[2], manifest.manifest_sha256,
                                canonical, root_stat.st_dev, root_stat.st_ino,
                                now + retention_seconds, now))
                    self._event(db, escrow_id, "sealing")
                os.mkdir(escrow_id, mode=0o700, dir_fd=root_fd)
                attempt_fd = os.open(
                    escrow_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=root_fd,
                )
                try:
                    _write_new(attempt_fd, "artifact", payload[0], 0o400)
                    os.fsync(attempt_fd)
                    copied = _read_file(attempt_fd, "artifact")
                    if (
                        copied is None
                        or copied[1] != payload[1]
                        or copied[2] != 0o400
                    ):
                        raise AdmissionDenied("escrow payload copy differs from terminal artifact")
                    # Stage must remain an exact committed success during
                    # independent verification; the copy does not grant access.
                    if verify_staged_artifacts(
                        self.stages, lease, stage_id,
                        allowed_output_paths=allowed_output_paths,
                    ) != manifest:
                        raise AdmissionDenied("stage changed while sealing escrow")
                    with self.ledger._connect() as db:
                        self.ledger._tx(db)
                        changed = db.execute(
                            "UPDATE pod_artifact_escrow "
                            "SET state='ready',payload_dev=?,payload_ino=?,stamp=? "
                            "WHERE id=? AND state='sealing'",
                            (copied[3], copied[4], self.ledger._now(), escrow_id),
                        )
                        if changed.rowcount != 1:
                            raise AdmissionDenied("escrow seal was already consumed")
                        self._event(db, escrow_id, "ready")
                    os.fsync(root_fd)
                finally:
                    os.close(attempt_fd)
            finally:
                os.close(root_fd)
            return self._record(self._row(escrow_id))

    def review(self, operator: GatewaySession, escrow_id: str) -> EscrowPreview:
        """Read-only, bounded and masked human preview after PodLease closure."""
        row = self._row(escrow_id)
        self._authorize_operator(operator, row)
        self._inspect(row, eligible=("ready", "approved"))
        target = str(Path(row["source_path"]) / row["relative_path"])
        decision = operator.authorize(
            kind="file_read", tool="read_file", target=target,
            args={"escrow_id": escrow_id, "manifest_sha256": row["manifest_sha256"]},
        )
        if not decision.allowed:
            raise AdmissionDenied("Gateway operator file-read policy refused escrow preview")
        src_fd, name = _parent_fd(row["source_path"], row["relative_path"])
        try:
            old = _read_file(src_fd, name)
        finally:
            os.close(src_fd)
        payload = self._payload(row)[0]
        # No unbounded code/data dumping into audit or model context.
        truncated = max(len(payload), len(old[0]) if old else 0) > 32768
        diff = ""
        if not truncated:
            try:
                before = (old[0] if old else b"").decode("utf-8")
                after = payload.decode("utf-8")
                candidate = "".join(difflib.unified_diff(
                    before.splitlines(keepends=True), after.splitlines(keepends=True),
                    fromfile="before/" + row["relative_path"],
                    tofile="escrow/" + row["relative_path"], n=3,
                ))
                truncated = len(candidate.encode("utf-8")) > 32768
                if not truncated:
                    diff = mask_secret_diff(candidate)
            except UnicodeDecodeError:
                truncated = True
        self._inspect(self._row(escrow_id), eligible=("ready", "approved"))
        return EscrowPreview(
            self._record(self._row(escrow_id)),
            str(row["source_sha256"]), row["before_sha256"], row["after_sha256"],
            row["before_mode"], row["after_mode"],
            diff, truncated,
        )

    def approve(
        self, operator: GatewaySession, escrow_id: str, *,
        ttl_seconds: int = 300,
    ) -> EscrowRecord:
        """One-use exact human Gateway approval; no live child lease needed."""
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
            raise AdmissionDenied("invalid post-lease approval duration")
        self.review(operator, escrow_id)
        row = self._row(escrow_id)
        if row["state"] != "ready":
            raise AdmissionDenied("escrow already approved or consumed")
        target = str(Path(row["source_path"]) / row["relative_path"])
        facts = {
            "escrow_id": escrow_id,
            "stage_id": row["stage_id"],
            "manifest_sha256": row["manifest_sha256"],
            "relative_path": row["relative_path"],
            "after_sha256": row["after_sha256"],
        }
        decision = operator.authorize(
            kind="tool_call", tool="approve_artifact_promotion",
            target=target, args=facts,
        )
        if not decision.allowed or REASON_APPROVED not in decision.reasons:
            raise AdmissionDenied("human approval of exact detached manifest required")
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            now = self.ledger._now()
            outcome = db.execute(
                "UPDATE pod_artifact_escrow "
                "SET state='approved',approval_expires_at=?,"
                "approver_principal=?,gateway_audit_id=?,stamp=? "
                "WHERE id=? AND state='ready' AND expires_at>?",
                (min(now + ttl_seconds, row["expires_at"]),
                 operator.caller.principal, decision.audit_id,
                 now, escrow_id, now),
            )
            if outcome.rowcount != 1:
                raise AdmissionDenied("escrow approval consumed by competing operator")
            self._event(db, escrow_id, "approved")
        return self._record(self._row(escrow_id))
