"""LOCUS-470/A: fail-closed stage-only Docker stdin channel regression.

This does not constitute live Codex/provider acceptance. It only proves the
bounded confidential payload is excluded from the frozen Docker command line
and denies malformed inputs before any receipt can be prepared.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from locus_runtime import pod_stage_docker as module
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_stage_docker_launch import (
    ONLY_LINUX_UNPRIVILEGED, mock_host, rig, run_command,
)
from locus_runtime.pod_workspace_staging import staged_workspace


def test_stdin_is_opt_in_and_command_has_no_prompt() -> None:
    common = dict(
        docker="/usr/bin/docker", socket_uri="unix:///var/run/docker.sock",
        image="test@sha256:" + "a" * 64, name="locus-pod-test",
        stage="/tmp/private-stage", entrypoint="codex",
        arguments=("exec", "--json", "-"),
        seccomp="/tmp/seccomp.json", uid=1234, gid=1234, proof="a" * 64,
    )
    plain = module._command_vector(**common)
    piped = module._command_vector(**common, interactive=True)
    assert "--interactive" not in plain
    assert piped.count("--interactive") == 1
    assert piped.index("--interactive") == piped.index("run") + 3
    assert "--network=none" in piped
    assert "--read-only" in piped
    assert "password-value" not in " ".join(piped)


@ONLY_LINUX_UNPRIVILEGED
@pytest.mark.parametrize("payload", [
    b"", b"x" * 65537, b"bad\x00payload", b"\xff", "text", 123,
])
def test_untrusted_stdin_rejected_before_docker_or_receipt(tmp_path, monkeypatch, payload):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        calls = mock_host(monkeypatch)
        with pytest.raises(AdmissionDenied):
            broker.execute(lease, stage.stage_id, run_command(snapshot), stdin=payload)
        assert calls == []
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


@ONLY_LINUX_UNPRIVILEGED
def test_stdin_delivery_is_single_use_and_secret_not_in_argv(tmp_path, monkeypatch):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    secret = b"sensitive-prompt-123\n"
    observed = []
    class FakeProcess:
        def __init__(self, argv, **kw):
            assert kw["stdin"] == subprocess.PIPE
            assert kw["close_fds"] is True and kw["shell"] is False
            assert "--interactive" in argv
            assert secret.decode().strip() not in " ".join(argv)
            observed.append((tuple(argv), kw["env"].copy()))
            self.returncode = None
        def communicate(self, input=None, timeout=None):
            observed.append(input)
            self.returncode = 0
            return b"", b""
        def poll(self): return self.returncode
        def kill(self): self.returncode = -9
    monkeypatch.setattr(module, "_trusted_local_binary", lambda _: ("/usr/bin/docker", "a" * 64))
    monkeypatch.setattr(module, "_local_socket", lambda _: "unix:///var/run/docker.sock")
    monkeypatch.setattr(module, "require_guardian_ready", lambda *_: "test-guardian-epoch")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        result = broker.execute(lease, stage.stage_id, run_command(snapshot), stdin=secret)
        assert result.exit_code == 0
        assert result.receipt.state == "succeeded"
        assert observed[-1] == secret
        assert secret.decode().strip() not in str(observed[0])
        with receipts.reservations._connect() as db:
            row = db.execute("SELECT launch_sha256 FROM pod_stage_docker_launch").fetchone()
            assert row is not None
            assert secret.decode().strip() not in str(row)
