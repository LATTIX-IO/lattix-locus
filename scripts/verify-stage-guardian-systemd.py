#!/usr/bin/env python3
"""Real Linux systemd manager acceptance of guardian restart after SIGKILL.

Runs only on an ephemeral, explicitly authorized Linux CI worker with sudo.
The service itself runs as the unprivileged current user, never as root.
"""
from __future__ import annotations

import getpass
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_guardian import require_guardian_ready


def _sudo(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["sudo", "-n", *args], text=True, capture_output=True,
        check=False, timeout=25,
    )
    if result.returncode:
        raise RuntimeError("systemd command failed: " + args[0] + ": " + result.stderr.strip()[-240:])
    return result


def _ready(ledger: PodReservationLedger, *, previous: str = "") -> tuple[str, int]:
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        try:
            epoch = require_guardian_ready(ledger, Path("/var/run/docker.sock"))
        except AdmissionDenied:
            time.sleep(0.2)
            continue
        with ledger._connect() as db:
            row = db.execute(
                "SELECT pid FROM pod_stage_guardian WHERE singleton=1"
            ).fetchone()
        if row is not None and epoch != previous:
            return epoch, int(row["pid"])
        time.sleep(0.2)
    raise RuntimeError("OS-managed guardian did not become freshly ready")


def main() -> int:
    if sys.platform != "linux" or os.geteuid() == 0:
        raise RuntimeError("systemd acceptance requires unprivileged Linux CI user")
    if not Path("/run/systemd/system").exists():
        raise RuntimeError("real systemd host manager is required for acceptance")
    if not Path("/var/run/docker.sock").is_socket():
        raise RuntimeError("real local Docker Engine is required")
    root = Path(__file__).resolve().parent.parent
    username = getpass.getuser()
    unit = "locus-guardian-acceptance-" + secrets.token_hex(6) + ".service"
    with tempfile.TemporaryDirectory(prefix="locus-guardian-systemd-") as folder:
        ledger = PodReservationLedger(Path(folder) / "runs.db")
        service = [
            "systemd-run", "--unit=" + unit, "--service-type=notify",
            "--property=User=" + username,
            "--property=WorkingDirectory=" + str(root),
            "--property=Restart=always", "--property=RestartSec=1s",
            "--property=WatchdogSec=10s",
            "--setenv=PYTHONPATH=" + str(root),
            "--setenv=PYTHONUNBUFFERED=1",
            sys.executable, "-m", "locus_runtime.pod_stage_recovery",
            "--ledger", ledger.path, "--socket", "/var/run/docker.sock",
            "--watch", "--poll-seconds", "0.25",
        ]
        try:
            _sudo(*service)
            print("systemd transient unit created", flush=True)
            first_epoch, first_pid = _ready(ledger)
            print("initial guardian heartbeat validated", flush=True)
            manager_pid = int(_sudo(
                "systemctl", "show", "--value", "-p", "MainPID", unit
            ).stdout.strip())
            if first_pid != manager_pid:
                raise RuntimeError("guardian PID not systemd managed MainPID")
            _sudo("kill", "-KILL", str(first_pid))
            print("guardian SIGKILL dispatched", flush=True)
            second_epoch, second_pid = _ready(ledger, previous=first_epoch)
            print("replacement guardian heartbeat validated", flush=True)
            if second_pid == first_pid:
                raise RuntimeError("systemd did not actually restart guardian")
            manager_pid = int(_sudo(
                "systemctl", "show", "--value", "-p", "MainPID", unit
            ).stdout.strip())
            if second_pid != manager_pid:
                raise RuntimeError("restarted guardian not owned by systemd")
            _sudo("systemctl", "is-active", "--quiet", unit)
            print("PASS: real systemd Type=notify restarted guardian and rotated epoch")
            return 0
        finally:
            # A wedged systemd manager must not replace the first failure
            # with a cleanup timeout; leave the job failed and observable.
            for operation in ("stop", "reset-failed"):
                try:
                    subprocess.run(
                        ["sudo", "-n", "systemctl", operation, unit],
                        check=False, capture_output=True, timeout=6,
                    )
                except subprocess.TimeoutExpired:
                    print("systemd cleanup did not confirm " + operation,
                          file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdmissionDenied, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("FAIL: systemd guardian acceptance: " + type(error).__qualname__ + ": " + str(error)[:240],
              file=sys.stderr)
        raise SystemExit(2) from error
