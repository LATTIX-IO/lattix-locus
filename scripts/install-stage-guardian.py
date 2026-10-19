#!/usr/bin/env python3
"""Install a fail-closed per-user systemd stage Docker guardian.

Operator-side only. The service shares the *existing* PodReservationLedger
and Docker socket with Locus, and may never run as root. Without the service
the StageDockerBroker refuses all child execution.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_guardian import require_guardian_ready
from locus_runtime.pod_stage_recovery import _private_ledger
from locus_runtime.pod_stage_guardian import _socket_identity


UNIT = "locus-stage-guardian.service"
UNIT_SOURCE = (
    Path(__file__).resolve().parent.parent / "install" / "systemd" / UNIT
)


def _owned_directory(path: Path) -> None:
    if not path.is_absolute():
        raise AdmissionDenied("guardian config directory must be absolute")
    if path.exists():
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise AdmissionDenied("guardian config directory not owned mode 0700")
    else:
        path.mkdir(mode=0o700)
        metadata = path.lstat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise AdmissionDenied("cannot establish private guardian config directory")


def _quote(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise AdmissionDenied("systemd environment value contains control bytes")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _replace_private(path: Path, data: str) -> None:
    raw_fd, temp = tempfile.mkstemp(prefix=".locus-guardian-", dir=path.parent)
    try:
        with os.fdopen(raw_fd, "w", encoding="utf-8") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _systemctl(*arguments: str) -> None:
    proc = subprocess.run(
        ["systemctl", "--user", *arguments],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, shell=False,
    )
    if proc.returncode:
        raise AdmissionDenied(
            "systemd user manager rejected guardian service installation"
        )


def install(
    *, ledger: Path, docker_socket: Path, python: Path,
    home: Path,
) -> None:
    if sys.platform != "linux" or os.geteuid() == 0:
        raise AdmissionDenied("use a nonroot Linux systemd user session")
    _private_ledger(ledger)
    _socket_identity(docker_socket)
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise AdmissionDenied("guardian Python executable unavailable")
    if not UNIT_SOURCE.is_file():
        raise AdmissionDenied("systemd guardian unit template unavailable")

    # The selected interpreter must import the installed Locus package even
    # outside the repository working directory.
    verify = subprocess.run(
        [str(python), "-c", "import locus_runtime.pod_stage_recovery"],
        cwd=str(home), check=False, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=12,
    )
    if verify.returncode:
        raise AdmissionDenied("selected Python does not contain installed Locus")

    config = home / ".config" / "locus"
    unit_dir = home / ".config" / "systemd" / "user"
    _owned_directory(config)
    _owned_directory(home / ".config" / "systemd")
    _owned_directory(unit_dir)
    _replace_private(
        config / "stage-guardian.env",
        "LOCUS_STAGE_LEDGER=" + _quote(str(ledger)) + "\n"
        + "LOCUS_DOCKER_SOCKET=" + _quote(str(docker_socket)) + "\n"
        + "LOCUS_PYTHON=" + _quote(str(python)) + "\n",
    )
    _replace_private(unit_dir / UNIT, UNIT_SOURCE.read_text(encoding="utf-8"))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", UNIT)
    authority = PodReservationLedger(ledger)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            require_guardian_ready(authority, docker_socket)
        except AdmissionDenied:
            time.sleep(0.25)
        else:
            _systemctl("is-active", "--quiet", UNIT)
            print("Guardian registered and active under systemd user supervision")
            return
    raise AdmissionDenied("guardian readiness gate did not become healthy")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--socket", default="/var/run/docker.sock")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    try:
        install(
            ledger=Path(args.ledger).resolve(strict=True),
            docker_socket=Path(args.socket).resolve(strict=True),
            python=Path(args.python).resolve(strict=True),
            home=Path.home().resolve(strict=True),
        )
    except (AdmissionDenied, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("Guardian install failed closed: " + type(exc).__qualname__,
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
