"""LOCUS-433: bind approved Docker image reference to durable action identity.

Only trusted-supervisor strict Docker mode adds image identity to authorization.
No raw image URI, argv, stdout or stderr is recorded in newly added args.
"""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_sandbox_broker import PodSandboxBroker
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture

_FIRST = "registry.example:5000/locus/sandbox@sha256:" + "a" * 64
_SECOND = "registry.example:5000/locus/sandbox@sha256:" + "b" * 64
_OTHER_REPOSITORY = "registry.example:5000/alternate/sandbox@sha256:" + "a" * 64


def _expected_digest(reference: str) -> str:
    return hashlib.sha256(
        b"locus.sandbox-image-ref.v1\x00" + reference.encode("utf-8"),
    ).hexdigest()


def _receipt_count(receipts) -> int:
    with receipts.reservations._connect() as db:
        return db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0]


def test_supervisor_image_identity_reaches_gateway_and_durable_receipt(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _FIRST)
    _, receipts, lease, _ = fixture(tmp_path)
    seen = []
    old_prepare = receipts.prepare

    def inspect_prepare(lease_arg, *, idempotency_key, authorization):
        seen.append(dict(authorization))
        return old_prepare(
            lease_arg, idempotency_key=idempotency_key,
            authorization=authorization,
        )

    monkeypatch.setattr(receipts, "prepare", inspect_prepare)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_FIRST,
    )
    outcome = strict.execute(lease, cmd(tmp_path, key="approved-image"))
    assert outcome.receipt.state == "succeeded"
    assert len(calls) == 1
    assert len(seen) == 1
    args = seen[0]["args"]
    assert args["sandbox_image_ref_digest"] == _expected_digest(_FIRST)
    assert len(args["sandbox_launch_contract_digest"]) == 64
    assert set(args) == {"sandbox_image_ref_digest", "sandbox_launch_contract_digest"}
    assert _FIRST not in repr(args)
    assert seen[0]["kind"] == "process_exec"
    assert seen[0]["jail"].allow_network is False
    assert calls[0][1]["stdout"] is subprocess.DEVNULL
    assert calls[0][1]["stderr"] is subprocess.DEVNULL
    assert calls[0][1]["shell"] is False
    with receipts.reservations._connect() as db:
        row = db.execute(
            "SELECT fingerprint FROM pod_action_receipt",
        ).fetchone()
    assert row["fingerprint"] == outcome.receipt.fingerprint


@pytest.mark.parametrize("next_image", [_SECOND, _OTHER_REPOSITORY])
def test_changed_approved_image_ref_cannot_reuse_action_key(
    tmp_path, monkeypatch, next_image,
):
    calls = docker(monkeypatch)
    _, receipts, lease, _ = fixture(tmp_path)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _FIRST)
    first = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_FIRST,
    )
    first.execute(lease, cmd(tmp_path, key="image-key"))
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", next_image)
    second = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=next_image,
    )
    with pytest.raises(ActionReplayDenied):
        second.execute(lease, cmd(tmp_path, key="image-key"))
    assert len(calls) == 1
    assert _receipt_count(receipts) == 1


def test_same_image_key_is_nonreplayable_after_dispatch(tmp_path, monkeypatch):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _FIRST)
    _, receipts, lease, _ = fixture(tmp_path)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_FIRST,
    )
    strict.execute(lease, cmd(tmp_path, key="same-approved-image"))
    with pytest.raises(ActionReplayDenied):
        strict.execute(lease, cmd(tmp_path, key="same-approved-image"))
    assert len(calls) == 1


def test_optional_legacy_broker_keeps_original_action_shape(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", "python:3.12.10-slim-bookworm")
    _, receipts, lease, _ = fixture(tmp_path)
    seen = []
    old_prepare = receipts.prepare

    def inspect_prepare(lease_arg, *, idempotency_key, authorization):
        seen.append(dict(authorization))
        return old_prepare(
            lease_arg, idempotency_key=idempotency_key,
            authorization=authorization,
        )

    monkeypatch.setattr(receipts, "prepare", inspect_prepare)
    broker = PodSandboxBroker(receipts)
    receipt = broker.execute(lease, cmd(tmp_path, key="legacy-image"))
    assert receipt.receipt.state == "succeeded"
    assert len(calls) == 1
    assert "args" not in seen[0]
    assert _receipt_count(receipts) == 1


def test_catalog_and_image_identity_share_canonical_authorization(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _FIRST)
    _, receipts, lease, _ = fixture(tmp_path)
    seen = []
    old_prepare = receipts.prepare

    def inspect_prepare(lease_arg, *, idempotency_key, authorization):
        seen.append(dict(authorization))
        return old_prepare(
            lease_arg, idempotency_key=idempotency_key,
            authorization=authorization,
        )

    monkeypatch.setattr(receipts, "prepare", inspect_prepare)
    strict = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_FIRST,
    )
    command = cmd(tmp_path, key="catalog-and-image")
    from locus_runtime.pod_sandbox_broker import BrokerCommand

    command = BrokerCommand(
        tool=command.tool,
        argv=command.argv,
        working_dir=command.working_dir,
        idempotency_key=command.idempotency_key,
        timeout_seconds=command.timeout_seconds,
        catalog_digest="1" * 64,
    )
    result = strict.execute(lease, command)
    assert result.receipt.state == "succeeded"
    assert len(calls) == 1
    assert seen[0]["args"]["catalog_digest"] == "1" * 64
    assert seen[0]["args"]["sandbox_image_ref_digest"] == _expected_digest(_FIRST)
    assert len(seen[0]["args"]["sandbox_launch_contract_digest"]) == 64
    assert set(seen[0]["args"]) == {
        "catalog_digest", "sandbox_image_ref_digest",
        "sandbox_launch_contract_digest",
    }
