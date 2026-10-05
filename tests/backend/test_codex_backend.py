"""Track A: Codex coding backend — JSONL ThreadEvent mapping + compiler routing.
Pure/unit (no codex binary, no stack)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from locus_runtime.harness import codex_backend as cb  # noqa: E402
from locus_runtime.gateway import Capabilities  # noqa: E402


# --- ThreadEvent → normalized step mapping ----------------------------------
def test_map_agent_message():
    m = cb.map_thread_event(
        {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "all done"}}
    )
    assert m == {"kind": "agent_message", "text": "all done"}


def test_map_reasoning():
    m = cb.map_thread_event(
        {"type": "item.completed", "item": {"id": "2", "type": "reasoning", "text": "thinking…"}}
    )
    assert m is None


def test_local_model_metadata_fallback_is_recorded_as_warning():
    event = {
        "type": "item.completed",
        "item": {
            "type": "error",
            "message": "Model metadata for gpt-oss:20b not found. Defaulting to fallback metadata; this can degrade performance and cause issues.",
        },
    }
    assert cb.map_thread_event(event)["kind"] == "warning"


def test_map_command_execution():
    m = cb.map_thread_event(
        {
            "type": "item.completed",
            "item": {
                "id": "3",
                "type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "2 passed",
                "exit_code": 0,
                "status": "completed",
            },
        }
    )
    assert m["kind"] == "command" and m["command"] == "pytest -q" and m["exit_code"] == 0


def test_map_file_change():
    m = cb.map_thread_event(
        {
            "type": "item.completed",
            "item": {
                "id": "4",
                "type": "file_change",
                "status": "completed",
                "changes": [{"path": "a.py", "kind": "update"}, {"path": "b.py", "kind": "add"}],
            },
        }
    )
    assert m["kind"] == "file_change" and m["files"] == ["a.py", "b.py"]


def test_map_turn_and_errors():
    assert (
        cb.map_thread_event({"type": "turn.completed", "usage": {"output_tokens": 5}})["kind"]
        == "usage"
    )
    assert cb.map_thread_event({"type": "turn.failed", "error": {"message": "boom"}}) == {
        "kind": "error",
        "message": "boom",
    }
    assert cb.map_thread_event({"type": "error", "message": "fatal"}) == {
        "kind": "error",
        "message": "fatal",
    }


def test_map_ignored_events():
    assert cb.map_thread_event({"type": "thread.started", "thread_id": "t"}) is None
    assert cb.map_thread_event({"type": "turn.started"}) is None
    assert (
        cb.map_thread_event(
            {"type": "item.started", "item": {"id": "x", "type": "agent_message", "text": ""}}
        )
        is None
    )


# --- run_codex degrades when the binary is missing --------------------------
def test_run_codex_unavailable_when_no_binary(tmp_path):
    res = cb.run_codex(
        prompt="hi", cwd=str(tmp_path), codex_bin="codex-does-not-exist-xyz", timeout=5
    )
    assert res.outcome == "unavailable"
    assert res.answer == ""


def test_build_command_shape(tmp_path):
    cmd = cb._build_command(
        codex_bin="codex",
        cwd=str(tmp_path),
        model="gpt-oss:20b",
        sandbox="workspace-write",
        last_message_file="/tmp/x",
        config_overrides={},
    )
    assert cmd[:3] == ["codex", "exec", "--json"]
    assert "--oss" in cmd and "-m" in cmd and "gpt-oss:20b" in cmd
    assert "--cd" in cmd and "--sandbox" in cmd and cmd[-1] == "-"


def test_build_gateway_command_exposes_only_locus_mcp_tools(tmp_path):
    cmd = cb._build_gateway_command(
        codex_bin=["node.exe", "codex.js"],
        cwd=str(tmp_path),
        model="gpt-oss:20b",
        last_message_file=str(tmp_path / "answer.txt"),
        python_bin="python.exe",
        mcp_config_file=str(tmp_path / "mcp.json"),
        ollama_base_url="http://127.0.0.1:11434/v1",
    )
    assert cmd[:3] == ["node.exe", "codex.js", "exec"]
    assert "--local-provider" in cmd and "ollama" in cmd
    assert "--sandbox" in cmd and cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--disable") + 1] == "shell_tool"
    assert "mcp_servers.locus" in " ".join(cmd)
    assert "analytics.enabled=false" in cmd
    assert 'model_providers.oss.name="Local Ollama"' in cmd
    assert cmd[-1] == "-"


def test_codex_npm_shim_resolves_to_node_entrypoint(tmp_path, monkeypatch):
    shim_dir = tmp_path / "npm"
    node = shim_dir / ("node.exe" if os.name == "nt" else "node")
    entry = shim_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    shim = shim_dir / "codex.cmd"
    node.parent.mkdir(parents=True)
    entry.parent.mkdir(parents=True)
    node.touch()
    entry.touch()
    shim.touch()
    assert cb._codex_command(str(shim)) == [str(node.resolve()), str(entry.resolve())]


def test_codex_local_endpoint_returns_resolved_loopback_model():
    endpoint = cb._validate_local_endpoint("http://127.0.0.1:11434/v1", "gpt-oss:20b")
    assert endpoint.provider == "ollama"
    assert endpoint.model == "gpt-oss:20b"
    assert endpoint.base_url == "http://127.0.0.1:11434/v1"
    assert endpoint.egress_host == "127.0.0.1"


def test_codex_capabilities_forward_zero_action_budget() -> None:
    payload = cb._capabilities_payload(SimpleNamespace(capabilities=Capabilities()), max_actions=0)
    assert payload["max_actions"] == 0


@pytest.mark.parametrize(
    "base_url",
    ["https://example.com/v1", "http://user:pass@127.0.0.1:11434/v1"],
)
def test_codex_local_endpoint_rejects_non_loopback_and_url_credentials(base_url):
    with pytest.raises(ValueError, match="credential-free loopback"):
        cb._validate_local_endpoint(base_url, "gpt-oss:20b")


@pytest.mark.parametrize("setup_failure", ["missing_workspace", "nested_runtime", "missing_codex"])
def test_codex_setup_failure_returns_result_before_gateway_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup_failure: str
) -> None:
    authorized = False

    class UnexpectedGate:
        def __init__(self, **_kwargs):
            nonlocal authorized
            authorized = True
            raise AssertionError("setup must complete before gateway authorization")

    monkeypatch.setattr(cb, "GatewayModelGate", UnexpectedGate)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cwd = workspace / "missing" if setup_failure == "missing_workspace" else workspace
    runtime_dir = (
        workspace / "runtime" if setup_failure == "nested_runtime" else tmp_path / "runtime"
    )
    if setup_failure == "missing_codex":

        def missing_codex(_value: str | None) -> list[str]:
            raise FileNotFoundError("Codex runtime is unavailable")

        monkeypatch.setattr(cb, "_codex_command", missing_codex)

    result = cb.run_codex_with_locus_tools(
        prompt="run",
        cwd=str(cwd),
        runtime_dir=str(runtime_dir),
        audit_path=str(tmp_path / "audit.jsonl"),
        kill_switch_path=str(tmp_path / "DISABLED"),
        run_id="run-1",
        isolation_strategy="kernel-bwrap",
        gateway_session=None,  # type: ignore[arg-type]
        ollama_base_url="http://127.0.0.1:11434/v1",
    )

    assert result.outcome == (
        "unavailable" if setup_failure in {"missing_workspace", "missing_codex"} else "failed"
    )
    assert not authorized


# --- compiler routing: harness_backend == codex -----------------------------
def test_code_node_routes_to_codex(monkeypatch):
    gc = pytest.importorskip("app.graph_compiler")

    captured = {}

    def _fake_run_codex(**kwargs):
        captured.update(kwargs)
        return cb.CodexResult(
            answer="built it", reasoning="plan", files=["x.py"], outcome="completed"
        )

    monkeypatch.setattr(cb, "run_codex", _fake_run_codex)

    class _WS:
        class _Ex:
            def workdir(self):
                return "/projects/repo"

        executor = _Ex()

    class _Binding:
        allow_outside = "ask"

    class _Prov:
        workspace = _WS()
        binding = _Binding()

    r = gc.AgentResolution(
        agent_id="sdet",
        system_prompt="sp",
        model="gpt-oss:20b",
        provider="ollama",
        base_url="http://x/v1",
        execution_mode="code",
        harness_backend="codex",
    )
    deps = gc.CompilerDeps(
        resolve_agent=lambda c: r,
        make_chat_client=lambda res: None,
        execute_native=lambda *a: {},
        mode="execute",
    )
    deps.provisioned = _Prov()

    class _Node:
        id = "build"
        type = "locus/agent"
        title = "Build"
        config = {"agent_id": "sdet", "phase": "build", "harness_backend": "codex"}

    out = gc._run_agent_node(
        _Node(), incoming=[], out_ports=[], state={"run_input": {"message": "do it"}}, deps=deps
    )
    assert out["mode"] == "codex"
    assert out["route"] == "agreed"
    assert out["response"] == "built it"
    assert captured["cwd"] == "/projects/repo" and captured["sandbox"] == "workspace-write"


def test_codex_unavailable_falls_back_to_native(monkeypatch):
    gc = pytest.importorskip("app.graph_compiler")
    monkeypatch.setattr(cb, "run_codex", lambda **k: cb.CodexResult(outcome="unavailable"))
    monkeypatch.setattr(
        gc, "_delegate_to_swe_agent", lambda node, r, p, deps: {"mode": "code", "fallback": True}
    )

    class _Prov:
        class workspace:
            class executor:
                @staticmethod
                def workdir():
                    return "/projects/repo"

        class binding:
            allow_outside = "ask"

    r = gc.AgentResolution(
        agent_id="sdet",
        system_prompt="sp",
        model="m",
        provider="ollama",
        base_url="http://x/v1",
        execution_mode="code",
        harness_backend="codex",
    )
    deps = gc.CompilerDeps(
        resolve_agent=lambda c: r,
        make_chat_client=lambda res: None,
        execute_native=lambda *a: {},
        mode="execute",
    )
    deps.provisioned = _Prov()

    class _Node:
        id = "build"
        type = "locus/agent"
        title = "Build"
        config = {"harness_backend": "codex"}

    out = gc._run_agent_node(_Node(), incoming=[], out_ports=[], state={"run_input": {}}, deps=deps)
    assert out.get("fallback") is True and out["mode"] == "code"
