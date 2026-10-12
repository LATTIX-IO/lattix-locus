"""LOCUS-433 increment 18: strict Docker-image identity must never degrade to native jail."""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_sandbox_broker import PodSandboxBroker
from locus_runtime.sandbox import (
    ConfinementSelection, HostPlatform, IsolationStrategy,
)
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture

_APPROVED = "registry.example:5000/locus/sandbox@sha256:" + "a" * 64


def _receipts_count(receipts) -> int:
    with receipts.reservations._connect() as db:
        return db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0]


@pytest.mark.parametrize(("strategy", "platform"), [
    (IsolationStrategy.KERNEL_SEATBELT, HostPlatform.MACOS),
    (IsolationStrategy.WINDOWS_APPCONTAINER, HostPlatform.WINDOWS),
])
def test_strict_docker_identity_denies_native_backend_before_planning_or_receipt(
    tmp_path, monkeypatch, strategy, platform,
):
    calls = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(strategy, platform, "native jail available"),
    )
    monkeypatch.setattr(
        broker_module.SandboxManager, "plan",
        lambda *_: pytest.fail("native planner must not run in strict Docker mode"),
    )
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_APPROVED,
    )
    with pytest.raises(AdmissionDenied, match="requires hardened Docker"):
        strict.execute(lease, cmd(tmp_path, key="native-bypass"))
    assert not calls
    assert _receipts_count(receipts) == 0


def test_bwrap_selection_may_promote_to_real_hardened_docker_in_strict_mode(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.KERNEL_BWRAP, HostPlatform.LINUX, "bwrap",
        ),
    )
    monkeypatch.setattr(broker_module.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "")
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _APPROVED)
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_APPROVED,
    )
    result = strict.execute(lease, cmd(tmp_path, key="bwrap-docker-upgrade"))
    assert result.receipt.state == "succeeded"
    assert result.backend == IsolationStrategy.HARDENED_DOCKER.value
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert _APPROVED in argv
    assert "--network=none" in argv
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    with pytest.raises(ActionReplayDenied):
        strict.execute(lease, cmd(tmp_path, key="bwrap-docker-upgrade"))
    assert len(calls) == 1
    assert _receipts_count(receipts) == 1


def test_strict_mode_rejects_bwrap_when_docker_not_available(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.KERNEL_BWRAP, HostPlatform.LINUX, "bwrap",
        ),
    )
    monkeypatch.setattr(broker_module.shutil, "which", lambda _: None)
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "")
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_APPROVED,
    )
    with pytest.raises(AdmissionDenied, match="bwrap default"):
        strict.execute(lease, cmd(tmp_path, key="no-docker"))
    assert not calls
    assert _receipts_count(receipts) == 0


def test_legacy_non_strict_native_strategy_retains_prior_planning_path(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setattr(
        broker_module, "select_confining_strategy",
        lambda: ConfinementSelection(
            IsolationStrategy.KERNEL_SEATBELT, HostPlatform.MACOS, "native",
        ),
    )

    def witness_planner(*_args, **_kwargs):
        raise RuntimeError("native strategy reached existing planner")

    monkeypatch.setattr(broker_module.SandboxManager, "plan", witness_planner)
    _, receipts, lease, _ = fixture(tmp_path)
    ordinary = PodSandboxBroker(receipts)
    with pytest.raises(RuntimeError, match="native strategy reached existing planner"):
        ordinary.execute(lease, cmd(tmp_path, key="legacy-native"))
    assert not calls
    assert _receipts_count(receipts) == 0
