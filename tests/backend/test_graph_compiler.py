"""Tests for the canvas graph -> LangGraph compiler (apps/backend/app/graph_compiler.py).

These prove the reactflow ``graph_json`` becomes a real, runnable multi-agent
LangGraph: linear structure, named-port conditional routing, bounded loops, the
real cross-functional-development workflow running end-to-end with a scripted
client, and code-node delegation to the harness SweAgent. No network required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make ``app`` importable when tests run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

gc = pytest.importorskip("app.graph_compiler", reason="langgraph / backend not importable")
from frontier_runtime.harness.llm import ChatResponse  # noqa: E402

if not gc.LANGGRAPH_AVAILABLE:  # pragma: no cover
    pytest.skip("langgraph not installed", allow_module_level=True)


# --------------------------------------------------------------------------- #
# Lightweight duck-typed node/edge (compiler only reads these attributes)
# --------------------------------------------------------------------------- #
class _Node:
    def __init__(self, d: dict) -> None:
        self.id = d["id"]
        self.type = d["type"]
        self.title = d.get("title", d["id"])
        self.config = d.get("config", {})


class _Edge:
    def __init__(self, d: dict) -> None:
        self.from_node = d["from"]
        self.to_node = d["to"]
        self.from_port = d.get("from_port")
        self.to_port = d.get("to_port")


def _load_cross_functional_graph() -> tuple[list[_Node], list[_Edge]]:
    path = _REPO_ROOT / "examples" / "workflows" / "cross-functional-development" / "workflow.json"
    graph = json.loads(path.read_text(encoding="utf-8"))["graph"]
    return [_Node(n) for n in graph["nodes"]], [_Edge(e) for e in graph["links"]]


def _chat_resolution(agent_id: str = "a", mode: str = "chat") -> gc.AgentResolution:
    return gc.AgentResolution(
        agent_id=agent_id,
        system_prompt="You are an engineer.",
        model="gpt-oss:20b",
        provider="ollama",
        base_url="http://localhost:11434/v1",
        execution_mode=mode,
        found=True,
    )


def _native_executor(node, incoming, by_port):
    t = node.type
    if "trigger" in t:
        return {"message": "Spec: add a /health endpoint."}
    if "output" in t:
        return {
            "published": {"destination": node.config.get("destination")},
            "message": "delivered",
        }
    return {"message": f"native {node.id}"}


class _RoutingClient:
    """Votes continue_discussion twice then agreed; always approves at the gate."""

    provider = "scripted"
    model = "scripted"

    def __init__(self) -> None:
        self.consensus_calls = 0

    def complete(self, messages, **_kw):
        user = messages[-1]["content"]
        if "agreed | continue_discussion" in user:
            self.consensus_calls += 1
            tok = "continue_discussion" if self.consensus_calls <= 2 else "agreed"
            return ChatResponse(text=f"Assessment...\nDECISION: {tok}")
        if "approve | request_changes" in user:
            return ChatResponse(text="Looks complete.\nDECISION: approve")
        return ChatResponse(text="My discipline input: extend existing patterns; add tests.")


def _deps(client, *, resolve=None, workspace=None, max_loops=3, provisioned=None):
    deps = gc.CompilerDeps(
        resolve_agent=resolve or (lambda cfg: _chat_resolution(cfg.get("agent_id", "a"))),
        make_chat_client=lambda r: client,
        execute_native=_native_executor,
        run_id="run/test",
        repo_root=str(_REPO_ROOT),
        workspace=workspace,
        emit=lambda k, d: None,
        max_loop_iterations=max_loops,
    )
    deps.provisioned = provisioned
    return deps


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_linear_graph_compiles_with_entry_and_terminal():
    nodes = [
        _Node({"id": "t", "type": "frontier/trigger", "title": "T"}),
        _Node({"id": "a", "type": "frontier/agent", "title": "A", "config": {"agent_id": "x"}}),
        _Node({"id": "o", "type": "frontier/output", "title": "O"}),
    ]
    links = [
        _Edge({"from": "t", "to": "a", "from_port": "out", "to_port": "in"}),
        _Edge({"from": "a", "to": "o", "from_port": "out", "to_port": "in"}),
    ]
    compiled = gc.compile_frontier_graph(nodes, links, _deps(_RoutingClient()))
    assert compiled.entry == "t"
    assert compiled.terminals == ["o"]
    assert compiled.has_cycle is False
    assert compiled.agent_node_ids == ["a"]
    assert compiled.routing_nodes == {}


def test_cross_functional_graph_is_cyclic_with_two_routers():
    nodes, links = _load_cross_functional_graph()
    compiled = gc.compile_frontier_graph(nodes, links, _deps(_RoutingClient()))
    assert compiled.has_cycle is True
    assert compiled.entry == "trigger"
    assert compiled.terminals == ["output"]
    assert set(compiled.routing_nodes) == {"consensus", "gate"}
    assert sorted(compiled.routing_nodes["consensus"]) == ["agreed", "continue_discussion"]
    assert sorted(compiled.routing_nodes["gate"]) == ["approve", "request_changes"]


# --------------------------------------------------------------------------- #
# Routing + bounded loop
# --------------------------------------------------------------------------- #
def test_router_forces_forward_port_after_loop_bound():
    deps = _deps(_RoutingClient(), max_loops=3)

    def ancestors_of(_node_id):  # facilitate is an ancestor of consensus
        return {"facilitate", "backend"}

    port_targets = {"continue_discussion": "facilitate", "agreed": "build"}
    router = gc._router_for("consensus", port_targets, deps, ancestors_of)

    # model keeps voting to continue, but the bound forces the forward port
    state = {
        "node_outputs": {"consensus": {"route": "continue_discussion"}},
        "loop_counts": {"consensus": 3},
    }
    assert router(state) == "agreed"  # forward (build is not an ancestor)

    # below the bound, honour the model's choice
    state2 = {
        "node_outputs": {"consensus": {"route": "continue_discussion"}},
        "loop_counts": {"consensus": 1},
    }
    assert router(state2) == "continue_discussion"


def test_extract_route_parses_decision_line_and_synonyms():
    assert gc._extract_route("...\nDECISION: agreed", ["agreed", "continue_discussion"]) == "agreed"
    assert gc._extract_route("we should keep going", ["agreed", "continue_discussion"]) == ""
    assert gc._extract_route("approve it", ["approve", "request_changes"]) == "approve"


# --------------------------------------------------------------------------- #
# End-to-end run of the real workflow (scripted client, no network)
# --------------------------------------------------------------------------- #
def test_cross_functional_graph_runs_and_terminates():
    nodes, links = _load_cross_functional_graph()
    client = _RoutingClient()

    def resolve(cfg):
        # the build node is the implementer; with no workspace it degrades to a
        # plan-only chat (so the scripted run stays offline)
        mode = "code" if cfg.get("phase") == "build" else "chat"
        return _chat_resolution(cfg.get("agent_id", "a"), mode)

    deps = _deps(client, resolve=resolve, workspace=None, max_loops=3)
    compiled = gc.compile_frontier_graph(nodes, links, deps)
    result = gc.run_compiled_graph(compiled, {"message": "Spec: add a /health endpoint."}, deps)

    node_results = result["node_results"]
    assert set(node_results) == {n.id for n in nodes}, "every node should have executed"
    # discussion loop ran more than once but was bounded
    assert 1 < result["loop_counts"]["consensus"] <= 4
    assert node_results["consensus"]["route"] == "agreed"
    assert node_results["gate"]["route"] == "approve"
    assert "output" in node_results


# --------------------------------------------------------------------------- #
# Code-node delegation to the harness SweAgent
# --------------------------------------------------------------------------- #
def test_code_node_delegates_to_swe_agent(monkeypatch):
    from frontier_runtime.harness import swe_agent as swe_mod
    from frontier_runtime.harness.loop import LoopOutcome

    class _FakeResult:
        outcome = LoopOutcome.SUBMITTED
        patch = "diff --git a/x b/x\n+ok"
        answer = "implemented"
        steps = 3
        has_patch = True

    monkeypatch.setattr(swe_mod.SweAgent, "solve", lambda self, task: _FakeResult())

    # a fake provisioned workspace (executor unused because solve is patched)
    class _WS:
        executor = object()
        base_ref = "HEAD"

    class _Binding:
        test_command = ""
        allow_outside = "ask"

    class _Prov:
        workspace = _WS()
        binding = _Binding()

    node = _Node(
        {
            "id": "build",
            "type": "frontier/agent",
            "title": "Build",
            "config": {"agent_id": "sdet", "phase": "build"},
        }
    )
    deps = _deps(
        _RoutingClient(), resolve=lambda cfg: _chat_resolution("sdet", "code"), provisioned=_Prov()
    )

    res = gc._run_agent_node(
        node, incoming=[], out_ports=[], state={"run_input": {"message": "build it"}}, deps=deps
    )
    assert res["mode"] == "code"
    assert res["patch"].startswith("diff --git")
    assert res["route"] == "agreed"


def test_code_node_without_workspace_degrades_to_plan_only():
    node = _Node(
        {
            "id": "build",
            "type": "frontier/agent",
            "title": "Build",
            "config": {"agent_id": "sdet", "phase": "build"},
        }
    )
    deps = _deps(
        _RoutingClient(), resolve=lambda cfg: _chat_resolution("sdet", "code"), provisioned=None
    )
    res = gc._run_agent_node(
        node, incoming=[], out_ports=[], state={"run_input": {"message": "build it"}}, deps=deps
    )
    assert res["mode"] in {"plan_only", "simulated"}
    assert res["route"] == "agreed"  # never traps the graph when it cannot build
