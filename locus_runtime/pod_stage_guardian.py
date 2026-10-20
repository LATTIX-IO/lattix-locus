"""Supervisor-private, Linux-only recovery guardian heartbeat and admission.

A stage Docker worker MUST verify this out-of-stage, kernel-identity-bound
record before a one-use receipt and immediately before Docker starts. Only the
independent OS-supervised recovery guardian publishes it. A dead/reused PID,
reboot, stale heartbeat, changed local daemon or blocked reconciliation denies.

This check cannot protect against the privileged Docker daemon, root, or a
same-UID adversary allowed to overwrite the supervisor's private SQLite DB.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import socket
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Literal, cast

from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger

_MAX_AGE_NS = 5_000_000_000
_BOOT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.ASCII)
_EPOCH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_INVOCATION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_Phase = Literal["ready", "blocked"]


def _kernel_identity(pid: int) -> tuple[str, int]:
    """Bind to Linux boot ID and immutable process creation tick."""
    if sys.platform != "linux" or os.geteuid() == 0:
        raise AdmissionDenied("guardian identity only supported for nonroot Linux")
    if type(pid) is not int or pid <= 1:
        raise AdmissionDenied("guardian PID invalid")
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        stat_path = Path("/proc") / str(pid) / "stat"
        raw = stat_path.read_text(encoding="ascii")
        # /proc/PID/stat comm is parenthesized and may contain ')' or spaces.
        marker = raw.rfind(")")
        tail = raw[marker + 2:].split()
        start = int(tail[19])
        proc_owner = stat_path.stat().st_uid
    except (OSError, ValueError, IndexError, UnicodeError) as exc:
        raise AdmissionDenied("guardian OS process identity unavailable") from exc
    if (not _BOOT.fullmatch(boot) or marker < 0 or start <= 0
        or proc_owner != os.geteuid()):
        raise AdmissionDenied("guardian OS identity invalid or foreign")
    return boot, start


def _proc_supervisor(pid: int, invocation: str) -> None:
    """Prove same process advertises recovery watch and systemd invocation."""
    if not _INVOCATION.fullmatch(invocation):
        raise AdmissionDenied("guardian manager invocation invalid")
    try:
        base = Path("/proc") / str(pid)
        args = (base / "cmdline").read_bytes().split(b"\x00")
        environ = (base / "environ").read_bytes().split(b"\x00")
    except OSError as exc:
        raise AdmissionDenied("guardian procfs proof unavailable") from exc
    if (
        b"-m" not in args or b"locus_runtime.pod_stage_recovery" not in args
        or b"--watch" not in args
        or ("INVOCATION_ID=" + invocation).encode("ascii") not in environ
        or not any(x.startswith(b"NOTIFY_SOCKET=") and len(x) > 15 for x in environ)
    ):
        raise AdmissionDenied("guardian is not a systemd-notified recovery watcher")


def _socket_identity(socket_path: Path) -> tuple[str, int, int]:
    try:
        if not socket_path.is_absolute():
            raise AdmissionDenied("Docker socket path must be absolute")
        resolved = socket_path.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise AdmissionDenied("guardian local daemon socket unavailable") from exc
    if not stat.S_ISSOCK(info.st_mode):
        raise AdmissionDenied("guardian Docker endpoint is not a Unix socket")
    if (info.st_uid not in (0, os.geteuid())
        or stat.S_IMODE(info.st_mode) & 0o002):
        raise AdmissionDenied("guardian Docker endpoint not trusted")
    return str(resolved), info.st_dev, info.st_ino


def _ledger_identity(ledger: PodReservationLedger) -> tuple[int, int]:
    try:
        dbstat = Path(ledger.path).stat()
        parent = Path(ledger.path).parent
        parentstat = parent.stat()
    except OSError as exc:
        raise AdmissionDenied("guardian ledger unavailable") from exc
    if (
        not Path(ledger.path).is_absolute()
        or not stat.S_ISREG(dbstat.st_mode)
        or dbstat.st_uid != os.geteuid()
        or stat.S_IMODE(dbstat.st_mode) & 0o022
        or parentstat.st_uid != os.geteuid()
        or stat.S_IMODE(parentstat.st_mode) != 0o700
    ):
        raise AdmissionDenied("guardian ledger lacks private supervisor ownership")
    return dbstat.st_dev, dbstat.st_ino


def _schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS pod_stage_guardian (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        epoch TEXT NOT NULL,
        pid INTEGER NOT NULL,
        process_start INTEGER NOT NULL,
        boot_id TEXT NOT NULL,
        invocation_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ready','blocked')),
        heartbeat_monotonic_ns INTEGER NOT NULL,
        ledger_dev INTEGER NOT NULL,
        ledger_ino INTEGER NOT NULL,
        socket_path TEXT NOT NULL,
        socket_dev INTEGER NOT NULL,
        socket_ino INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS pod_stage_guardian_audit (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        epoch_sha256 TEXT NOT NULL,
        event TEXT NOT NULL CHECK(
            event IN ('registered','ready','blocked')
        ),
        stamp INTEGER NOT NULL
    )""")


def _attest(row: sqlite3.Row, ledger: PodReservationLedger, socket_path: Path,
            *, check_age: bool) -> None:
    if (
        not _EPOCH.fullmatch(row["epoch"])
        or row["state"] not in ("ready", "blocked")
        or row["heartbeat_monotonic_ns"] <= 0
    ):
        raise AdmissionDenied("guardian record invalid")
    boot, start = _kernel_identity(row["pid"])
    dev, ino = _ledger_identity(ledger)
    path, sock_dev, sock_ino = _socket_identity(socket_path)
    if (
        row["boot_id"] != boot or row["process_start"] != start
        or row["ledger_dev"] != dev or row["ledger_ino"] != ino
        or row["socket_path"] != path
        or row["socket_dev"] != sock_dev or row["socket_ino"] != sock_ino
    ):
        raise AdmissionDenied("guardian PID reused, rebooted or authority changed")
    _proc_supervisor(int(row["pid"]), row["invocation_id"])
    if check_age:
        age = time.monotonic_ns() - row["heartbeat_monotonic_ns"]
        if age < 0 or age > _MAX_AGE_NS or row["state"] != "ready":
            raise AdmissionDenied("guardian heartbeat stale or reconciliation blocked")


def require_guardian_ready(
    ledger: PodReservationLedger, socket_path: Path,
) -> str:
    """Fail closed before every stage-Docker admission and during dispatch."""
    if not isinstance(ledger, PodReservationLedger):
        raise AdmissionDenied("guardian ledger authority invalid")
    with ledger._connect() as db:
        try:
            row = db.execute(
                "SELECT * FROM pod_stage_guardian WHERE singleton=1"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise AdmissionDenied("OS-supervised stage guardian not registered") from exc
    if row is None:
        raise AdmissionDenied("OS-supervised stage guardian not registered")
    _attest(row, ledger, socket_path, check_age=True)
    return cast(str, row["epoch"])


class GuardianPublisher:
    """Registered exclusively in systemd-notify watch mode, never by a pod."""

    def __init__(self, ledger: PodReservationLedger, socket_path: Path) -> None:
        self.ledger = ledger
        self.socket_path = socket_path
        manager = os.environ.get("NOTIFY_SOCKET", "")
        invocation = os.environ.get("INVOCATION_ID", "").lower()
        if not manager or not _INVOCATION.fullmatch(invocation):
            raise AdmissionDenied("guardian requires systemd Type=notify supervision")
        self.manager = manager
        self.invocation = invocation
        self.pid = os.getpid()
        self.boot, self.start = _kernel_identity(self.pid)
        _proc_supervisor(self.pid, invocation)
        self.epoch = secrets.token_hex(32)
        self.ledger_dev, self.ledger_ino = _ledger_identity(ledger)
        self.socket, self.socket_dev, self.socket_ino = _socket_identity(socket_path)
        with ledger._connect() as db:
            ledger._tx(db)
            _schema(db)
            old = db.execute(
                "SELECT * FROM pod_stage_guardian WHERE singleton=1"
            ).fetchone()
            if old is not None:
                try:
                    _attest(old, ledger, socket_path, check_age=True)
                except AdmissionDenied:
                    pass
                else:
                    raise AdmissionDenied("another healthy guardian owns this ledger")
            db.execute(
                "INSERT INTO pod_stage_guardian_audit(epoch_sha256,event,stamp)"
                " VALUES (?,?,?)",
                (hashlib.sha256(self.epoch.encode("ascii")).hexdigest(),
                 "registered", ledger._now()),
            )
            db.execute(
                "INSERT OR REPLACE INTO pod_stage_guardian "
                "(singleton,epoch,pid,process_start,boot_id,invocation_id,state,"
                "heartbeat_monotonic_ns,ledger_dev,ledger_ino,socket_path,socket_dev,socket_ino)"
                " VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.epoch, self.pid, self.start, self.boot, self.invocation,
                 "blocked", time.monotonic_ns(), self.ledger_dev, self.ledger_ino,
                 self.socket, self.socket_dev, self.socket_ino),
            )

    def notify(self, text: str) -> None:
        """Use actual sd_notify datagrams; failure is never silently ignored."""
        target = self.manager
        if target.startswith("@"):
            target = "\x00" + target[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as transport:
                transport.settimeout(1)
                transport.sendto(text.encode("ascii"), target)
        except OSError as exc:
            raise AdmissionDenied("systemd guardian watchdog unavailable") from exc

    def publish(self, phase: _Phase) -> None:
        if phase not in ("ready", "blocked"):
            raise AdmissionDenied("invalid guardian phase")
        with self.ledger._connect() as db:
            self.ledger._tx(db)
            current = db.execute(
                "SELECT epoch,pid,process_start,state FROM pod_stage_guardian "
                "WHERE singleton=1"
            ).fetchone()
            if current is None or (
                current["epoch"], current["pid"], current["process_start"]
            ) != (self.epoch, self.pid, self.start):
                raise AdmissionDenied("guardian publisher epoch no longer current")
            db.execute(
                "UPDATE pod_stage_guardian "
                "SET state=?,heartbeat_monotonic_ns=? WHERE singleton=1",
                (phase, time.monotonic_ns()),
            )
            if current["state"] != phase:
                db.execute(
                    "INSERT INTO pod_stage_guardian_audit("
                    "epoch_sha256,event,stamp) VALUES (?,?,?)",
                    (hashlib.sha256(self.epoch.encode("ascii")).hexdigest(),
                     phase, self.ledger._now()),
                )
        self.notify("READY=1\nSTATUS=Locus Docker guardian active" if phase == "ready"
                    else "STATUS=Locus Docker guardian reconciliation blocked")
        self.notify("WATCHDOG=1")
