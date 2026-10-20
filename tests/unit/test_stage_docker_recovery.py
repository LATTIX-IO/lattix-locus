"""LOCUS-466: restart orphan reconciliation (unit and real kernel crash).

No synthetic-only test can establish Docker behavior after SIGKILL. The real
acceptance fork is opt-in and MUST execute with a real local Linux daemon.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from locus_runtime import pod_stage_docker, pod_stage_recovery
from locus_runtime.action_fencing import action_lock
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_docker import StageDockerBroker
from locus_runtime.pod_stage_recovery import StageDockerRecovery
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.stage_guardian_support import managed_guardian_for_test
from tests.unit.test_stage_docker_launch import (
    ONLY_LINUX_UNPRIVILEGED, _source_inventory, rig, run_command,
)


def _create_uncertain_launch(tmp_path, monkeypatch, *, image: str | None = None):
    """Prepare a verified stage but cause a Docker-daemon invocation failure."""
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    if image is not None:
        broker = StageDockerBroker(stages, receipts, image=image)
    fixture = staged_workspace(src, staging_parent=tmp_path)
    snapshot = fixture.__enter__()
    bound = stages.bind(lease, snapshot)
    with monkeypatch.context() as patched:
        patched.setattr(
            pod_stage_docker, "_trusted_local_binary",
            lambda _: ("/usr/bin/docker", "e" * 64),
        )
        patched.setattr(
            pod_stage_docker, "_local_socket",
            lambda _: (
                "unix://" + os.path.realpath("/var/run/docker.sock")
                if image is not None else "unix:///var/run/docker.sock"
            ),
        )
        patched.setattr(
            pod_stage_docker, "require_guardian_ready",
            lambda *_: "test-guardian-epoch",
        )

        class Refused:
            def __init__(self, argv, **kwargs):
                self.returncode = None

            def wait(self, timeout):
                self.returncode = 125
                return 125

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        patched.setattr(pod_stage_docker.subprocess, "Popen", Refused)
        patched.setattr(
            pod_stage_docker.subprocess, "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1),
        )
        with pytest.raises(AdmissionDenied, match="launch outcome ambiguous"):
            broker.execute(lease, bound.stage_id, run_command(snapshot))
    return src, lease, receipts, stages, fixture, snapshot, bound


def _row(ledger: PodReservationLedger, stage_id: str):
    with ledger._connect() as db:
        return db.execute(
            "SELECT l.*,s.staged_path FROM pod_stage_docker_launch l "
            "JOIN pod_stage_binding s ON s.id=l.stage_id "
            "WHERE l.stage_id=?", (stage_id,),
        ).fetchone()


def _fake_inspection(row, *, wrong_proof: bool = False):
    return {
        "Id": "a" * 64,
        "Name": "/" + row["container_name"],
        "Config": {
            "Image": row["image_ref"],
            "Labels": {
                "io.lattix.locus.stage-only": "v1",
                "io.lattix.locus.stage-proof":
                    "a" * 64 if wrong_proof else row["stage_proof"],
            },
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Binds": None,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges", "seccomp=/trusted/profile.json"],
        },
        "Mounts": [{
            "Type": "bind", "Source": row["staged_path"],
            "Destination": "/workspace", "RW": True,
        }],
    }


@ONLY_LINUX_UNPRIVILEGED
def test_recovery_reaps_only_proven_container_and_retains_parent_budget(tmp_path, monkeypatch):
    src, lease, receipts, stages, context, snapshot, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        row = _row(ledger, bound.stage_id)
        live = [_fake_inspection(row)]
        deleted: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            pod_stage_recovery, "_trusted_local_binary",
            lambda _: ("/usr/bin/docker", "e" * 64),
        )
        monkeypatch.setattr(
            pod_stage_recovery, "_local_socket",
            lambda _: "unix:///var/run/docker.sock",
        )
        monkeypatch.setattr(
            pod_stage_recovery, "_inspect",
            lambda *args: live[0] if live else None,
        )

        def fake_docker(*, binary, socket, params):
            deleted.append(params)
            live.clear()
            return subprocess.CompletedProcess(params, 0, stdout=b"", stderr=b"")

        monkeypatch.setattr(pod_stage_recovery, "_docker", fake_docker)
        before = ledger.snapshot("docker-parent")
        assert before.held.actions == 3
        result = StageDockerRecovery(ledger).recover_pending()
        assert [r.outcome for r in result] == ["removed"]
        assert deleted == [("container", "rm", "--force", "a" * 64)]
        assert receipts.get(row["action_id"]).state == "uncertain"
        assert ledger.get(lease.reservation_id).state == "uncertain"
        assert ledger.snapshot("docker-parent").held == before.held
        assert ledger.snapshot("docker-parent").available == before.available
        assert (src / "code.txt").read_text() == "original"
        assert stages.inspect_executable is not None
        # A second recovery never dispatches work or frees the reservation.
        assert [r.outcome for r in StageDockerRecovery(ledger).recover_pending()] == [
            "absent-observed"
        ]
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            receipts.execute_prepared_once(lease, row["action_id"], invoke=lambda: 7)
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_forged_proof_is_never_killed_or_accepted(tmp_path, monkeypatch):
    src, lease, receipts, _, context, _, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        row = _row(ledger, bound.stage_id)
        monkeypatch.setattr(
            pod_stage_recovery, "_local_socket",
            lambda _: "unix:///var/run/docker.sock",
        )
        monkeypatch.setattr(
            pod_stage_recovery, "_trusted_local_binary",
            lambda _: ("/usr/bin/docker", "e" * 64),
        )
        monkeypatch.setattr(
            pod_stage_recovery, "_inspect",
            lambda *args: _fake_inspection(row, wrong_proof=True),
        )
        def forbidden(*args, **kwargs):
            pytest.fail("unproven container must not be deleted")
        monkeypatch.setattr(pod_stage_recovery, "_docker", forbidden)
        reports = StageDockerRecovery(ledger).recover_pending()
        assert reports[0].outcome == "needs-review"
        assert ledger.get(lease.reservation_id).state == "uncertain"
        assert ledger.snapshot("docker-parent").held.actions == 3
        assert receipts.get(row["action_id"]).state == "uncertain"
        assert (src / "code.txt").read_text() == "original"
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_live_dispatch_lock_blocks_orphan_recovery(tmp_path, monkeypatch):
    _, lease, receipts, _, context, _, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        row = _row(ledger, bound.stage_id)
        with action_lock(ledger.path, row["action_id"]) as locked:
            assert locked
            reports = StageDockerRecovery(ledger).recover_pending()
            assert [r.outcome for r in reports] == ["busy"]
        assert ledger.get(lease.reservation_id).state == "active"
        assert receipts.get(row["action_id"]).state == "uncertain"
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_tampered_provenance_requires_manual_recovery(tmp_path, monkeypatch):
    _, lease, receipts, _, context, _, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        with ledger._connect() as db:
            ledger._tx(db)
            db.execute(
                "UPDATE pod_stage_docker_launch SET stage_proof=? WHERE stage_id=?",
                ("f" * 64, bound.stage_id),
            )
        reports = StageDockerRecovery(ledger).recover_pending()
        assert [r.outcome for r in reports] == ["needs-review"]
        assert ledger.snapshot("docker-parent").held.actions == 3
        assert receipts.get(_row(ledger, bound.stage_id)["action_id"]).state == "uncertain"
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_late_docker_rpc_after_empty_scan_is_not_permanently_cleared(tmp_path, monkeypatch):
    """First absence is only observed; a subsequent run can reap a late start."""
    _, lease, receipts, _, context, _, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        row = _row(ledger, bound.stage_id)
        late = [_fake_inspection(row)]
        scans = [0]
        removed: list[str] = []
        monkeypatch.setattr(
            pod_stage_recovery, "_local_socket",
            lambda _: "unix:///var/run/docker.sock",
        )
        monkeypatch.setattr(
            pod_stage_recovery, "_trusted_local_binary",
            lambda _: ("/usr/bin/docker", "e" * 64),
        )

        def discover(*args):
            scans[0] += 1
            # First reaper pass cannot see the daemon's delayed create.
            if scans[0] == 1:
                return None
            return late[0] if late else None

        def delete(*, binary, socket, params):
            removed.append(params[-1])
            late.clear()
            return subprocess.CompletedProcess(params, 0, stdout=b"", stderr=b"")

        monkeypatch.setattr(pod_stage_recovery, "_inspect", discover)
        monkeypatch.setattr(pod_stage_recovery, "_docker", delete)
        recovery = StageDockerRecovery(ledger)
        assert recovery.recover_pending()[0].outcome == "absent-observed"
        assert ledger.get(lease.reservation_id).state == "uncertain"
        assert receipts.get(row["action_id"]).state == "uncertain"
        assert ledger.snapshot("docker-parent").held.actions == 3
        assert recovery.recover_pending()[0].outcome == "removed"
        assert removed == ["a" * 64]
        assert ledger.snapshot("docker-parent").held.actions == 3
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_daemon_unavailable_does_not_release_or_replay(tmp_path, monkeypatch):
    _, lease, receipts, _, context, _, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")

        def unavailable(_):
            raise AdmissionDenied("local daemon not reachable")

        monkeypatch.setattr(pod_stage_recovery, "_local_socket", unavailable)
        outcome = StageDockerRecovery(ledger).recover_pending()
        assert [r.outcome for r in outcome] == ["needs-review"]
        assert ledger.get(lease.reservation_id).state == "uncertain"
        assert ledger.snapshot("docker-parent").held.actions == 3
        action_id = _row(ledger, bound.stage_id)["action_id"]
        assert receipts.get(action_id).state == "uncertain"
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            receipts.execute_prepared_once(lease, action_id, invoke=lambda: 1)
    finally:
        context.__exit__(None, None, None)


@ONLY_LINUX_UNPRIVILEGED
def test_guardian_does_not_steal_recent_live_prepared_receipt(tmp_path, monkeypatch):
    """A watcher may see a new prepared row before broker obtains its OS lock."""
    _, lease, receipts, _, context, _, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch)
    )
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        row = _row(ledger, bound.stage_id)
        with ledger._connect() as db:
            ledger._tx(db)
            db.execute(
                "UPDATE pod_action_receipt SET state='prepared',outcome_digest=NULL "
                "WHERE id=?", (row["action_id"],),
            )
            db.execute(
                "UPDATE pod_stage_docker_launch SET state='prepared' WHERE stage_id=?",
                (bound.stage_id,),
            )
        monkeypatch.setattr(
            pod_stage_recovery, "_local_socket",
            lambda _: "unix:///var/run/docker.sock",
        )
        with monkeypatch.context() as patched:
            def no_docker(_):
                pytest.fail("guardian must not touch Docker before grace expires")
            patched.setattr(pod_stage_recovery, "_trusted_local_binary", no_docker)
            pending = StageDockerRecovery(ledger).recover_pending()
        assert [r.outcome for r in pending] == ["busy"]
        assert receipts.get(row["action_id"]).state == "prepared"
        assert ledger.get(lease.reservation_id).state == "active"
        # Once stale, an authenticated restart may conservatively mark the
        # never-dispatched prepared intent uncertain without making a launch.
        with ledger._connect() as db:
            ledger._tx(db)
            db.execute(
                "UPDATE pod_stage_docker_launch SET stamp=? WHERE stage_id=?",
                (ledger._now() - 30, bound.stage_id),
            )
        monkeypatch.setattr(
            pod_stage_recovery, "_trusted_local_binary",
            lambda _: ("/usr/bin/docker", "e" * 64),
        )
        monkeypatch.setattr(
            pod_stage_recovery, "_inspect", lambda *args: None,
        )
        assert StageDockerRecovery(ledger).recover_pending()[0].outcome == "absent-observed"
        assert receipts.get(row["action_id"]).state == "uncertain"
        assert ledger.snapshot("docker-parent").held.actions == 3
    finally:
        context.__exit__(None, None, None)


def _docker_names() -> str:
    command = subprocess.run(
        ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock",
         "ps", "-a", "--filter", "name=locus-pod-",
         "--format", "{{.Names}}"],
        shell=False, capture_output=True, timeout=6, check=True,
    )
    return command.stdout.decode("utf-8").strip()


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="must use a real local Docker Engine",
)
def test_sigkill_supervisor_restart_reaps_orphan_and_fences_budget(tmp_path):
    image = os.environ["LOCUS_DOCKER_ACCEPTANCE_IMAGE"]
    src, lease, _, receipts, stages, _ = rig(tmp_path, executable="sleep")
    before = _source_inventory(src)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ):
        stage = stages.bind(lease, snapshot)
        broker = StageDockerBroker(stages, receipts, image=image)
        pid = os.fork()
        if pid == 0:
            try:
                broker.execute(
                    lease, stage.stage_id,
                    run_command(snapshot, executable="sleep", argument="30",
                                timeout=25),
                )
            except Exception:
                os._exit(19)
            os._exit(0)
        try:
            deadline = time.monotonic() + 14
            started = False
            while time.monotonic() < deadline:
                with receipts.reservations._connect() as db:
                    rows = db.execute(
                        "SELECT state FROM pod_stage_docker_launch "
                        "WHERE stage_id=?", (stage.stage_id,),
                    ).fetchall()
                if rows and rows[0]["state"] == "dispatching" and _docker_names():
                    started = True
                    break
                time.sleep(0.1)
            assert started, "Docker child was not launched before forced supervisor crash"
        finally:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)

        # A genuinely independent restarted supervisor has no in-memory
        # GatewaySession and cannot steal the independent guardian's action
        # lock. Return code 3 is a transient, explicit BUSY result, never a
        # successful cleanup claim. Wait until one reconciler has completed.
        deadline = time.monotonic() + 14
        while True:
            recovered = subprocess.run(
                [sys.executable, "-m", "locus_runtime.pod_stage_recovery",
                 "--ledger", str(tmp_path / "runs.db")],
                shell=False, check=False, capture_output=True, timeout=10,
            )
            reports = json.loads(recovered.stdout.decode("utf-8"))
            if recovered.returncode == 0:
                assert reports and all(
                    r["outcome"] in {"removed", "absent-observed"}
                    for r in reports
                )
                assert all(r["reservation_held"] is True for r in reports)
                break
            # Only an authenticated action-lock collision is retryable.
            assert recovered.returncode == 3, recovered.stderr[-500:]
            assert reports and all(r["outcome"] == "busy" for r in reports)
            assert time.monotonic() < deadline, "reconciliation lock stayed busy"
            time.sleep(0.15)
        assert _docker_names() == ""
        assert _source_inventory(src) == before
        reopened = PodReservationLedger(tmp_path / "runs.db")
        assert reopened.get(lease.reservation_id).state == "uncertain"
        assert reopened.snapshot("docker-parent").held.actions == 3
        with reopened._connect() as db:
            state = db.execute(
                "SELECT state FROM pod_action_receipt",
            ).fetchone()[0]
            assert state == "uncertain"
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            broker.execute(
                lease, stage.stage_id,
                run_command(snapshot, executable="sleep", argument="30", timeout=25),
            )
    assert _source_inventory(src) == before


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="must use a real local Docker Engine",
)
def test_independent_guardian_reaps_without_waiting_for_supervisor_restart(tmp_path):
    """Guardian is its own OS process; supervisor SIGKILL never terminates it."""
    image = os.environ["LOCUS_DOCKER_ACCEPTANCE_IMAGE"]
    src, lease, _, receipts, stages, _ = rig(tmp_path, executable="sleep")
    before = _source_inventory(src)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ) as watchdog:
        stage = stages.bind(lease, snapshot)
        broker = StageDockerBroker(stages, receipts, image=image)
        pid: int | None = None
        try:
            assert watchdog.poll() is None, "independent guardian did not remain alive"
            pid = os.fork()
            if pid == 0:
                try:
                    broker.execute(
                        lease, stage.stage_id,
                        run_command(snapshot, executable="sleep", argument="30",
                                    timeout=25),
                    )
                except Exception:
                    os._exit(21)
                os._exit(0)
            deadline = time.monotonic() + 15
            active = False
            while time.monotonic() < deadline:
                with receipts.reservations._connect() as db:
                    row = db.execute(
                        "SELECT state FROM pod_stage_docker_launch WHERE stage_id=?",
                        (stage.stage_id,),
                    ).fetchone()
                if row is not None and row["state"] == "dispatching" and _docker_names():
                    active = True
                    break
                time.sleep(0.1)
            assert active, "guardian preempted live child or container could not start"
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            pid = None
            # A separate continuously running guardian, not this test and
            # not a restarted supervisor, must observe and stop the orphan.
            deadline = time.monotonic() + 14
            cleared = False
            while time.monotonic() < deadline:
                if _docker_names() == "":
                    with receipts.reservations._connect() as db:
                        row = db.execute(
                            "SELECT state FROM pod_action_receipt",
                        ).fetchone()
                    if row is not None and row["state"] == "uncertain":
                        cleared = True
                        break
                time.sleep(0.2)
            assert cleared, "guardian did not independently reap crashed child"
            assert watchdog.poll() is None, "guardian stopped after supervisor death"
            assert receipts.reservations.get(lease.reservation_id).state == "uncertain"
            assert receipts.reservations.snapshot("docker-parent").held.actions == 3
            assert _source_inventory(src) == before
        finally:
            if pid is not None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
    assert _docker_names() == ""


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="delayed daemon-create requires real local Engine",
)
def test_guardian_reaps_late_create_after_prior_absent_observation(tmp_path, monkeypatch):
    """A daemon-late container is still scoped/reaped after the empty first pass."""
    image = os.environ["LOCUS_DOCKER_ACCEPTANCE_IMAGE"]
    src, lease, receipts, _, context, snapshot, bound = (
        _create_uncertain_launch(tmp_path, monkeypatch, image=image)
    )
    before = _source_inventory(src)
    try:
        ledger = PodReservationLedger(tmp_path / "runs.db")
        row = _row(ledger, bound.stage_id)
        with managed_guardian_for_test(tmp_path / "runs.db") as guardian:
            end = time.monotonic() + 8
            while time.monotonic() < end:
                with ledger._connect() as db:
                    outcomes = [r[0] for r in db.execute(
                        "SELECT outcome FROM pod_stage_recovery_audit "
                        "WHERE stage_id=?", (bound.stage_id,),
                    )]
                if "absent-observed" in outcomes:
                    break
                time.sleep(0.1)
            assert "absent-observed" in outcomes
            # Simulate a Docker daemon creating a previously dispatched,
            # provenance-labeled container after a prior empty observation.
            # No new Gateway receipt or stage mutation is authorized.
            cmd = [
                "/usr/bin/docker", "--host", "unix:///var/run/docker.sock",
                "run", "--rm", "-d", "--pull=never",
                "--name", row["container_name"],
                "--label=io.lattix.locus.stage-only=v1",
                "--label=io.lattix.locus.stage-proof=" + row["stage_proof"],
                "--network=none", "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--security-opt=seccomp=" + str(
                    pod_stage_docker._validated_seccomp_profile_path()
                ),
                "--read-only",
                "--user=" + str(os.geteuid()) + ":" + str(os.getegid()),
                "--ipc=private", "--cgroupns=private",
                "--memory=256m", "--cpus=1", "--pids-limit=64",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                "--mount", "type=bind,source=" + str(snapshot.staged)
                + ",target=/workspace,bind-propagation=rprivate,bind-recursive=disabled",
                "--workdir=/workspace", "--entrypoint=sleep", image, "30",
            ]
            spawned = subprocess.run(
                cmd, shell=False, capture_output=True, timeout=10, check=False,
            )
            assert spawned.returncode == 0, spawned.stderr[-700:]
            end = time.monotonic() + 12
            removed = False
            while time.monotonic() < end:
                with ledger._connect() as db:
                    outcomes = [r[0] for r in db.execute(
                        "SELECT outcome FROM pod_stage_recovery_audit "
                        "WHERE stage_id=? ORDER BY sequence",
                        (bound.stage_id,),
                    )]
                if "removed" in outcomes and _docker_names() == "":
                    removed = True
                    break
                time.sleep(0.2)
            assert removed, "independent guardian failed to reap late daemon create"
            assert guardian.poll() is None
            assert ledger.get(lease.reservation_id).state == "uncertain"
            assert ledger.snapshot("docker-parent").held.actions == 3
            assert receipts.get(row["action_id"]).state == "uncertain"
            assert _source_inventory(src) == before
    finally:
        context.__exit__(None, None, None)
