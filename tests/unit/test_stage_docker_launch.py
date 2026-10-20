"""LOCUS-466: stage-only Docker worker security tests.

Synthetic checks must NOT be mistaken for Engine/kernel evidence. The tests
gated by LOCUS_REAL_DOCKER_ACCEPTANCE invoke a real local Docker daemon.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from locus_runtime import pod_stage_docker as module
from locus_runtime.action_receipts import ActionReplayDenied, PodActionReceipts
from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ResourceBudget,
)
from locus_runtime.pod_sandbox_broker import BrokerCommand
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_stage_docker import StageDockerBroker, StagePodCancelled
from locus_runtime.pod_workspace_staging import staged_workspace
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from locus_runtime.task_scheduler import DurableTaskScheduler
from tests.gateway_support import FakeEngine
from tests.unit.stage_guardian_support import managed_guardian_for_test

IMAGE = "busybox@sha256:" + "a" * 64
ONLY_LINUX_UNPRIVILEGED = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="stage Docker contract requires an unprivileged Linux host",
)


def rig(tmp_path: Path, *, executable: str = "touch"):
    src = tmp_path / "source"
    src.mkdir()
    (src / "code.txt").write_text("original", encoding="utf-8")
    for private in (".git", ".aws", ".locus", ".config"):
        (src / private).mkdir()
        (src / private / "secret.txt").write_text("private", encoding="utf-8")
    ledger = PodReservationLedger(tmp_path / "runs.db")
    gateway = Gateway(FakeEngine(), lambda _: None)
    parent = gateway.open_session(
        run_id="docker-parent", principal="alice", engine="supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"run_command"}),
            allowed_executables=(executable,),
            read_roots=(str(src),),
            max_actions=5, max_tool_calls=5,
            max_risk_class=RiskClass.R2,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=500, duration_used_seconds=0,
                max_duration_seconds=60, cost_used_usd=0, max_cost_usd=0,
            ),
        ),
    )
    plan = compile_task_plan(
        {"contract_version": "1.0", "nodes": [{
            "node_id": "work", "depends_on": [], "capabilities": ["run_command"],
            "risk_ceiling": "R2",
            "budget": {
                "actions": 3, "tokens": 200, "wall_seconds": 40,
                "spend_microunits": 0,
            },
            "deliverables": ["staged output"],
            "acceptance_criteria": ["source remains unchanged"],
        }]},
        intent=TaskIntent(
            "Make an isolated staging output", "professional", "alice",
            ("do not touch the original workspace",),
        ),
        parent_authority=TaskAuthority(
            frozenset({"run_command"}), "R2",
            TaskBudget(5, 500, 60, 0),
        ),
    )
    scheduler = DurableTaskScheduler(ledger)
    scheduler.bind(plan, parent, max_tool_calls=5)
    scheduler.bind_engine_pins(plan, parent, (("work", "cli/test"),))
    claim = scheduler.claim_next(
        plan, parent, expected_engine_pins=(("work", "cli/test"),),
    )
    assert claim is not None
    scheduler.reserve(
        plan, parent, claim, budget=ResourceBudget(3, 2, 200, 30, 0),
        lease_seconds=45, pod_revision="docker-stage-v2",
    )
    lease = scheduler.admit(plan, parent, claim, engine="cli/test")
    receipts = PodActionReceipts(ledger, supervisor=parent)
    stages = PodStageBindings(ledger, receipts)
    broker = StageDockerBroker(stages, receipts, image=IMAGE)
    return src, lease, parent, receipts, stages, broker


def run_command(snapshot, *, executable="touch", argument="/workspace/output.txt",
                key="only-attempt", timeout=4):
    return BrokerCommand(
        "run_command", (executable, argument), str(snapshot.staged), key,
        timeout_seconds=timeout,
    )


def mock_host(monkeypatch: pytest.MonkeyPatch, written: Path | None = None):
    argv_seen: list[list[str]] = []
    monkeypatch.setattr(
        module, "_trusted_local_binary",
        lambda _: ("/usr/bin/docker", "e" * 64),
    )
    monkeypatch.setattr(
        module, "_local_socket",
        lambda _: "unix:///var/run/docker.sock",
    )
    # Synthetic CLI tests exercise the worker contract; real Engine tests
    # use a distinct process-authenticated guardian, never this patch.
    monkeypatch.setattr(
        module, "require_guardian_ready", lambda *_: "test-guardian-epoch",
    )

    class Process:
        def __init__(self, argv, **kwargs):
            assert kwargs["shell"] is False and kwargs["close_fds"] is True
            assert kwargs["env"]["DOCKER_CONFIG"] == kwargs["env"]["HOME"]
            assert "DOCKER_HOST" not in kwargs["env"]
            argv_seen.append(list(argv))
            self.returncode = None

        def wait(self, timeout):
            if written is not None:
                written.write_text("agent changed stage", encoding="utf-8")
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    return argv_seen


@ONLY_LINUX_UNPRIVILEGED
def test_stage_only_launch_is_receipted_and_mutation_tolerant(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch, snapshot.staged / "output.txt")
        result = broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert result.receipt.state == "succeeded"
        assert result.exit_code == 0
        assert len(calls) == 1
        cmd = calls[0]
        assert cmd[1:4] == ["--host", "unix:///var/run/docker.sock", "run"]
        assert "--network=none" in cmd
        assert "--cap-drop=ALL" in cmd
        assert "--read-only" in cmd
        assert "--pull=never" in cmd
        assert "-v" not in cmd
        mounts = [cmd[i + 1] for i, part in enumerate(cmd[:-1]) if part == "--mount"]
        assert len(mounts) == 1
        assert "source=" + str(snapshot.staged) in mounts[0]
        assert "bind-recursive=disabled" in mounts[0]
        assert str(src) not in str(cmd)
        assert str(snapshot.staged / "output.txt") != str(src / "output.txt")
        assert (src / "code.txt").read_text(encoding="utf-8") == "original"
        assert (snapshot.staged / "output.txt").read_text() == "agent changed stage"
        assert stages.inspect_executable(lease, stage.stage_id).stage_id == stage.stage_id
        with pytest.raises(AdmissionDenied):
            stages.inspect(lease, stage.stage_id)
        assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
            "prepared", "dispatching", "fence_guarded", "succeeded",
        ]
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert len(calls) == 1


@ONLY_LINUX_UNPRIVILEGED
def test_original_drift_fails_before_receipt(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        (src / "code.txt").write_text("changed by operator", encoding="utf-8")
        with pytest.raises(AdmissionDenied, match="original workspace changed"):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert calls == []
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


@ONLY_LINUX_UNPRIVILEGED
def test_staged_file_tamper_and_protected_path_fail_before_receipt(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        (snapshot.staged / "code.txt").write_text("unapproved", encoding="utf-8")
        with pytest.raises(AdmissionDenied):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert not calls
        (snapshot.staged / "code.txt").write_text("original", encoding="utf-8")
        (snapshot.staged / ".aws").mkdir()
        with pytest.raises(AdmissionDenied):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert not calls
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


@ONLY_LINUX_UNPRIVILEGED
def test_immutable_cli_image_and_stage_path_gates(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        with pytest.raises(AdmissionDenied):
            broker.execute(
                lease, stage.stage_id,
                BrokerCommand(
                    "run_command", ("touch", "/workspace/file"),
                    str(src), "bad-path",
                ),
            )
        with pytest.raises(AdmissionDenied):
            StageDockerBroker(stages, receipts, image="busybox:latest")
        assert not calls
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def _source_inventory(root: Path):
    result = {}
    for entry in root.rglob("*"):
        rel = str(entry.relative_to(root))
        data = entry.stat(follow_symlinks=False)
        result[rel] = (
            stat.S_IMODE(data.st_mode), data.st_uid, data.st_gid,
            hashlib.sha256(entry.read_bytes()).hexdigest() if entry.is_file() else None,
        )
    return result


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="explicit real Docker Engine acceptance only",
)
@pytest.mark.parametrize("mode", ["success", "timeout", "cancel", "revoke", "guardian_death"])
def test_real_kernel_host_workspace_integrity(tmp_path, mode):
    image = os.environ["LOCUS_DOCKER_ACCEPTANCE_IMAGE"]
    src, lease, parent, receipts, stages, _ = rig(
        tmp_path, executable="touch" if mode == "success" else "sleep",
    )
    before = _source_inventory(src)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ) as guardian:
        stage = stages.bind(lease, snapshot)
        broker = StageDockerBroker(stages, receipts, image=image)
        assert not any(
            (snapshot.staged / segment).exists()
            for segment in (".git", ".aws", ".locus", ".config")
        )
        cancel = threading.Event()
        if mode == "success":
            outcome = broker.execute(
                lease, stage.stage_id,
                run_command(snapshot, argument="/workspace/output.txt", timeout=15),
            )
            assert outcome.exit_code == 0
            assert (snapshot.staged / "output.txt").is_file()
            assert outcome.receipt.state == "succeeded"
        else:
            if mode == "cancel":
                timer = threading.Timer(0.5, cancel.set)
                timer.start()
            elif mode == "revoke":
                timer = threading.Timer(
                    0.5, lambda: receipts.reservations.revoke_parent(parent),
                )
                timer.start()
            elif mode == "guardian_death":
                timer = threading.Timer(0.5, guardian.kill)
                timer.start()
            try:
                expected = AdmissionDenied if mode in (
                    "revoke", "guardian_death"
                ) else (subprocess.TimeoutExpired, StagePodCancelled)
                with pytest.raises(expected):
                    broker.execute(
                        lease, stage.stage_id,
                        run_command(
                            snapshot, executable="sleep", argument="30",
                            timeout=1 if mode == "timeout" else 10,
                        ), cancel=cancel,
                    )
            finally:
                if mode in ("cancel", "revoke", "guardian_death"):
                    timer.join()
            with receipts.reservations._connect() as db:
                states = [r[0] for r in db.execute(
                    "SELECT state FROM pod_action_receipt"
                )]
            assert states == ["uncertain"]
        if mode == "revoke":
            with pytest.raises(AdmissionDenied):
                stages.inspect_executable(lease, stage.stage_id)
        else:
            assert stages.inspect_executable(lease, stage.stage_id)
        assert _source_inventory(src) == before
    assert not snapshot.staged.exists()


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("launch_status", [125, 126, 127])
def test_failed_daemon_launch_is_uncertain_and_not_replayable(
    tmp_path, monkeypatch, launch_status,
):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)

        class FailedProcess:
            def __init__(self, argv, **kwargs):
                calls.append(list(argv))
                self.returncode = None

            def wait(self, timeout):
                self.returncode = launch_status
                return launch_status

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(module.subprocess, "Popen", FailedProcess)
        monkeypatch.setattr(
            module.subprocess, "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1),
        )
        with pytest.raises(AdmissionDenied, match="launch outcome ambiguous"):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert len(calls) == 1
        with receipts.reservations._connect() as db:
            states = [r[0] for r in db.execute(
                "SELECT state FROM pod_action_receipt"
            )]
            launches = [r[0] for r in db.execute(
                "SELECT state FROM pod_stage_docker_launch"
            )]
        assert states == ["uncertain"]
        assert launches == ["uncertain"]
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert len(calls) == 1


@ONLY_LINUX_UNPRIVILEGED
def test_regular_nonzero_application_exit_has_completed_receipt(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)

        class CompletedApplication:
            def __init__(self, argv, **kwargs):
                calls.append(list(argv))
                self.returncode = None

            def wait(self, timeout):
                self.returncode = 7
                return 7

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(module.subprocess, "Popen", CompletedApplication)
        result = broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert result.exit_code == 7
        assert result.receipt.state == "succeeded"
        assert len(calls) == 1
        with receipts.reservations._connect() as db:
            assert tuple(db.execute(
                "SELECT state,exit_code FROM pod_stage_docker_launch"
            ).fetchone()) == ("finished", 7)
        assert (src / "code.txt").read_text() == "original"
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert len(calls) == 1


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.skipif(
    os.getenv("LOCUS_REAL_DOCKER_ACCEPTANCE") != "1",
    reason="actual Docker CLI application-exit propagation requires Engine",
)
def test_real_kernel_application_nonzero_is_not_ambiguous(tmp_path):
    src, lease, _, receipts, stages, _ = rig(tmp_path, executable="sh")
    before = _source_inventory(src)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ):
        stage = stages.bind(lease, snapshot)
        broker = StageDockerBroker(
            stages, receipts, image=os.environ["LOCUS_DOCKER_ACCEPTANCE_IMAGE"],
        )
        command = BrokerCommand(
            "run_command", ("sh", "-c", "exit 7"),
            str(snapshot.staged), "application-exit-7", timeout_seconds=10,
        )
        outcome = broker.execute(lease, stage.stage_id, command)
        assert outcome.exit_code == 7
        assert outcome.receipt.state == "succeeded"
        with receipts.reservations._connect() as db:
            row = db.execute(
                "SELECT state,exit_code FROM pod_stage_docker_launch"
            ).fetchone()
            assert tuple(row) == ("finished", 7)
        assert _source_inventory(src) == before
        with pytest.raises((AdmissionDenied, ActionReplayDenied)):
            broker.execute(lease, stage.stage_id, command)
    assert _source_inventory(src) == before


@ONLY_LINUX_UNPRIVILEGED
def test_pre_cancelled_child_has_no_receipt_or_launch(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        canceled = threading.Event()
        canceled.set()
        with pytest.raises(StagePodCancelled):
            broker.execute(
                lease, stage.stage_id, run_command(snapshot), cancel=canceled,
            )
        assert not calls
        with receipts.reservations._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM pod_action_receipt"
            ).fetchone()[0] == 0


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("attack", ("symlink", "hardlink", "case_collision"))
def test_hostile_stage_entry_cannot_reach_docker(tmp_path, monkeypatch, attack):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        if attack == "symlink":
            (snapshot.staged / "escape").symlink_to(src / "code.txt")
        elif attack == "hardlink":
            os.link(src / "code.txt", snapshot.staged / "escape")
        else:
            (snapshot.staged / "CODE.TXT").write_text("collision")
        with pytest.raises((AdmissionDenied, PermissionError)):
            broker.execute(lease, stage.stage_id, run_command(snapshot))
        assert not calls
        with receipts.reservations._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM pod_action_receipt"
            ).fetchone()[0] == 0


@ONLY_LINUX_UNPRIVILEGED
def test_foreign_gateway_child_cannot_claim_stage(tmp_path, monkeypatch):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    src, lease, _, receipts, stages, broker = rig(tmp_path / "one")
    _, foreign, _, _, _, _ = rig(tmp_path / "two")
    with staged_workspace(src, staging_parent=tmp_path / "one") as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        with pytest.raises(AdmissionDenied):
            broker.execute(foreign, stage.stage_id, run_command(snapshot))
        assert not calls
        with receipts.reservations._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM pod_action_receipt"
            ).fetchone()[0] == 0


@ONLY_LINUX_UNPRIVILEGED
def test_execution_timeout_cannot_outlive_reserved_budget(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        with pytest.raises(AdmissionDenied, match="exceeds child duration"):
            broker.execute(
                lease, stage.stage_id,
                run_command(snapshot, timeout=40),
            )
        assert not calls
        with receipts.reservations._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM pod_action_receipt"
            ).fetchone()[0] == 0
