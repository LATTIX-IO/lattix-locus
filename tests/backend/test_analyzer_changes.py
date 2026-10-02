"""Tests for read+exec analyzer agents, file-change tracking, and the updated
cross-functional graph. No network required."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

gc = pytest.importorskip("app.graph_compiler")
from frontier_runtime.harness.executor import LocalDirectExecutor  # noqa: E402
from frontier_runtime.harness.tools import CodingToolset  # noqa: E402
from frontier_runtime.harness.workspace import Workspace  # noqa: E402

requires_git = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git required")


# --- A1: allow_edits read+exec toolset --------------------------------------
def test_allow_edits_false_blocks_mutation(tmp_path):
    (tmp_path / "f.txt").write_text("hello\n")
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ro = CodingToolset(workspace=ws, allow_edits=False)
    # view is allowed
    assert "hello" in ro.dispatch("str_replace_editor", {"command": "view", "path": "f.txt"})
    # create / str_replace / insert are denied
    assert "[denied]" in ro.dispatch(
        "str_replace_editor", {"command": "create", "path": "g.txt", "file_text": "x"}
    )
    assert "[denied]" in ro.dispatch(
        "str_replace_editor",
        {"command": "str_replace", "path": "f.txt", "old_str": "hello", "new_str": "bye"},
    )
    assert not (tmp_path / "g.txt").exists()  # nothing written

    # default toolset still allows edits
    rw = CodingToolset(workspace=ws, allow_edits=True)
    rw.dispatch("str_replace_editor", {"command": "create", "path": "g.txt", "file_text": "x"})
    assert (tmp_path / "g.txt").exists()


# --- B: changed_files -------------------------------------------------------
@requires_git
def test_changed_files_reports_status_and_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, capture_output=True
    )
    # modify a.py + add b.py
    (repo / "a.py").write_text("x = 1\ny = 2\n")
    (repo / "b.py").write_text("print('new')\n")

    ws = Workspace(run_id="t", executor=LocalDirectExecutor(repo), base_ref="HEAD")
    files = {f["path"]: f for f in ws.changed_files()}
    assert "a.py" in files and "b.py" in files
    assert files["b.py"]["status"] == "A"
    assert files["a.py"]["additions"] >= 1
    assert "+y = 2" in files["a.py"]["diff"]


# --- A2: analyzer delegation never edits + returns findings -----------------
def test_analyzer_delegate_returns_findings(monkeypatch):
    from frontier_runtime.harness import swe_agent as swe_mod
    from frontier_runtime.harness.loop import LoopOutcome

    captured = {}

    class _Result:
        outcome = LoopOutcome.SUBMITTED
        answer = "Found a HIGH severity SQL injection in db.py; must fix."
        patch = ""
        steps = 4
        has_patch = False

    def _fake_solve(self, task):
        captured["allow_edits"] = self.allow_edits
        return _Result()

    monkeypatch.setattr(swe_mod.SweAgent, "solve", _fake_solve)

    class _WS:
        executor = object()
        base_ref = "HEAD"

    class _Binding:
        test_command = ""
        allow_outside = "ask"

    class _Prov:
        workspace = _WS()
        binding = _Binding()

    class _FakeClient:
        provider = "ollama"
        model = "gpt-oss:20b"

        def complete(self, *a, **k):
            from frontier_runtime.harness.llm import ChatResponse

            return ChatResponse(text="")

    r = gc.AgentResolution(
        agent_id="security-auditor-agent",
        system_prompt="sp",
        model="m",
        provider="ollama",
        base_url="http://x/v1",
        execution_mode="analyze",
    )
    deps = gc.CompilerDeps(
        resolve_agent=lambda c: r,
        make_chat_client=lambda res: _FakeClient(),
        execute_native=lambda *a: {},
        mode="execute",
    )
    deps.provisioned = _Prov()

    class _Node:
        id = "security-audit"
        type = "frontier/agent"
        title = "Security Audit"
        config = {
            "agent_id": "security-auditor-agent",
            "phase": "verify",
            "harness_mode": "analyze",
        }

    out = gc._run_agent_node(
        _Node(), incoming=[], out_ports=[], state={"run_input": {"message": "spec"}}, deps=deps
    )
    assert captured["allow_edits"] is False  # analyzer cannot edit
    assert out["mode"] == "analyze"
    assert out["verdict"] == "request_changes"  # "HIGH severity ... must fix"
    assert "SQL injection" in out["response"]


# --- A3: the updated cross-functional graph compiles ------------------------
def test_cross_functional_v2_graph_compiles():
    path = _REPO_ROOT / "examples" / "workflows" / "cross-functional-development" / "workflow.json"
    graph = json.loads(path.read_text(encoding="utf-8"))["graph"]
    node_ids = {n["id"] for n in graph["nodes"]}
    assert {"security-audit", "qa-verify", "perf-verify"} <= node_ids

    class N:
        def __init__(self, d):
            self.id = d["id"]
            self.type = d["type"]
            self.title = d.get("title", d["id"])
            self.config = d.get("config", {})

    class E:
        def __init__(self, d):
            self.from_node = d["from"]
            self.to_node = d["to"]
            self.from_port = d.get("from_port")
            self.to_port = d.get("to_port")

    nodes = [N(n) for n in graph["nodes"]]
    links = [E(e) for e in graph["links"]]

    def resolve(cfg):
        mode = (
            "analyze"
            if cfg.get("harness_mode") == "analyze"
            else ("code" if cfg.get("phase") == "build" else "chat")
        )
        return gc.AgentResolution(
            agent_id=cfg.get("agent_id", "a"),
            system_prompt="sp",
            model="m",
            provider="ollama",
            base_url="http://x/v1",
            execution_mode=mode,
        )

    deps = gc.CompilerDeps(
        resolve_agent=resolve,
        make_chat_client=lambda r: None,
        execute_native=lambda *a: {},
        mode="execute",
    )
    compiled = gc.compile_frontier_graph(nodes, links, deps)
    assert compiled.has_cycle is True
    assert set(compiled.routing_nodes) == {"consensus", "gate"}
