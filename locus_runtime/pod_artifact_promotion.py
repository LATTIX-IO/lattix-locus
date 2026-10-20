"""LOCUS-468/B: one-file, host-supervised artifact promotion and crash recovery.

Only the trusted parent may call this, after a *real* human-approved, sealed
Gateway action. The container never gets writable access to the original.
Multi-file transactions, deletes, new ancestor directories, Windows/macOS,
and unattended approval are explicitly unsupported (fail closed).
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import sqlite3
import stat
import sys
from dataclasses import dataclass
from typing import cast
from pathlib import Path

from locus_runtime.action_fencing import action_lock
from locus_runtime.gateway import GatewaySession, REASON_APPROVED
from locus_runtime.pod_artifact_verification import (
    VerifiedArtifactManifest, verify_staged_artifacts,
)
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_workspace_staging import _excluded_name, _open_anchored_directory

_MAX_PROMOTED_BYTES = 8 * 1024 * 1024
_SCHEMA_VERSION = 1
_STATES = {"approved", "promoting", "promoted", "rolled_back", "uncertain"}
_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    approval_id: str
    stage_id: str
    manifest_sha256: str
    path: str
    state: str
    principal: str
    expires_at: int


def _relative(path: str) -> tuple[str, ...]:
    if (not isinstance(path, str) or not path or path.startswith("/")
        or "\\" in path or "\x00" in path or "\n" in path or "\r" in path
        or len(path.encode("utf-8")) > 2048):
        raise AdmissionDenied("unsafe promotion relative output path")
    parts = path.split("/")
    if any(part in ("", ".", "..") or _excluded_name(part, tuple(parts[:i]))
           for i, part in enumerate(parts)):
        raise AdmissionDenied("protected or traversing promotion path")
    return tuple(parts)


def _parent_fd(root: str | Path, path: str) -> tuple[int, str]:
    """Traverse only original StageLease absolute roots and pinned ancestors."""
    parts = _relative(path)
    fd = _open_anchored_directory(Path(root))
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part, _FLAGS | os.O_DIRECTORY, dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _read_file(fd: int, name: str) -> tuple[bytes, str, int, int, int] | None:
    try:
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
        or before.st_size > _MAX_PROMOTED_BYTES
        or stat.S_IMODE(before.st_mode) & 0o7000):
        raise AdmissionDenied("unsafe promotion source entry")
    opened = os.open(name, _FLAGS, dir_fd=fd)
    try:
        current = os.fstat(opened)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or current.st_nlink != 1:
            raise AdmissionDenied("promotion file changed during open")
        chunks: list[bytes] = []
        total = 0
        while data := os.read(opened, 262144):
            total += len(data)
            if total > _MAX_PROMOTED_BYTES or total > before.st_size:
                raise AdmissionDenied("promotion file byte limit exceeded")
            chunks.append(data)
        after = os.fstat(opened)
        if (total != before.st_size or
            (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
            or after.st_nlink != 1):
            raise AdmissionDenied("promotion file mutated during hash")
        payload = b"".join(chunks)
        return (
            payload, hashlib.sha256(payload).hexdigest(),
            stat.S_IMODE(before.st_mode), before.st_dev, before.st_ino,
        )
    finally:
        os.close(opened)


def _write_new(fd: int, name: str, payload: bytes, mode: int) -> None:
    if len(payload) > _MAX_PROMOTED_BYTES:
        raise AdmissionDenied("promotion payload exceeds limit")
    out = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=fd,
    )
    try:
        pos = 0
        while pos < len(payload):
            count = os.write(out, payload[pos:])
            if count <= 0:
                raise OSError("incomplete promotion journal write")
            pos += count
        os.fchmod(out, mode)
        os.fsync(out)
    finally:
        os.close(out)


def _stat_same_root(left: int, right: int) -> bool:
    return os.fstat(left).st_dev == os.fstat(right).st_dev


def _deny_source_submounts(root: str) -> None:
    """Never write through nested bind/mount aliases inside an approved root."""
    if sys.platform != "linux":
        raise AdmissionDenied("host source mount inventory requires Linux")
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AdmissionDenied("promotion host mount inventory unavailable") from exc
    prefix = str(root).rstrip("/") + "/"
    for line in lines:
        data = line.split(" - ", 1)[0].split()
        if len(data) < 5:
            raise AdmissionDenied("malformed promotion host mount inventory")
        mount = data[4]
        for encoded, decoded in (
            ("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\"),
        ):
            mount = mount.replace(encoded, decoded)
        if mount.startswith(prefix):
            raise AdmissionDenied("nested source mount is not promotable")


def _exchange(fd: int, temporary: str, name: str) -> None:
    """Atomic Linux two-dentry exchange. The displaced inode remains inspectable.

    Used only for an existing-file content modification. Compare the displaced
    inode to the one pinned by human approval after exchange. If it differs,
    exchange back under the OS fence rather than quietly discard an operator's
    independently written data. A foreign process can still race these kernel
    operations, so any unprovable compensation stays uncertain.
    """
    if sys.platform != "linux":
        raise AdmissionDenied("kernel atomic exchange requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise AdmissionDenied("kernel atomic exchange unavailable") from exc
    rename.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(fd, os.fsencode(temporary), fd, os.fsencode(name), 2) != 0:
        raise AdmissionDenied("atomic source exchange failed")


def _publish_noreplace(fd: int, temporary: str, name: str) -> None:
    """Linux atomic create-only publication; never clobber a late new file."""
    if sys.platform != "linux":
        raise AdmissionDenied("atomic no-replace rename needs Linux kernel")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise AdmissionDenied("kernel atomic noreplace unavailable") from exc
    rename.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(fd, os.fsencode(temporary), fd, os.fsencode(name), 1) != 0:
        if ctypes.get_errno() == errno.EEXIST:
            raise AdmissionDenied("a concurrent source file already exists")
        raise AdmissionDenied("kernel atomic noreplace rejected promotion")


class StageArtifactPromoter:
    """Explicit, durable one-use human approval plus one-file atomic write."""

    def __init__(self, stages: PodStageBindings) -> None:
        if not isinstance(stages, PodStageBindings):
            raise AdmissionDenied("promotion requires trusted StageLease authority")
        self.stages = stages
        self.ledger = stages.ledger
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            db.execute("""CREATE TABLE IF NOT EXISTS pod_artifact_promotion_schema(
                version INTEGER NOT NULL
            )""")
            row = db.execute(
                "SELECT version FROM pod_artifact_promotion_schema",
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO pod_artifact_promotion_schema VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            elif row["version"] != _SCHEMA_VERSION:
                raise AdmissionDenied("unsupported promotion ledger schema")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_artifact_promotion(
                id TEXT PRIMARY KEY,
                stage_id TEXT NOT NULL UNIQUE REFERENCES pod_stage_binding(id),
                manifest_sha256 TEXT NOT NULL,
                receipt_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                staged_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_before_sha256 TEXT,
                source_before_mode INTEGER,
                source_dev INTEGER,
                source_ino INTEGER,
                stage_after_sha256 TEXT NOT NULL,
                prepared_dev INTEGER,
                prepared_ino INTEGER,
                principal TEXT NOT NULL,
                gateway_audit_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                backup_name TEXT NOT NULL,
                temp_name TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'approved','promoting','promoted','rolled_back','uncertain'
                )),
                stamp INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pod_artifact_promotion_audit(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT NOT NULL REFERENCES pod_artifact_promotion(id),
                event TEXT NOT NULL,
                stamp INTEGER NOT NULL
            )""")

    def _auth(
        self, operator: GatewaySession, lease: PodLease,
    ) -> None:
        if (
            not isinstance(operator, GatewaySession)
            or not isinstance(lease, PodLease)
            or operator.gateway is not lease._session.gateway
            or operator.caller.run_id == lease.run_id
            or operator.caller.principal != lease._session.caller.principal
        ):
            raise AdmissionDenied("separate, authenticated operator is required")
        authority = operator.gateway._authenticate(operator.caller)
        if authority is None or not authority.can_delegate:
            raise AdmissionDenied("operator does not have trusted supervisor authority")

    def approve(
        self, operator: GatewaySession, lease: PodLease,
        manifest: VerifiedArtifactManifest, *, ttl_seconds: int = 300,
    ) -> PromotionRecord:
        """Consume EXACT Gateway human-approved R3 action, then record one use.

        The user-facing approval must be issued by the privileged UI into the
        Gateway ApprovalLedger for this exact fingerprint. Passing a boolean
        or a model-generated string does not grant promotion authority.
        """
        self._auth(operator, lease)
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
            raise AdmissionDenied("promotion approval requires bounded expiration")
        if not isinstance(manifest, VerifiedArtifactManifest):
            raise AdmissionDenied("independently verified manifest required")
        if len(manifest.changes) != 1:
            raise AdmissionDenied("atomic V1 promotion supports exactly one file")
        change = manifest.changes[0]
        if (change.kind not in ("added", "modified")
            or not change.after_sha256
            or change.before_sha256 == change.after_sha256):
            raise AdmissionDenied("V1 promotion requires content add or modification")
        current = verify_staged_artifacts(
            self.stages, lease, manifest.stage_id,
            allowed_output_paths=frozenset(manifest.allowed_output_paths),
        )
        if current != manifest:
            raise AdmissionDenied("promotion manifest changed after inspection")
        path = change.path
        _relative(path)
        stage = self.stages.inspect_executable(lease, manifest.stage_id)
        _deny_source_submounts(stage.source_path)
        original_fd, original_name = _parent_fd(stage.source_path, path)
        try:
            initial = _read_file(original_fd, original_name)
        finally:
            os.close(original_fd)
        if (
            (initial[1] if initial else None) != change.before_sha256
            or (initial[2] if initial else None) != change.before_mode
        ):
            raise AdmissionDenied("promotion source mode or bytes changed before approval")
        target = str(Path(stage.source_path) / path)
        args = {
            "stage_id": manifest.stage_id,
            "manifest_sha256": manifest.manifest_sha256,
            "relative_path": path,
            "after_sha256": change.after_sha256,
        }
        decision = operator.authorize(
            kind="tool_call", tool="approve_artifact_promotion",
            target=target, args=args,
        )
        if not decision.allowed or REASON_APPROVED not in decision.reasons:
            raise AdmissionDenied("a human-approved exact Gateway action is mandatory")
        approved_id = secrets.token_hex(20)
        now = self.ledger._now()
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            if db.execute(
                "SELECT 1 FROM pod_artifact_promotion WHERE stage_id=?",
                (stage.stage_id,),
            ).fetchone():
                raise AdmissionDenied("StageLease already has a promotion decision")
            db.execute("""INSERT INTO pod_artifact_promotion(
                id,stage_id,manifest_sha256,receipt_id,source_path,staged_path,
                relative_path,source_before_sha256,source_before_mode,
                source_dev,source_ino,stage_after_sha256,principal,
                gateway_audit_id,expires_at,backup_name,temp_name,state,stamp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'approved',?)""",
                       (approved_id, stage.stage_id, manifest.manifest_sha256,
                        manifest.receipt_id, stage.source_path, stage.staged_path,
                        path, change.before_sha256,
                        initial[2] if initial else None,
                        initial[3] if initial else None,
                        initial[4] if initial else None,
                        change.after_sha256,
                        operator.caller.principal, decision.audit_id, now + ttl_seconds,
                        ".locus-backup-" + approved_id,
                        ".locus-promotion-" + approved_id, now))
            db.execute(
                "INSERT INTO pod_artifact_promotion_audit(approval_id,event,stamp)"
                " VALUES (?, 'approved', ?)", (approved_id, now),
            )
        return PromotionRecord(
            approved_id, stage.stage_id, manifest.manifest_sha256,
            path, "approved", operator.caller.principal, now + ttl_seconds,
        )

    def _row(self, approval_id: str) -> sqlite3.Row:
        with self.ledger._connect() as db:
            row = db.execute(
                "SELECT * FROM pod_artifact_promotion WHERE id=?", (approval_id,),
            ).fetchone()
        if row is None:
            raise AdmissionDenied("unknown artifact approval")
        return cast(sqlite3.Row, row)

    def _transition(self, approval_id: str, before: str, after: str) -> None:
        if before not in _STATES or after not in _STATES:
            raise AdmissionDenied("invalid artifact transition")
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            result = db.execute(
                "UPDATE pod_artifact_promotion SET state=?,stamp=? "
                "WHERE id=? AND state=?",
                (after, self.ledger._now(), approval_id, before),
            )
            if result.rowcount != 1:
                raise AdmissionDenied("promotion decision already consumed or changed")
            db.execute(
                "INSERT INTO pod_artifact_promotion_audit(approval_id,event,stamp)"
                " VALUES (?,?,?)", (approval_id, after, self.ledger._now()),
            )

    def _seal_prepared_target(
        self, approval_id: str, expected_digest: str, prepared: tuple[bytes, str, int, int, int],
    ) -> None:
        """Durably pin the exact pending inode BEFORE any rename can publish it.

        Crash/restart recovery must not undo a separately replaced file with
        equal bytes: compare dev/ino as well as digest.
        """
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            result = db.execute(
                "UPDATE pod_artifact_promotion "
                "SET prepared_dev=?,prepared_ino=?,stamp=? "
                "WHERE id=? AND state='promoting' AND prepared_ino IS NULL "
                "AND stage_after_sha256=?",
                (prepared[3], prepared[4], self.ledger._now(), approval_id,
                 expected_digest),
            )
            if result.rowcount != 1 or prepared[1] != expected_digest:
                raise AdmissionDenied("prepared promotion inode cannot be sealed")
            db.execute(
                "INSERT INTO pod_artifact_promotion_audit(approval_id,event,stamp) "
                "VALUES (?,'prepared-target',?)",
                (approval_id, self.ledger._now()),
            )

    def promote(
        self, operator: GatewaySession, lease: PodLease,
        manifest: VerifiedArtifactManifest, *, approval_id: str,
    ) -> PromotionRecord:
        """Apply exactly one approved file through supervisor-owned os.replace.

        This is a single-file atomic visibility guarantee, NOT a multi-file
        transaction. Crash after durable 'promoting' is never auto-replayed.
        The source-level lock also serializes sibling promotion attempts.
        """
        self._auth(operator, lease)
        stage = self.stages.inspect_executable(lease, manifest.stage_id)
        with action_lock(
            self.ledger.path, "source-promotion:" + stage.source_path,
        ) as acquired:
            if not acquired:
                raise AdmissionDenied("another source promotion owns the OS fence")
            row = self._row(approval_id)
            if (
                row["state"] != "approved"
                or row["stage_id"] != manifest.stage_id
                or row["manifest_sha256"] != manifest.manifest_sha256
                or row["principal"] != operator.caller.principal
                or row["expires_at"] <= self.ledger._now()
                or row["source_path"] != stage.source_path
                or row["staged_path"] != stage.staged_path
                or row["receipt_id"] != manifest.receipt_id
            ):
                raise AdmissionDenied("stale, foreign or replayed artifact approval")
            if verify_staged_artifacts(
                self.stages, lease, manifest.stage_id,
                allowed_output_paths=frozenset(manifest.allowed_output_paths),
            ) != manifest:
                raise AdmissionDenied("staged files changed after approval")
            change = manifest.changes[0]
            if (
                change.path != row["relative_path"]
                or change.before_sha256 != row["source_before_sha256"]
                or change.after_sha256 != row["stage_after_sha256"]
            ):
                raise AdmissionDenied("approved artifact file identity was altered")
            target = str(Path(stage.source_path) / row["relative_path"])
            write = operator.authorize(
                kind="file_write", tool="write_file", target=target,
                args={"manifest_sha256":manifest.manifest_sha256},
            )
            if not write.allowed:
                raise AdmissionDenied("independent Gateway file write policy denied promotion")

            _deny_source_submounts(stage.source_path)
            src_fd, src_name = _parent_fd(stage.source_path, row["relative_path"])
            stage_fd, staged_name = _parent_fd(stage.staged_path, row["relative_path"])
            backup_fd = _open_anchored_directory(Path(stage.staged_path).parent)
            try:
                if not _stat_same_root(src_fd, stage_fd) or not _stat_same_root(src_fd, backup_fd):
                    raise AdmissionDenied("atomic/rollback filesystem domains differ")
                old = _read_file(src_fd, src_name)
                new = _read_file(stage_fd, staged_name)
                if (
                    (old[1] if old else None) != row["source_before_sha256"]
                    or (old[2] if old else None) != row["source_before_mode"]
                    or (old[3] if old else None) != row["source_dev"]
                    or (old[4] if old else None) != row["source_ino"]
                    or new is None or new[1] != row["stage_after_sha256"]
                    or (old is not None and new[1] == old[1])
                ):
                    raise AdmissionDenied("source or output changed before atomic promotion")
                mode = old[2] if old else 0o600
                # Setuid/setgid/sticky and executable mode changes are not
                # supported through this V1 content-only promotion contract.
                if (new[2] & 0o7000
                    or old is not None and bool(new[2] & 0o111) != bool(old[2] & 0o111)):
                    raise AdmissionDenied("artifact mode promotion outside V1 policy")

                self._transition(approval_id, "approved", "promoting")
                # From here crashes or IO errors stay uncertain; no second
                # approval can replay the same StageLease transaction.
                if old is not None:
                    _write_new(backup_fd, row["backup_name"], old[0], old[2])
                    os.fsync(backup_fd)
                _write_new(src_fd, row["temp_name"], new[0], mode)
                os.fsync(src_fd)
                prepared = _read_file(src_fd, row["temp_name"])
                if (
                    prepared is None
                    or prepared[1] != row["stage_after_sha256"]
                    or prepared[2] != mode
                ):
                    raise AdmissionDenied("prepared artifact bytes or mode changed")
                self._seal_prepared_target(
                    approval_id, row["stage_after_sha256"], prepared,
                )
                fresh = _read_file(src_fd, src_name)
                if (
                    (fresh[1] if fresh else None) != row["source_before_sha256"]
                    or (fresh[2] if fresh else None) != row["source_before_mode"]
                    or (fresh[3] if fresh else None) != row["source_dev"]
                    or (fresh[4] if fresh else None) != row["source_ino"]
                ):
                    raise AdmissionDenied("original changed during promotion preparation")
                if old is None:
                    _publish_noreplace(src_fd, row["temp_name"], src_name)
                else:
                    # An exchange keeps the displaced inode observable instead
                    # of irreversibly overwriting an outside writer's rename.
                    _exchange(src_fd, row["temp_name"], src_name)
                    displaced = _read_file(src_fd, row["temp_name"])
                    if (
                        displaced is None
                        or displaced[1] != row["source_before_sha256"]
                        or displaced[2] != row["source_before_mode"]
                        or displaced[3] != row["source_dev"]
                        or displaced[4] != row["source_ino"]
                    ):
                        active = _read_file(src_fd, src_name)
                        if (
                            active is None
                            or (active[3], active[4]) != (prepared[3], prepared[4])
                        ):
                            raise AdmissionDenied(
                                "concurrent source swap cannot be safely compensated"
                            )
                        _exchange(src_fd, row["temp_name"], src_name)
                        os.fsync(src_fd)
                        raise AdmissionDenied(
                            "concurrent source replacement compensated; manual review"
                        )
                os.fsync(src_fd)
                committed = _read_file(src_fd, src_name)
                if (
                    committed is None
                    or committed[1] != row["stage_after_sha256"]
                    or (committed[3], committed[4])
                    != (prepared[3], prepared[4])
                ):
                    raise AdmissionDenied("atomic promotion target not independently verified")
                self._transition(approval_id, "promoting", "promoted")
                return PromotionRecord(
                    approval_id, stage.stage_id, manifest.manifest_sha256,
                    row["relative_path"], "promoted", operator.caller.principal,
                    row["expires_at"],
                )
            finally:
                # A failed/ambiguous promotion retains its source-directory
                # temporary inode for independent crash recovery. Cleaning it
                # here could destroy the only exact displaced-source witness.
                try:
                    if self._row(approval_id)["state"] == "promoted":
                        try:
                            os.unlink(row["temp_name"], dir_fd=src_fd)
                        except FileNotFoundError:
                            # New-file RENAME_NOREPLACE moved the temporary
                            # dentry to its final path; absence is expected.
                            pass
                finally:
                    os.close(backup_fd)
                    os.close(stage_fd)
                    os.close(src_fd)

    def recover_uncertain(self, approval_id: str) -> str:
        """Independent restarted supervisor: rollback only when bytes match.

        Never replay a promotion. Any divergent / symlinked / missing backup
        blocks cleanup with a durable uncertain outcome for operator review.
        """
        row = self._row(approval_id)
        if row["state"] != "promoting":
            raise AdmissionDenied("only interrupted promotions can be reconciled")
        with action_lock(
            self.ledger.path, "source-promotion:" + row["source_path"],
        ) as acquired:
            if not acquired:
                raise AdmissionDenied("live promoter still owns source OS fence")
            row = self._row(approval_id)
            if row["state"] != "promoting":
                raise AdmissionDenied("promotion recovery replay denied")
            _deny_source_submounts(str(row["source_path"]))
            src_fd, name = _parent_fd(row["source_path"], row["relative_path"])
            backup_fd = _open_anchored_directory(Path(row["staged_path"]).parent)
            try:
                old = _read_file(src_fd, name)
                current = old[1] if old else None
                if current == row["source_before_sha256"]:
                    self._transition(approval_id, "promoting", "rolled_back")
                    return "rolled_back"
                if current != row["stage_after_sha256"]:
                    self._transition(approval_id, "promoting", "uncertain")
                    return "uncertain"
                if row["source_before_sha256"] is None:
                    os.unlink(name, dir_fd=src_fd)
                else:
                    backup = _read_file(backup_fd, row["backup_name"])
                    if backup is None or backup[1] != row["source_before_sha256"]:
                        self._transition(approval_id, "promoting", "uncertain")
                        return "uncertain"
                    if not _stat_same_root(src_fd, backup_fd):
                        self._transition(approval_id, "promoting", "uncertain")
                        return "uncertain"
                    os.replace(
                        row["backup_name"], name,
                        src_dir_fd=backup_fd, dst_dir_fd=src_fd,
                    )
                os.fsync(src_fd)
                restored = _read_file(src_fd, name)
                if (restored[1] if restored else None) != row["source_before_sha256"]:
                    self._transition(approval_id, "promoting", "uncertain")
                    return "uncertain"
                self._transition(approval_id, "promoting", "rolled_back")
                return "rolled_back"
            finally:
                os.close(backup_fd)
                os.close(src_fd)
