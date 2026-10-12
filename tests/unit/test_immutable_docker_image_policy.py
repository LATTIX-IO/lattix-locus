"""LOCUS-433: strict immutable Docker image refs at the single broker boundary."""
from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_sandbox_broker import PodSandboxBroker
from locus_runtime.pod_tool_broker import SandboxedPodToolBroker
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture
from tests.unit.test_pod_tool_broker import setup

_PIN = "a" * 64
_OTHER_PIN = "b" * 64
_IMAGE = "registry.example:5000/locus/sandbox@sha256:" + _PIN


def _count(receipts):
    with receipts.reservations._connect() as db:
        return db.execute("SELECT COUNT(*) FROM pod_action_receipt").fetchone()[0]


@pytest.mark.parametrize("image", [
    "python:3.12.10-slim-bookworm",
    "python:latest",
    "python",
    "python@sha256:" + "A" * 64,
    "python@sha256:" + "0" * 63,
    "python@sha256:" + "z" * 64,
    "python@sha256:" + "0" * 65,
    "python:latest@sha256:" + _PIN,
    "registry.example:5000/team/runner:latest@sha256:" + _PIN,
    "../escape@sha256:" + _PIN,
    "registry.example/team/../runner@sha256:" + _PIN,
    "registry.example//runner@sha256:" + _PIN,
    "registry.example/runner@sha256:" + _PIN + " --privileged",
    "",
])
def test_strict_registry_denies_mutable_or_malformed_images(
    tmp_path, monkeypatch, image,
):
    docker_calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", image)
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    with pytest.raises(AdmissionDenied, match="Docker sandbox image digest pin"):
        strict.execute(lease, cmd(tmp_path))
    assert docker_calls == []
    assert _count(receipts) == 0


def test_strict_registry_executes_one_immutable_image_without_raw_output(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _, mapping, calls = setup(tmp_path, monkeypatch)
    strict = SandboxedPodToolBroker(
        receipts, mapping, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    assert strict.requires_immutable_docker_image is True
    out = strict.execute_status(
        lease, tool_name="fixed_tool", action_idempotency_key="image-fixed",
    )
    assert out.receipt.state == "succeeded"
    assert out.status.code == 0
    assert len(calls) == 1
    argv, opts = calls[0]
    assert argv.count(_IMAGE) == 1
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert opts["shell"] is False
    assert opts["stdout"] is subprocess.DEVNULL
    assert opts["stderr"] is subprocess.DEVNULL
    with pytest.raises(ActionReplayDenied):
        strict.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="image-fixed",
        )
    assert len(calls) == 1


def test_image_changed_by_planner_fails_against_preplanner_snapshot(
    tmp_path, monkeypatch,
):
    original = broker_module.SandboxManager.plan
    docker_calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)

    def swapped(self, spec, policy):
        monkeypatch.setenv(
            "SANDBOX_RUNNER_IMAGE",
            "registry.example:5000/locus/sandbox@sha256:" + _OTHER_PIN,
        )
        return original(self, spec, policy)

    monkeypatch.setattr(broker_module.SandboxManager, "plan", swapped)
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    with pytest.raises(AdmissionDenied, match="Docker sandbox isolation contract"):
        strict.execute(lease, cmd(tmp_path))
    assert not docker_calls
    assert _count(receipts) == 0


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], object()])
def test_supervisor_image_policy_requires_exact_bool(
    tmp_path, monkeypatch, invalid,
):
    _, receipts, _, _, mapping, calls = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Docker image pin policy must be a boolean"):
        PodSandboxBroker(receipts, require_immutable_docker_image=invalid)
    with pytest.raises(ValueError, match="Docker image pin policy must be a boolean"):
        SandboxedPodToolBroker(
            receipts, mapping, require_immutable_docker_image=invalid,
            pinned_docker_image=_IMAGE,
        )
    assert not calls
    assert _count(receipts) == 0


def test_image_requirement_frozen_and_changes_catalog_revision(tmp_path, monkeypatch):
    _, receipts, _, _, mapping, _ = setup(tmp_path, monkeypatch)
    optional = SandboxedPodToolBroker(receipts, mapping)
    strict = SandboxedPodToolBroker(
        receipts, mapping, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    assert optional.requires_immutable_docker_image is False
    assert strict.requires_immutable_docker_image is True
    assert optional.catalog_digest != strict.catalog_digest
    with pytest.raises(FrozenInstanceError):
        strict._require_immutable_docker_image = False
    with pytest.raises(FrozenInstanceError):
        strict._executor = None


def test_legacy_tagged_image_remains_usable_only_without_strict_mode(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", "python:3.12.10-slim-bookworm")
    app, receipts, lease, _ = fixture(tmp_path)
    out = app.execute(lease, cmd(tmp_path))
    assert out.receipt.state == "succeeded"
    assert len(calls) == 1
    assert "python:3.12.10-slim-bookworm" in calls[0][0]


def test_strict_mode_requires_supervisor_approved_exact_digest(tmp_path, monkeypatch):
    _, receipts, _, _, mapping, invocations = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="approved digest"):
        PodSandboxBroker(receipts, require_immutable_docker_image=True)
    with pytest.raises(ValueError, match="approved digest"):
        SandboxedPodToolBroker(
            receipts, mapping, require_immutable_docker_image=True,
        )
    with pytest.raises(ValueError, match="approved digest"):
        PodSandboxBroker(
            receipts, require_immutable_docker_image=True,
            pinned_docker_image="python:latest",
        )
    with pytest.raises(ValueError, match="strict mode enabled"):
        PodSandboxBroker(receipts, pinned_docker_image=_IMAGE)
    assert not invocations
    assert _count(receipts) == 0


def test_valid_runtime_digest_other_than_supervisor_pin_denied(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv(
        "SANDBOX_RUNNER_IMAGE",
        "registry.example:5000/locus/sandbox@sha256:" + _OTHER_PIN,
    )
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    with pytest.raises(AdmissionDenied, match="does not match approved digest"):
        strict.execute(lease, cmd(tmp_path))
    assert not calls
    assert _count(receipts) == 0


def test_distinct_admin_image_pins_have_distinct_catalog_identity(
    tmp_path, monkeypatch,
):
    _, receipts, _, _, mapping, invocations = setup(tmp_path, monkeypatch)
    first = SandboxedPodToolBroker(
        receipts, mapping, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    second = SandboxedPodToolBroker(
        receipts, mapping, require_immutable_docker_image=True,
        pinned_docker_image="registry.example:5000/locus/sandbox@sha256:" + _OTHER_PIN,
    )
    assert first.catalog_digest != second.catalog_digest
    with pytest.raises(FrozenInstanceError):
        first._pinned_docker_image = None
    assert not invocations
    assert _count(receipts) == 0
