"""Live smoke tests for the graph compiler against a real local model.

Skipped unless a reachable OpenAI-compatible endpoint serving a coding model is
configured. Point it at your host Ollama gpt-oss:20b:

    FRONTIER_LIVE_BASE_URL=http://localhost:11434/v1 \
    FRONTIER_LIVE_MODEL=gpt-oss:20b \
    pytest -m live tests/backend/test_graph_compiler_live.py

These never run a full SWE-bench fleet (resource rule); they prove (1) a chat
agent node generates real text and (2) a single code node runs the harness loop
against a trivial spec in a temp git repo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

gc = pytest.importorskip("app.graph_compiler", reason="backend not importable")

_BASE_URL = os.getenv("FRONTIER_LIVE_BASE_URL", "")
_MODEL = os.getenv("FRONTIER_LIVE_MODEL", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (_BASE_URL and _MODEL),
        reason="set FRONTIER_LIVE_BASE_URL + FRONTIER_LIVE_MODEL to run live smoke",
    ),
]


class _Node:
    def __init__(self, d):
        self.id = d["id"]
        self.type = d["type"]
        self.title = d.get("title", d["id"])
        self.config = d.get("config", {})


class _Edge:
    def __init__(self, d):
        self.from_node = d["from"]
        self.to_node = d["to"]
        self.from_port = d.get("from_port")
        self.to_port = d.get("to_port")


def _resolution(mode="chat"):
    return gc.AgentResolution(
        agent_id="live-agent",
        system_prompt="You are a precise engineer. Answer concretely.",
        model=_MODEL,
        provider="ollama",
        base_url=_BASE_URL,
        execution_mode=mode,
        found=True,
    )


def _make_client(_r):
    from frontier_runtime.harness.llm import OpenAIChatClient

    return OpenAIChatClient(
        model=_MODEL,
        base_url=_BASE_URL,
        api_key="ollama",
        provider="openai-compatible",
        request_timeout=600.0,
    )


def test_chat_node_generates_real_text():
    nodes = [
        _Node({"id": "t", "type": "frontier/trigger", "title": "T"}),
        _Node(
            {
                "id": "a",
                "type": "frontier/agent",
                "title": "A",
                "config": {"agent_id": "live-agent"},
            }
        ),
        _Node({"id": "o", "type": "frontier/output", "title": "O"}),
    ]
    links = [
        _Edge({"from": "t", "to": "a", "from_port": "out", "to_port": "in"}),
        _Edge({"from": "a", "to": "o", "from_port": "out", "to_port": "in"}),
    ]

    def native(node, incoming, by_port):
        if "trigger" in node.type:
            return {"message": "Name three properties of a good unit test."}
        return {"message": "done"}

    deps = gc.CompilerDeps(
        resolve_agent=lambda cfg: _resolution("chat"),
        make_chat_client=_make_client,
        execute_native=native,
        run_id="run/live",
        repo_root=str(_REPO_ROOT),
        workspace=None,
        emit=lambda k, d: None,
    )
    compiled = gc.compile_frontier_graph(nodes, links, deps)
    result = gc.run_compiled_graph(
        compiled, {"message": "Name three properties of a good unit test."}, deps
    )
    a = result["node_results"]["a"]
    assert a["mode"] == "live", a
    assert len(a["response"]) > 20


def test_code_node_runs_harness_loop_on_trivial_spec(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True, capture_output=True
    )

    node = _Node(
        {
            "id": "build",
            "type": "frontier/agent",
            "title": "Build",
            "config": {"agent_id": "live-agent", "phase": "build"},
        }
    )
    deps = gc.CompilerDeps(
        resolve_agent=lambda cfg: _resolution("code"),
        make_chat_client=_make_client,
        execute_native=lambda *a: {},
        run_id="run/live-code",
        repo_root=str(repo),
        workspace={"repo_path": str(repo), "isolation": "in-place", "allow_outside": "deny"},
        emit=lambda k, d: None,
    )
    compiled = gc.compile_frontier_graph([node], [], deps)
    result = gc.run_compiled_graph(
        compiled,
        {
            "message": "Create hello.py with a function hi() returning 'hi', and test_hello.py asserting it."
        },
        deps,
    )
    res = result["node_results"]["build"]
    assert res["mode"] in {"code", "plan_only"}
    # when the model produced a patch, the workspace should reflect it
    if res.get("patch"):
        assert "hello" in res["patch"].lower() or "def" in res["patch"].lower()
