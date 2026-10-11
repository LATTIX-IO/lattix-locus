"""Local per-action OS lock used to fence recovery from live tool callbacks.

Only trusted host-side brokers use this primitive. Locks are released by the
operating system when the holding process terminates, including hard crashes.
A per-action file avoids serializing independent parallel agent pods.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from locus_runtime.pod_reservations import AdmissionDenied


def _lock_path(database: str, action_id: str) -> Path:
    if not isinstance(action_id, str) or not action_id or len(action_id) > 256:
        raise AdmissionDenied("invalid action identity for fence")
    root = Path(f"{database}.action-locks")
    if root.is_symlink():
        raise AdmissionDenied("symlinked dispatch lock directory refused")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise AdmissionDenied("untrusted dispatch lock directory")
    if os.name == "posix" and stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise AdmissionDenied("dispatch lock directory must be private")
    filename = hashlib.sha256(action_id.encode("utf-8")).hexdigest() + ".lock"
    return root / filename


@contextmanager
def action_lock(database: str, action_id: str) -> Iterator[bool]:
    """Acquire nonblocking exclusive OS lock; yield False when already owned.

    Windows uses a one-byte byte-range lock, POSIX uses flock. Both are
    kernel-managed and released on process death. No timing/heartbeat
    heuristic is sufficient proof that a callback has terminated.
    """
    target = _lock_path(database, action_id)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    held = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\x00")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
            else:
                held = True
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
            else:
                held = True
        yield held
    finally:
        if held:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
