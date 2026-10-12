"""LOCUS-433 increment 20: normalize trusted broker command identity before side effects."""
from __future__ import annotations

import subprocess

import pytest

from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_sandbox_broker import BrokerCommand
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture


@pytest.mark.parametrize("bad_tool", [
    "", " ", " tool", "tool ", "tool\nforged", "tool\rinjected",
    "tool\tsuffix", "tool/other", "tool\\other", "tool:alias",
    "tool\u202e", "\u212aey", "9tool", "x" * 129,
])
def test_noncanonical_tool_identity_denied_at_command_construction(bad_tool):
    with pytest.raises(ValueError, match="canonical tool identifier"):
        BrokerCommand(
            tool=bad_tool, argv=("echo", "ok"), working_dir="/tmp",
            idempotency_key="valid-key",
        )


@pytest.mark.parametrize("bad_key", [
    "", "invalid key", "line\nbreak", "carriage\rreturn",
    "tab\tseparator", "path/segment", "name\\backslash", "\u00e9",
    "a" * 161,
])
def test_noncanonical_action_key_denied_at_command_construction(bad_key):
    with pytest.raises(ValueError, match="canonical idempotency key"):
        BrokerCommand(
            tool="run_command", argv=("echo", "ok"),
            working_dir="/tmp", idempotency_key=bad_key,
        )


@pytest.mark.parametrize("bad_cwd", [
    "", "   ", "relative\nforged", "/tmp\rinjected", "/tmp\x00other",
    "a" * 4097,
])
def test_malformed_working_directory_denied_before_any_admission(bad_cwd):
    with pytest.raises(ValueError, match="bounded working directory"):
        BrokerCommand(
            tool="run_command", argv=("echo", "ok"),
            working_dir=bad_cwd, idempotency_key="safe-one",
        )


@pytest.mark.parametrize("tool_name", [
    "run_command", "fixed_tool", "Tool-v2.0", "x" * 128,
])
def test_approved_canonical_tool_grammar_is_backward_compatible(tool_name):
    command = BrokerCommand(
        tool=tool_name, argv=("echo", "ok"),
        working_dir="/tmp", idempotency_key="valid-key",
    )
    assert command.tool == tool_name


@pytest.mark.parametrize("key", [
    "safe-one", "safe_one", "action.rev:42", "A" * 160,
])
def test_receipt_compatible_key_grammar_remains_accepted(key):
    command = BrokerCommand(
        tool="run_command", argv=("echo", "ok"),
        working_dir="/tmp", idempotency_key=key,
    )
    assert command.idempotency_key == key


def test_valid_broker_identity_keeps_single_gateway_fenced_process(
    tmp_path, monkeypatch,
):
    calls = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path, key="valid-action:42"))
    assert result.receipt.state == "succeeded"
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    with pytest.raises(ActionReplayDenied):
        app.execute(lease, cmd(tmp_path, key="valid-action:42"))
    assert len(calls) == 1
    assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
