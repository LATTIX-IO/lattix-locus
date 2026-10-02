"""Track A: Codex coding backend — JSONL ThreadEvent mapping + compiler routing.
Pure/unit (no codex binary, no stack)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from frontier_runtime.harness import codex_backend as cb  # noqa: E402


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
    assert m["kind"] == "reasoning" and m["text"] == "thinking…"


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
        type = "frontier/agent"
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
        type = "frontier/agent"
        title = "Build"
        config = {"harness_backend": "codex"}

    out = gc._run_agent_node(_Node(), incoming=[], out_ports=[], state={"run_input": {}}, deps=deps)
    assert out.get("fallback") is True and out["mode"] == "code"
