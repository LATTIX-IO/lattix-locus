"""LOCUS-466: Linux-only stage generation witness held outside child mounts.

V1 StageLease ctime/mtime checks remain strict *before* dispatch. This
additional durable, kernel-attested witness tolerates subsequent changes to
the writable stage without accepting a recycled inode or renamed mount.
Neither the nonce nor the supervisor SQLite ledger is mounted in the child.
"""
from __future__ import annotations

import ctypes
import os
import stat
import sys
from pathlib import Path
from typing import Any

from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_workspace_staging import _open_anchored_directory

# Linux statx(2), using libc's architecture-independent wrapper.
_AT_EMPTY_PATH = 0x1000
_AT_SYMLINK_NOFOLLOW = 0x100
_STATX_BTIME = 0x800
_STATX_MNT_ID = 0x1000


class _Timestamp(ctypes.Structure):
    _fields_ = [
        ("sec", ctypes.c_int64), ("nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    _fields_ = [
        ("mask", ctypes.c_uint32), ("blksize", ctypes.c_uint32),
        ("attributes", ctypes.c_uint64), ("nlink", ctypes.c_uint32),
        ("uid", ctypes.c_uint32), ("gid", ctypes.c_uint32),
        ("mode", ctypes.c_uint16), ("spare0", ctypes.c_uint16),
        ("ino", ctypes.c_uint64), ("size", ctypes.c_uint64),
        ("blocks", ctypes.c_uint64), ("attributes_mask", ctypes.c_uint64),
        ("atime", _Timestamp), ("btime", _Timestamp),
        ("ctime", _Timestamp), ("mtime", _Timestamp),
        ("rdev_major", ctypes.c_uint32), ("rdev_minor", ctypes.c_uint32),
        ("dev_major", ctypes.c_uint32), ("dev_minor", ctypes.c_uint32),
        ("mnt_id", ctypes.c_uint64), ("dio_mem_align", ctypes.c_uint32),
        ("dio_offset_align", ctypes.c_uint32), ("spare3", ctypes.c_uint64 * 12),
    ]


def kernel_birth_identity(fd: int) -> tuple[int, int]:
    """Return (birthtime_ns, mount_id), or fail closed on unsupported hosts."""
    if sys.platform != "linux" or os.geteuid() == 0:
        raise AdmissionDenied("stage execution requires an unprivileged Linux supervisor")
    info = _Statx()
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.statx
        call.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint,
            ctypes.POINTER(_Statx),
        ]
        call.restype = ctypes.c_int
        code = call(fd, b"", _AT_EMPTY_PATH | _AT_SYMLINK_NOFOLLOW,
                    _STATX_BTIME | _STATX_MNT_ID, ctypes.byref(info))
    except (AttributeError, OSError, TypeError) as exc:
        raise AdmissionDenied("Linux statx stage identity unavailable") from exc
    if (
        code != 0 or (info.mask & (_STATX_BTIME | _STATX_MNT_ID)) !=
        (_STATX_BTIME | _STATX_MNT_ID)
        or info.btime.sec <= 0 or info.mnt_id == 0
        or info.btime.nsec >= 1_000_000_000
    ):
        raise AdmissionDenied("stage birth and mount identity cannot be proved")
    return int(info.btime.sec * 1_000_000_000 + info.btime.nsec), int(info.mnt_id)


def _private_parent(path: Path) -> tuple[int, int]:
    """No writable shared staging parent or stage escape via symlink ancestors."""
    fd = _open_anchored_directory(path.parent)
    try:
        current = os.fstat(fd)
        if (
            current.st_uid != os.geteuid()
            or stat.S_IMODE(current.st_mode) != 0o700
        ):
            raise AdmissionDenied("staging parent must be supervisor-owned mode 0700")
        return current.st_dev, current.st_ino
    finally:
        os.close(fd)


def _deny_nested_mounts(stage: Path) -> None:
    """A stage containing host bind submounts must not be made recursive-rw."""
    # A readable /proc/self/mountinfo is mandatory, not best-effort.
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AdmissionDenied("host mount inventory unavailable") from exc
    prefix = str(stage) + "/"
    for line in lines:
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            raise AdmissionDenied("malformed host mount inventory")
        # Linux mountinfo replaces unsafe path characters with octal escapes.
        raw = fields[4]
        for encoded, decoded in (
            ("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\"),
        ):
            raw = raw.replace(encoded, decoded)
        if raw == str(stage) or raw.startswith(prefix):
            raise AdmissionDenied("stage contains a pre-existing nested/bind mount")


def stage_witness(stage: Path) -> tuple[int, int, int, int, int, int, int]:
    """Attest parent inode and stage inode/birthtime without trusting path strings."""
    if not stage.is_absolute() or "," in str(stage) or ":" in str(stage):
        raise AdmissionDenied("stage mount path contains unsafe Docker syntax")
    parent_dev, parent_ino = _private_parent(stage)
    fd = _open_anchored_directory(stage)
    try:
        directory = os.fstat(fd)
        if (
            directory.st_uid != os.geteuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            raise AdmissionDenied("stage must remain supervisor-owned mode 0700")
        birthtime, mount = kernel_birth_identity(fd)
        _deny_nested_mounts(stage)
        return (parent_dev, parent_ino, directory.st_dev,
                directory.st_ino, birthtime, mount, directory.st_uid)
    finally:
        os.close(fd)


def require_generation(db: Any, row: Any) -> str:
    """Re-attest trusted V2 generation after writes; no stage ctime dependency."""
    record = db.execute(
        "SELECT * FROM pod_stage_exec_generation WHERE stage_id=?",
        (row["id"],),
    ).fetchone()
    if record is None:
        raise AdmissionDenied("stage executable generation not registered")
    if (
        record["reservation_id"] != row["reservation_id"]
        or record["binding_sha256"] != row["binding_sha256"]
        or record["source_sha256"] != row["source_sha256"]
        or record["staged_path"] != row["staged_path"]
        or record["stage_dev"] != row["stage_dev"]
        or record["stage_ino"] != row["stage_ino"]
    ):
        raise AdmissionDenied("executable stage witness no longer matches StageLease")
    current = stage_witness(Path(row["staged_path"]))
    expected = (
        record["parent_dev"], record["parent_ino"],
        row["stage_dev"], row["stage_ino"],
        record["birth_ns"], record["mount_id"], record["stage_uid"],
    )
    if current != expected:
        raise AdmissionDenied("stage generation was substituted or recycled")
    return str(record["generation"])
