"""LOCUS-470/B: malicious frame, scope, budget, and confined-runner tests.

Synthetic protocol tests cannot establish real Docker/Codex/Ollama acceptance.
The separate real-host workflow must run official CLI and a genuine model.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest

from locus_runtime.pod_codex_bridge import (
    CodexOllamaBridge, CodexRelayDenied, _reject_tool_responses, _request, relay_error,
)
from locus_runtime.pod_codex_transport import CodexFramePump
from locus_runtime.harness.codex_cli_worker import CODEX_VERSION, CodexCliWorker
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_workspace_staging import staged_workspace
from tests.unit.test_stage_docker_launch import ONLY_LINUX_UNPRIVILEGED, rig


def rpc(data, *, n=1, path="/v1/responses"):
    return json.dumps({
        "schema": "locus.codex.stdio.v1", "kind": "model_request",
        "id": n, "method": "POST", "path": path,
        "body": base64.b64encode(json.dumps(data).encode()).decode(),
    }).encode("ascii")


def test_exact_model_and_endpoint_only():
    model = "llama3.2:1b"
    request = _request(rpc({"model": model, "input": "hello"}), model)
    assert request[0] == 1 and request[1] == "/v1/responses"
    assert b"hello" in request[2]
    assert request[5] == 0
    for payload in (
        rpc({"model": model}, path="http://evil.example/"),
        rpc({"model": model}, path="/v1/models"),
        rpc({"model": "other", "input": "hello"}),
        rpc({"model": model, "tools": [{"type": "function"}],
             "tool_choice": "required"}),
        rpc({"model": model, "max_output_tokens": 100000}),
        rpc({"model": model, "stream": "true"}),
        b'{"schema":"locus.codex.stdio.v1","schema":"duplicate"}',
        b"garbage",
        b"{" + b"x" * (2 * 1024 * 1024) + b"}",
    ):
        with pytest.raises(CodexRelayDenied):
            _request(payload, model)
    downscoped = _request(rpc({
        "model": model, "input": "answer without any tools",
        "tools": [{"type": "function", "name": "shell_command"}],
        "tool_choice": "auto", "parallel_tool_calls": True,
    }), model)
    outbound = json.loads(downscoped[2])
    assert downscoped[5] == 1
    assert "tools" not in outbound and "tool_choice" not in outbound
    assert "parallel_tool_calls" not in outbound
    assert "shell_command" not in downscoped[2].decode("utf-8")
    denied = relay_error(1)
    assert b"prompt" not in denied and b"credential" not in denied


def test_model_response_tool_calls_are_never_forwarded():
    normal = {"output": [{"type": "message", "content": [{"type": "output_text",
               "text": "The sea appears blue because of light scattering."}]}]}
    _reject_tool_responses(json.dumps(normal).encode(), "application/json")
    bad = {"output": [{"type": "function_call", "name": "shell_command"}]}
    with pytest.raises(CodexRelayDenied, match="tool response refused"):
        _reject_tool_responses(json.dumps(bad).encode(), "application/json")
    event = (
        b"event: response.output_item.added\n"
        + b"data: "
        + json.dumps({
            "type": "response.output_item.added",
            "item": {"type": "function_call", "name": "shell_command"},
        }).encode("ascii")
        + b"\n\ndata: [DONE]\n\n"
    )
    with pytest.raises(CodexRelayDenied, match="tool response refused"):
        _reject_tool_responses(event, "text/event-stream")
    with pytest.raises(CodexRelayDenied, match="unparseable"):
        _reject_tool_responses(b"{malformed", "application/json")


def test_no_nonlocal_provider():
    class NeverUsed:
        pass
    with pytest.raises(CodexRelayDenied):
        CodexOllamaBridge(NeverUsed(), model="llama3.2:1b", host="example.com")


class FakeProcess:
    """A source of official-runner-only JSONL with writable stdin."""
    def __init__(self, lines):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(line + b"\n" for line in lines))
        self.returncode = 0


class StubBridge:
    def __init__(self):
        self.calls = []
    def handle(self, raw):
        self.calls.append(raw)
        return relay_error(1)


def frame(**fields):
    fields["schema"] = "locus.codex.stdio.v1"
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def test_frame_pump_requires_pinned_ready_and_terminal():
    success = [
        frame(kind="ready", version=CODEX_VERSION),
        frame(kind="done", ok=True, events=1, bytes=3,
              sha256=hashlib.sha256(b"abc").hexdigest(),
              stdout_sha256="a" * 64),
    ]
    proc = FakeProcess(success)
    pump = CodexFramePump(proc, StubBridge(), frame(kind="init"))
    assert pump.finish() == hashlib.sha256(b"abc").hexdigest()
    assert b'"kind":"init"' in proc.stdin.getvalue()
    for lines in (
        [success[1]],
        [frame(kind="ready", version="unknown"), success[1]],
        [success[0], success[0], success[1]],
        [success[0], frame(kind="done", ok=False, events=1)],
        [success[0], frame(kind="not-an-action")],
        [success[0], b"raw codex response leaked"],
    ):
        bad = CodexFramePump(FakeProcess(lines), StubBridge(), b'{"init":true}')
        with pytest.raises(CodexRelayDenied):
            bad.finish()


@ONLY_LINUX_UNPRIVILEGED
def test_worker_requires_exact_engine_and_no_source_rewrite(tmp_path):
    src, lease, _, receipts, stages, broker = rig(tmp_path)
    worker = CodexCliWorker(broker, model="llama3.2:1b")
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        stage = stages.bind(lease, snapshot)
        with pytest.raises(AdmissionDenied, match="engine pin"):
            worker.run(
                lease, stage.stage_id, prompt="Hello", output_name="answer.txt",
                idempotency_key="worker",
            )
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_container_image_and_runner_are_static():
    repo = Path(__file__).resolve().parents[2]
    dockerfile = (repo / "docker/codex/Dockerfile").read_text()
    runner = (repo / "docker/codex/stdio_runner.py").read_text()
    assert "@openai/codex@${CODEX_CLI_VERSION}" in dockerfile
    assert "FROM ${NODE_BASE}" in dockerfile
    assert 'PINNED_CODEX_VERSION = "' + CODEX_VERSION + '"' in runner
    assert '"--ignore-user-config"' in runner
    assert '"--sandbox", "read-only"' in runner
    assert '"--disable", "shell_tool"' in runner
    assert 'model_provider="locus_ollama"' in runner
    assert "model_providers.locus_ollama.wire_api" in runner
    assert '"--ignore-rules"' in runner
    assert '"--disable", "unified_exec"' in runner
    assert '"--oss", "--local-provider", "ollama"' not in runner
    assert "shell=True" not in runner
    assert "stderr=subprocess.PIPE" in runner
