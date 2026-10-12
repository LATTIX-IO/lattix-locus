"""LOCUS-433 increment 17: receipt identity for the frozen Docker launch contract.

These regressions exercise the real Gateway and durable action-receipt path,
with Docker subprocess calls mocked at the single existing execution sink.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_sandbox_broker import PodSandboxBroker
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture

_IMAGE = "registry.example:5000/locus/sandbox@sha256:" + "a" * 64
_PREFIX = b"locus.sandbox-launch-contract.v1\x00"


def _strict(receipts):
    return PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )


def _receipt_count(receipts) -> int:
    with receipts.reservations._connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM pod_action_receipt",
        ).fetchone()[0]


def _capture_authorization(receipts, monkeypatch):
    seen = []
    original = receipts.prepare

    def capture(lease_arg, *, idempotency_key, authorization):
        seen.append(dict(authorization))
        return original(
            lease_arg, idempotency_key=idempotency_key,
            authorization=authorization,
        )

    monkeypatch.setattr(receipts, "prepare", capture)
    return seen


def test_strict_receipt_binds_actual_verified_launch_without_container_nonce(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    seen = _capture_authorization(receipts, monkeypatch)

    outcome = _strict(receipts).execute(
        lease, cmd(tmp_path, key="verified-launch"),
    )
    assert outcome.receipt.state == "succeeded"
    assert len(seen) == 1
    assert len(calls) == 1

    invoked, options = calls[0]
    assert invoked[:4] == ["docker", "run", "--rm", "--name"]
    assert invoked[4].startswith("locus-pod-")
    verified = tuple(invoked[:3] + invoked[5:])
    canonical = json.dumps(
        {
            "backend": "hardened-docker",
            "argv": verified,
            "launcher_cwd": options["cwd"],
            "timeout_seconds": options["timeout"],
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    expected = hashlib.sha256(_PREFIX + canonical).hexdigest()
    args = seen[0]["args"]
    assert args["sandbox_launch_contract_digest"] == expected
    assert len(args["sandbox_launch_contract_digest"]) == 64
    assert set(args) == {
        "sandbox_image_ref_digest", "sandbox_launch_contract_digest",
    }
    assert str(tmp_path) not in repr(args)
    assert _IMAGE not in repr(args)
    assert "locus-pod-" not in repr(args)
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    with receipts.reservations._connect() as db:
        row = db.execute(
            "SELECT fingerprint FROM pod_action_receipt",
        ).fetchone()
    assert row["fingerprint"] == outcome.receipt.fingerprint


def test_changed_timeout_with_same_action_key_is_refused(tmp_path, monkeypatch):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    broker = _strict(receipts)
    broker.execute(lease, cmd(tmp_path, key="time-key", timeout=15))
    with pytest.raises(ActionReplayDenied, match="different action"):
        broker.execute(lease, cmd(tmp_path, key="time-key", timeout=16))
    assert len(calls) == 1
    assert _receipt_count(receipts) == 1


def test_changed_resource_limit_with_same_key_is_refused(tmp_path, monkeypatch):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    broker = _strict(receipts)
    broker.execute(lease, cmd(tmp_path, key="resource-key"))

    original_policy = broker_module.SandboxPolicy

    def changed_policy(*args, **kwargs):
        policy = original_policy(*args, **kwargs)
        policy.memory_limit = "99g"
        return policy

    monkeypatch.setattr(broker_module, "SandboxPolicy", changed_policy)
    with pytest.raises(ActionReplayDenied, match="different action"):
        broker.execute(lease, cmd(tmp_path, key="resource-key"))
    assert len(calls) == 1
    assert _receipt_count(receipts) == 1


def test_identical_strict_action_is_still_nonreplayable(tmp_path, monkeypatch):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    broker = _strict(receipts)
    broker.execute(lease, cmd(tmp_path, key="same-verified"))
    with pytest.raises(ActionReplayDenied):
        broker.execute(lease, cmd(tmp_path, key="same-verified"))
    assert len(calls) == 1


def test_legacy_non_strict_receipt_does_not_add_launch_identity(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    _, receipts, lease, _ = fixture(tmp_path)
    seen = _capture_authorization(receipts, monkeypatch)
    broker = PodSandboxBroker(receipts)
    out = broker.execute(lease, cmd(tmp_path, key="legacy-shape"))
    assert out.receipt.state == "succeeded"
    assert len(calls) == 1
    assert "args" not in seen[0]


def test_changed_launcher_directory_cannot_reuse_action_key(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    broker = _strict(receipts)
    broker.execute(lease, cmd(tmp_path, key="launcher-key"))

    alternate = tmp_path / "other-launcher-dir"
    alternate.mkdir()
    original_plan = broker_module.SandboxManager.plan

    def redirected_launcher(self, spec, policy):
        plan = original_plan(self, spec, policy)
        plan.metadata["launcher_cwd"] = str(alternate)
        return plan

    monkeypatch.setattr(broker_module.SandboxManager, "plan", redirected_launcher)
    with pytest.raises(ActionReplayDenied, match="different action"):
        broker.execute(lease, cmd(tmp_path, key="launcher-key"))
    assert len(calls) == 1
    assert _receipt_count(receipts) == 1


def test_changed_read_mount_cannot_reuse_action_key(tmp_path, monkeypatch):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    broker = _strict(receipts)
    broker.execute(lease, cmd(tmp_path, key="mount-key"))

    extra = tmp_path / "extra-read"
    extra.mkdir()
    original_policy = broker_module.SandboxPolicy

    def added_mount(*args, **kwargs):
        policy = original_policy(*args, **kwargs)
        policy.allowed_read_paths.append(str(extra))
        return policy

    monkeypatch.setattr(broker_module, "SandboxPolicy", added_mount)
    with pytest.raises(ActionReplayDenied, match="different action"):
        broker.execute(lease, cmd(tmp_path, key="mount-key"))
    assert len(calls) == 1
    assert _receipt_count(receipts) == 1
