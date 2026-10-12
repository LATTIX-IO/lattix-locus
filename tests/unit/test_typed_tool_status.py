"""LOCUS-433: gateway-receipted status projection (no stdout collection)."""
from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_tool_broker import TypedToolStatus
from locus_runtime.tool_result_contract import (
    StatusResult, ToolResultPolicy, ToolResultRejected, decode_tool_result,
)
from tests.unit.test_pod_tool_broker import setup


def test_authorized_tool_returns_receipt_bound_canonical_status(tmp_path, monkeypatch):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)
    result = app.execute_status(
        lease, tool_name="fixed_tool", action_idempotency_key="status-ok",
    )
    assert isinstance(result, TypedToolStatus)
    assert isinstance(result.status, StatusResult)
    assert result.status.ok and result.status.code == 0
    assert result.receipt.state == "succeeded"
    assert len(result.status.digest) == 64
    expected = decode_tool_result(
        b'{"schema":"status/v1","ok":true,"code":0}',
        ToolResultPolicy("status/v1"),
    )
    assert result.status.digest == expected.digest
    assert len(launches) == 1
    argv, opts = launches[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert opts["stdout"] is subprocess.DEVNULL
    assert opts["stderr"] is subprocess.DEVNULL
    assert "--network=none" in argv
    assert [name for name, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
    with pytest.raises(FrozenInstanceError):
        result.status.code = 10


def test_nonzero_exit_has_failure_status_without_raw_output(tmp_path, monkeypatch):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)

    def fail(argv, **kwargs):
        launches.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 17)

    monkeypatch.setattr(broker_module.subprocess, "run", fail)
    result = app.execute_status(
        lease, tool_name="fixed_tool", action_idempotency_key="status-17",
    )
    assert result.status.ok is False and result.status.code == 17
    assert result.receipt.state == "succeeded"  # process ran, tool returned 17
    assert len(launches) == 1
    assert "stdout" not in repr(result)
    assert "stderr" not in repr(result)


@pytest.mark.parametrize("exit_code", [-9, 256, 99999])
def test_unrepresentable_exit_cannot_be_released_or_replayed(
    tmp_path, monkeypatch, exit_code,
):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)

    def invalid(argv, **kwargs):
        launches.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, exit_code)

    monkeypatch.setattr(broker_module.subprocess, "run", invalid)
    with pytest.raises(ToolResultRejected, match="status"):
        app.execute_status(
            lease, tool_name="fixed_tool", action_idempotency_key="bad-exit",
        )
    # The external effect already happened and remains consumed even though
    # the projection was invalid. Never create a second subprocess.
    with pytest.raises(ActionReplayDenied):
        app.execute_status(
            lease, tool_name="fixed_tool", action_idempotency_key="bad-exit",
        )
    assert len(launches) == 1
    with receipts.reservations._connect() as db:
        assert db.execute(
            "SELECT state FROM pod_action_receipt"
        ).fetchone()[0] == "succeeded"


def test_denied_gateway_does_not_emit_typed_status_or_spawn(tmp_path, monkeypatch):
    from tests.gateway_support import FakeEngine

    app, receipts, lease, _, _, launches = setup(
        tmp_path, monkeypatch, engine=FakeEngine(allow={"agent_policy": False}),
    )
    with pytest.raises(AdmissionDenied):
        app.execute_status(
            lease, tool_name="fixed_tool", action_idempotency_key="not-allowed",
        )
    assert len(launches) == 0
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_status_projection_uses_same_replay_barrier(tmp_path, monkeypatch):
    app, _, lease, _, _, launches = setup(tmp_path, monkeypatch)
    app.execute_status(
        lease, tool_name="fixed_tool", action_idempotency_key="one-call",
    )
    with pytest.raises(ActionReplayDenied):
        app.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="one-call",
        )
    assert len(launches) == 1


def test_status_projection_timeout_does_not_erase_uncertainty(tmp_path, monkeypatch):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)

    def timeout(argv, **kwargs):
        launches.append((argv, kwargs))
        if argv[:2] == ["docker", "kill"]:
            return subprocess.CompletedProcess(argv, 0)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(broker_module.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        app.execute_status(
            lease, tool_name="fixed_tool", action_idempotency_key="ambiguous",
        )
    with pytest.raises(ActionReplayDenied):
        app.execute_status(
            lease, tool_name="fixed_tool", action_idempotency_key="ambiguous",
        )
    assert len(launches) == 2  # container launch and bounded cleanup
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT state FROM pod_action_receipt").fetchone()[0] == "uncertain"
