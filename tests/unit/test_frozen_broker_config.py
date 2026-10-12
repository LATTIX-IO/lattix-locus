"""LOCUS-433 increment 19: direct broker configuration is immutable after validation."""
from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError

import pytest

from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_sandbox_broker import PodSandboxBroker
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture

_IMAGE = "registry.example:5000/locus/sandbox@sha256:" + "a" * 64


@pytest.mark.parametrize("field,value", [
    ("_require_immutable_docker_image", False),
    ("_pinned_docker_image", None),
    ("_pinned_docker_image", "python:latest"),
    ("_receipts", None),
])
def test_strict_supervisor_configuration_cannot_be_reassigned(
    tmp_path, monkeypatch, field, value,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    app = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    with pytest.raises(FrozenInstanceError):
        setattr(app, field, value)
    assert app._require_immutable_docker_image is True
    assert app._pinned_docker_image == _IMAGE
    assert app._receipts is receipts
    assert not calls
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_frozen_broker_no_mutable_dict_or_new_configuration_fields(
    tmp_path, monkeypatch,
):
    _, receipts, _, _ = fixture(tmp_path)
    app = PodSandboxBroker(receipts)
    assert not hasattr(app, "__dict__")
    assert not hasattr(app, "ad_hoc_admin_flag")
    with pytest.raises((FrozenInstanceError, TypeError, AttributeError)):
        app.ad_hoc_admin_flag = True
    with pytest.raises(FrozenInstanceError):
        app._require_immutable_docker_image = True
    assert app._require_immutable_docker_image is False
    assert app._pinned_docker_image is None


def test_strict_frozen_broker_retains_gateway_receipt_and_replay(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", _IMAGE)
    _, receipts, lease, _ = fixture(tmp_path)
    app = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    out = app.execute(lease, cmd(tmp_path, key="frozen-strict"))
    assert out.receipt.state == "succeeded"
    assert len(calls) == 1
    argv, options = calls[0]
    assert _IMAGE in argv
    assert "--network=none" in argv
    assert "--cap-drop=ALL" in argv
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path, key="frozen-strict"))
    assert len(calls) == 1
    assert [event for event, _ in receipts.audit(out.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]


def test_frozen_broker_repr_does_not_expose_supervisor_identity_or_ledger(
    tmp_path, monkeypatch,
):
    _, receipts, _, _ = fixture(tmp_path)
    app = PodSandboxBroker(
        receipts, require_immutable_docker_image=True,
        pinned_docker_image=_IMAGE,
    )
    view = repr(app)
    assert _IMAGE not in view
    assert str(tmp_path) not in view
    assert "PodActionReceipts" not in view
    assert "PodSandboxBroker" in view


def test_legacy_non_strict_broker_remains_usable_and_frozen(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", "python:3.12.10-slim-bookworm")
    _, receipts, lease, _ = fixture(tmp_path)
    app = PodSandboxBroker(receipts)
    assert app._require_immutable_docker_image is False
    with pytest.raises(FrozenInstanceError):
        app._receipts = None
    result = app.execute(lease, cmd(tmp_path, key="frozen-legacy"))
    assert result.receipt.state == "succeeded"
    assert len(calls) == 1
