"""LOCUS-467: mandatory native guardian evidence and revocation."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_guardian import (
    _schema, require_guardian_ready,
)
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.stage_guardian_support import managed_guardian_for_test
from tests.unit.test_stage_docker_launch import (
    ONLY_LINUX_UNPRIVILEGED, rig, run_command,
)


@ONLY_LINUX_UNPRIVILEGED
def test_no_guardian_denies_before_durable_receipt(tmp_path):
    original, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(original, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        with pytest.raises(AdmissionDenied, match="guardian not registered"):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0
        assert (original / "code.txt").read_text() == "original"


@ONLY_LINUX_UNPRIVILEGED
def test_foreign_forged_guardian_pid_denied(tmp_path):
    _, _, _, receipts, _, _ = rig(tmp_path)
    with receipts.reservations._connect() as db:
        receipts.reservations._tx(db)
        _schema(db)
        db.execute(
            "INSERT INTO pod_stage_guardian "
            "(singleton,epoch,pid,process_start,boot_id,invocation_id,state,"
            "heartbeat_monotonic_ns,ledger_dev,ledger_ino,socket_path,socket_dev,socket_ino) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("a" * 64, 987654321, 1,
             "12345678-1234-1234-1234-1234567890ab", "a" * 32,
             "ready", time.monotonic_ns(), 1, 1,
             "/var/run/docker.sock", 1, 1),
        )
    with pytest.raises(AdmissionDenied):
        require_guardian_ready(
            PodReservationLedger(tmp_path / "runs.db"), Path("/var/run/docker.sock")
        )


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="requires real Unix Docker socket and independent guardian process",
)
def test_real_guardian_liveness_boot_heartbeat_and_restart_epoch(tmp_path):
    _, _, _, receipts, _, _ = rig(tmp_path)
    authority = PodReservationLedger(tmp_path / "runs.db")
    sock = Path("/var/run/docker.sock")
    with managed_guardian_for_test(tmp_path / "runs.db") as first:
        old = require_guardian_ready(authority, sock)
        assert len(old) == 64
        with authority._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM pod_stage_guardian").fetchone()
        assert row["pid"] == first.pid
        assert row["state"] == "ready"
        assert row["heartbeat_monotonic_ns"] > 0
        first.kill()
        first.wait(timeout=4)
        with pytest.raises(AdmissionDenied):
            require_guardian_ready(authority, sock)
    with managed_guardian_for_test(tmp_path / "runs.db"):
        second = require_guardian_ready(authority, sock)
        assert second != old
        with authority._connect() as db:
            authority._tx(db)
            db.execute(
                "UPDATE pod_stage_guardian SET boot_id=?",
                ("00000000-0000-0000-0000-000000000000",),
            )
        with pytest.raises(AdmissionDenied):
            require_guardian_ready(authority, sock)
    # The dead second PID never grants access, even if the record says ready.
    with pytest.raises(AdmissionDenied):
        require_guardian_ready(authority, sock)


def test_systemd_unit_requires_restart_watchdog_and_notify():
    unit = (
        Path(__file__).resolve().parents[2]
        / "install" / "systemd" / "locus-stage-guardian.service"
    )
    body = unit.read_text(encoding="utf-8")
    for contract in (
        "Type=notify", "WatchdogSec=10s", "Restart=always",
        "RestartSec=1s", "NoNewPrivileges=yes",
        "EnvironmentFile=%h/.config/locus/stage-guardian.env",
        "--watch --poll-seconds 0.5",
    ):
        assert contract in body
