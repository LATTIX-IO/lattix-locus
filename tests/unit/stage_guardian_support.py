"""Real OS-process recovery guardian for Docker integration tests only.

The ephemeral AF_UNIX notify receiver approximates the sd_notify protocol.
It is NOT evidence of real systemd restart supervision; a separate systemd
acceptance is required for the production service unit.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from locus_runtime.pod_reservations import PodReservationLedger
from locus_runtime.pod_stage_guardian import require_guardian_ready


@contextmanager
def managed_guardian_for_test(
    ledger_path: Path, *, poll_seconds: str = "0.25",
) -> Iterator[subprocess.Popen[bytes]]:
    with tempfile.TemporaryDirectory(prefix="locus-notify-") as temp:
        notify = Path(temp) / "systemd.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(notify))
            receiver.settimeout(0.2)
            env = os.environ.copy()
            env.update({
                "INVOCATION_ID": secrets.token_hex(16),
                "NOTIFY_SOCKET": str(notify),
            })
            proc = subprocess.Popen(
                [sys.executable, "-m", "locus_runtime.pod_stage_recovery",
                 "--ledger", str(ledger_path), "--watch",
                 "--poll-seconds", poll_seconds],
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
                env=env,
            )
            try:
                deadline = time.monotonic() + 14
                ready = False
                while time.monotonic() < deadline and proc.poll() is None:
                    try:
                        packet = receiver.recv(8192)
                    except socket.timeout:
                        continue
                    if b"READY=1" in packet:
                        ready = True
                        break
                assert ready, "systemd-notify recovery guardian did not reach READY"
                ledger = PodReservationLedger(ledger_path)
                assert require_guardian_ready(ledger, Path("/var/run/docker.sock"))
                yield proc
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=6)
