"""Compile a reactflow ``graph_json`` into a real, runnable LangGraph multi-agent system.

The Workflow Studio canvas saves a graph of ``nodes`` + ``links`` (see
``frontier-graph/1.0``). Historically that graph was cosmetic at run time — the
backend executed a hardcoded single-agent loop and ignored the canvas. This
module turns the canvas into the actual execution engine:

* every node becomes a LangGraph ``StateGraph`` node;
* plain links become edges (multi-target = parallel fan-out, LangGraph runs them
  in one super-step; multiple links into a node = fan-in barrier);
* a node that emits more than one distinct ``from_port`` (e.g. a consensus node
  with ``agreed`` vs ``continue_discussion``) becomes a *conditional edge* whose
  router reads the node's ``route`` decision and enforces a bounded-loop guard so
  back-edges (consensus→facilitate, gate→build) provably terminate;
* ``frontier/agent`` nodes resolve their ``config.agent_id`` to the studio
  agent's real system prompt + model (gpt-oss:20b on local Ollama) and either
  answer in one shot (``chat``), run the harness coding loop for real file edits
  + tests (``code`` → ``SweAgent``), or run the full collaborative team
  (``team`` → ``CollaborativeTeam``);
* every other node type delegates to the existing native ``_execute_node`` so we
  never re-implement trigger/output/memory/retrieval/guardrail semantics.

The module is FastAPI-free and takes all backend-specific behaviour via
``CompilerDeps`` (dependency injection, mirroring ``GeneratedArtifactService``),
so it is importable and unit-testable without booting the app, and ``main.py``
can import it without a circular dependency.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Optional, TypedDict

try:  # langgraph is a declared dependency; degrade clearly if it is ever absent.
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - env dependent
    END = "__end__"  # type: ignore[assignment]
    START = "__start__"  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Shared state + reducers
# --------------------------------------------------------------------------- #
def _merge_dicts(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Key-wise merge. Each node writes only its own key, so this is lossless
    even when parallel branches update concurrently."""
    merged = dict(old or {})
    merged.update(new or {})
    return merged


def _take_last(old: str, new: str) -> str:
    return new if new else old


class GraphState(TypedDict, total=False):
    message: Annotated[str, _take_last]  # current live payload (best-effort)
    run_input: dict[str, Any]  # original POST input (set once)
    node_outputs: Annotated[dict[str, Any], _merge_dicts]  # node_id -> result
    loop_counts: Annotated[dict[str, int], _merge_dicts]  # node_id -> times entered
    events: Annotated[list[dict[str, Any]], operator.add]
    artifacts: Annotated[list[dict[str, Any]], operator.add]


# --------------------------------------------------------------------------- #
# Resolution + dependency injection
# --------------------------------------------------------------------------- #
@dataclass
class AgentResolution:
    """Everything an agent node needs, resolved from ``config.agent_id``."""

    agent_id: str
    system_prompt: str
    model: str  # bare model id, e.g. "gpt-oss:20b"
    provider: str  # "ollama"
    base_url: str  # OpenAI-compatible endpoint
    temperature: float = 0.2
    top_p: float = 0.95
    capability_profile: str = ""
    execution_mode: str = "chat"  # chat | code | team
    reasoning_effort: str = ""  # "" | low | medium | high
    harness_backend: str = "native"  # native (SweAgent) | codex
    found: bool = True


@dataclass
class CompilerDeps:
    # node.config -> AgentResolution
    resolve_agent: Callable[[dict[str, Any]], AgentResolution]
    # AgentResolution -> harness ChatClient (e.g. OpenAIChatClient on Ollama)
    make_chat_client: Callable[[AgentResolution], Any]
    # (node, incoming, incoming_by_port) -> result dict ; reuses main._execute_node
    execute_native: Callable[
        [Any, list[dict[str, Any]], dict[str, list[dict[str, Any]]]], dict[str, Any]
    ]
    run_id: str = "run/default"
    repo_root: Optional[str] = None
    workspace: Optional[dict[str, Any]] = None  # WorkspaceBinding.from_payload input
    emit: Optional[Callable[[str, dict[str, Any]], None]] = None
    # Out-of-bounds (working-folder) permission requests from the harness toolset.
    on_escalation: Optional[Callable[[dict[str, Any]], None]] = None
    max_loop_iterations: int = 4
    node_timeout_s: int = 600
    # gpt-oss is a reasoning model: it spends tokens on an analysis channel
    # before the final answer, so chat turns need a generous budget.
    max_chat_tokens: int = 4096
    # Run-level focus: execute = tools/code/team enabled; plan = analyze + produce
    # an execution plan (no file mutation); chat = pure conversation (no tools).
    mode: str = "execute"
    # run-scoped, populated by run_compiled_graph:
    provisioned: Any = None  # ProvisionedWorkspace | None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.emit:
            try:
                self.emit(kind, data)
            except Exception:  # noqa: BLE001 - telemetry must never break a run
                pass


@dataclass
class CompiledGraph:
    app: Any  # compiled langgraph app
    entry: str
    terminals: list[str]
    has_cycle: bool
    agent_node_ids: list[str]
    routing_nodes: dict[str, list[str]]
    node_count: int


# --------------------------------------------------------------------------- #
# Graph topology helpers (pure; do not import main)
# --------------------------------------------------------------------------- #
def _norm_type(node_type: str) -> str:
    candidate = str(node_type or "").strip()
    if not candidate:
        return "frontier/unknown"
    return candidate if candidate.startswith("frontier/") else f"frontier/{candidate}"


def _port(edge: Any) -> str:
    return str(getattr(edge, "from_port", None) or "out")


def _classify(nodes: list[Any], links: list[Any]) -> dict[str, Any]:
    by_id = {n.id: n for n in nodes}
    out_links: dict[str, list[Any]] = {n.id: [] for n in nodes}
    in_links: dict[str, list[Any]] = {n.id: [] for n in nodes}
    for e in links:
        if e.from_node in out_links and e.to_node in in_links:
            out_links[e.from_node].append(e)
            in_links[e.to_node].append(e)

    # routing node = emits >1 distinct from_port
    routing_nodes: dict[str, list[str]] = {}
    for nid, edges in out_links.items():
        ports = sorted({_port(e) for e in edges})
        if len(ports) > 1:
            routing_nodes[nid] = ports

    entry = next(
        (n.id for n in nodes if _norm_type(n.type) == "frontier/trigger" or not in_links[n.id]),
        nodes[0].id if nodes else "",
    )
    terminals = [
        n.id for n in nodes if not out_links[n.id] or _norm_type(n.type) == "frontier/output"
    ]

    # ancestors via reverse reachability (used to find the loop's forward port)
    rev: dict[str, set[str]] = {n.id: set() for n in nodes}
    for e in links:
        if e.from_node in rev and e.to_node in rev:
            rev[e.to_node].add(e.from_node)

    def ancestors(node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(rev.get(node_id, set()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(rev.get(cur, set()))
        return seen

    has_cycle = _has_cycle([n.id for n in nodes], links)
    return {
        "by_id": by_id,
        "out_links": out_links,
        "in_links": in_links,
        "routing_nodes": routing_nodes,
        "entry": entry,
        "terminals": terminals,
        "ancestors": ancestors,
        "has_cycle": has_cycle,
    }


def _has_cycle(node_ids: list[str], links: list[Any]) -> bool:
    indegree = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in links:
        if e.from_node in indegree and e.to_node in indegree:
            adj[e.from_node].append(e.to_node)
            indegree[e.to_node] += 1
    queue = [nid for nid, d in indegree.items() if d == 0]
    visited = 0
    while queue:
        cur = queue.pop()
        visited += 1
        for nxt in adj[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return visited != len(node_ids)


def _forward_port(
    node_id: str, port_targets: dict[str, str], ancestors_of: Callable[[str], set[str]]
) -> str | None:
    """The port whose target is not an ancestor of this node — i.e. the
    non-back-edge that makes the loop progress (consensus→build, gate→output)."""
    anc = ancestors_of(node_id)
    forward = [p for p, tgt in port_targets.items() if tgt not in anc and tgt != node_id]
    if forward:
        # prefer a conventional "go forward" name if present
        for pref in ("agreed", "approve", "approved", "out", "continue", "yes"):
            if pref in forward:
                return pref
        return forward[0]
    return None


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
_FORWARD_SYNONYMS = {"agreed", "approve", "approved", "yes", "done", "complete", "ship"}
_BACK_SYNONYMS = {"continue_discussion", "request_changes", "retry", "again", "no", "revise"}


def _extract_route(text: str, ports: list[str]) -> str:
    """Parse the agent's chosen port from its reply.

    Honours an explicit ``DECISION: <port>`` line first, then a bare port name,
    then forward/back synonyms mapped onto the available ports.
    """
    if not text or not ports:
        return ""
    low = text.lower()
    # explicit decision line
    import re

    m = re.search(r"decision\s*[:\-]\s*([a-z_]+)", low)
    candidates: list[str] = []
    if m:
        candidates.append(m.group(1))
    # bare port mentions (search the tail first)
    tail = low[-400:]
    for p in ports:
        if p.lower() in tail:
            candidates.append(p.lower())
    for cand in candidates:
        for p in ports:
            if cand == p.lower():
                return p
    # synonym mapping
    forward_ports = [p for p in ports if p.lower() in _FORWARD_SYNONYMS]
    back_ports = [p for p in ports if p.lower() in _BACK_SYNONYMS]
    if any(s in tail for s in _FORWARD_SYNONYMS) and forward_ports:
        return forward_ports[0]
    if any(s in tail for s in _BACK_SYNONYMS) and back_ports:
        return back_ports[0]
    return ""


def _router_for(
    node_id: str,
    port_targets: dict[str, str],
    deps: CompilerDeps,
    ancestors_of: Callable[[str], set[str]],
):
    forward = _forward_port(node_id, port_targets, ancestors_of)

    def route(state: GraphState) -> str:
        res = state.get("node_outputs", {}).get(node_id, {}) or {}
        chosen = str(res.get("route") or "").strip()
        counts = state.get("loop_counts", {}) or {}
        # bounded-loop guard: once this routing node has fired enough times,
        # force the forward (non-back-edge) port so loops always terminate.
        if counts.get(node_id, 0) >= deps.max_loop_iterations:
            forced = forward or next(iter(port_targets), "")
            deps._emit("loop_bound", node_id=node_id, forced_port=forced)
            return forced
        if chosen in port_targets:
            return chosen
        return forward or next(iter(port_targets), "")

    return route


# --------------------------------------------------------------------------- #
# Incoming gathering + prompt composition
# --------------------------------------------------------------------------- #
def _gather_incoming(
    node_id: str, in_links: dict[str, list[Any]], node_outputs: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    incoming: list[dict[str, Any]] = []
    by_port: dict[str, list[dict[str, Any]]] = {}
    for e in in_links.get(node_id, []):
        res = node_outputs.get(e.from_node)
        if res is None:
            continue
        incoming.append(res)
        by_port.setdefault(str(e.to_port or "in"), []).append(res)
    return incoming, by_port


def _spec_text(state: GraphState) -> str:
    run_input = state.get("run_input", {}) or {}
    return str(run_input.get("spec") or run_input.get("message") or "").strip()


def _compose_user_prompt(node: Any, incoming: list[dict[str, Any]], state: GraphState) -> str:
    spec = _spec_text(state)
    parts = [f"Workflow task for node '{getattr(node, 'title', node.id)}'."]
    if spec:
        parts.append(f"\nSpecification / objective:\n{spec[:4000]}")
    if incoming:
        ctx_lines = []
        for item in incoming[-6:]:
            if not isinstance(item, dict):
                continue
            msg = item.get("message") or item.get("response") or item.get("agreed_design")
            who = item.get("agent_id") or item.get("title") or ""
            if msg:
                ctx_lines.append(f"- {who}: {str(msg)[:600]}")
        if ctx_lines:
            parts.append("\nUpstream contributions:\n" + "\n".join(ctx_lines))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Agent node execution
# --------------------------------------------------------------------------- #
def _reasoning_text(resp: Any) -> str:
    """Extract the reasoning/analysis channel of a reasoning model (gpt-oss/
    Harmony) from a ChatResponse's raw completion, when present."""
    raw = getattr(resp, "raw", None)
    try:
        msg = raw.choices[0].message
    except Exception:  # noqa: BLE001
        return ""
    for attr in ("reasoning", "reasoning_content"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _run_agent_node(
    node: Any,
    incoming: list[dict[str, Any]],
    out_ports: list[str],
    state: GraphState,
    deps: CompilerDeps,
) -> dict[str, Any]:
    config = node.config if isinstance(getattr(node, "config", None), dict) else {}
    r = deps.resolve_agent(config)
    user_prompt = _compose_user_prompt(node, incoming, state)

    # Run-level mode gates real tool/file work. Only "execute" runs delegate to
    # the harness coding loop / team; "plan" and "chat" stay single-shot.
    if deps.mode == "execute":
        if r.execution_mode == "code":
            if r.harness_backend == "codex":
                return _delegate_to_codex_agent(node, r, user_prompt, deps)
            return _delegate_to_swe_agent(node, r, user_prompt, deps)
        if r.execution_mode == "team":
            return _delegate_to_collaborative_team(node, r, user_prompt, deps)
        if r.execution_mode == "analyze":
            return _delegate_to_analyzer_agent(node, r, user_prompt, deps)
    elif deps.mode == "plan":
        user_prompt += (
            "\n\nFocus: PLAN ONLY. Analyze the task and produce a concrete, step-by-step "
            "execution plan (files/components to change, approach, tests, risks). Do NOT "
            "execute tools or modify files."
        )

    # chat node — single-shot with the studio agent's real system prompt.
    if len(out_ports) > 1:
        user_prompt += (
            "\n\nWhen you have decided, end your reply with a line exactly:\n"
            f"DECISION: <one of: {' | '.join(out_ports)}>"
        )
    try:
        client = deps.make_chat_client(r)
        resp = client.complete(
            [
                {"role": "system", "content": r.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=r.temperature,
            top_p=r.top_p,
            max_tokens=deps.max_chat_tokens,
            reasoning_effort=r.reasoning_effort or None,
        )
        text = (resp.text or "").strip()
        reasoning = _reasoning_text(resp).strip()
        if not text:
            # reasoning models (gpt-oss/Harmony) can put everything on the
            # analysis channel and leave content empty if the turn ran long;
            # fall back to that channel so an agent never hands back blank.
            text = reasoning
            reasoning = ""  # don't duplicate it as both message and reasoning
        usage = getattr(resp, "usage", {}) or {}
        mode = "live"
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the run
        text = f"[agent unavailable: {exc}]"
        reasoning = ""
        usage = {}
        mode = "simulated"
    route = _extract_route(text, out_ports) if len(out_ports) > 1 else ""
    return {
        "agent_id": r.agent_id,
        "title": getattr(node, "title", node.id),
        "model": f"{r.provider}/{r.model}" if r.provider else r.model,
        "response": text,
        "message": text,
        "summary": text[:240],
        "reasoning": reasoning,
        "route": route,
        "usage": usage,
        "mode": mode,
        "resolved": r.found,
    }


def _delegate_to_swe_agent(
    node: Any, r: AgentResolution, user_prompt: str, deps: CompilerDeps
) -> dict[str, Any]:
    """Real implementation: run the harness coding loop in the bound workspace."""
    prov = deps.provisioned
    if prov is None:
        # No repo bound — degrade to a planning answer rather than editing an
        # arbitrary/over-broad tree. Safe default.
        return _plan_only_fallback(node, r, user_prompt, deps, reason="no_workspace_bound")
    try:
        from frontier_runtime.harness.model_profiles import resolve_profile
        from frontier_runtime.harness.swe_agent import SweAgent, SweTask

        profile = None
        try:
            profile = resolve_profile(
                r.provider or "openai-compatible", r.model, profile_id=r.capability_profile or None
            )
        except Exception:  # noqa: BLE001
            profile = None

        ws = prov.workspace
        binding = prov.binding
        task = SweTask(
            instance_id=f"{deps.run_id}-{node.id}",
            problem_statement=user_prompt,
            executor=ws.executor,
            test_command=getattr(binding, "test_command", "") or "",
            base_ref=getattr(ws, "base_ref", "") or "HEAD",
        )
        agent = SweAgent(
            client=deps.make_chat_client(r),
            profile=profile,
            system_prompt_override=r.system_prompt,
            out_of_bounds=getattr(binding, "allow_outside", "ask") or "ask",
            on_event=(lambda kind, data: deps._emit(f"swe.{kind}", node_id=node.id, **data)),
            on_escalation=deps.on_escalation,
        )
        deps._emit("code_node_started", node_id=node.id, agent_id=r.agent_id)
        result = agent.solve(task)
        solved = bool(getattr(result, "has_patch", False))
        return {
            "agent_id": r.agent_id,
            "title": getattr(node, "title", node.id),
            "model": f"{r.provider}/{r.model}",
            "response": result.answer or "",
            "message": result.answer or "",
            "summary": f"{result.outcome}; steps={result.steps}; patch={'yes' if solved else 'no'}",
            "patch": result.patch or "",
            "route": "agreed" if solved else "request_changes",
            "outcome": str(result.outcome),
            "steps": result.steps,
            "mode": "code",
            "artifacts": [{"type": "patch", "node": node.id, "diff": result.patch or ""}],
        }
    except Exception as exc:  # noqa: BLE001
        return _plan_only_fallback(node, r, user_prompt, deps, reason=f"swe_error: {exc}")


def _delegate_to_codex_agent(
    node: Any, r: AgentResolution, user_prompt: str, deps: CompilerDeps
) -> dict[str, Any]:
    """Run the build via the Codex subprocess backend in the bound worktree.
    xFrontier still owns memory (prompt), workspace, diff capture, and events.
    Degrades to the native SweAgent if the codex binary is unavailable."""
    prov = deps.provisioned
    if prov is None:
        return _plan_only_fallback(node, r, user_prompt, deps, reason="no_workspace_bound")
    try:
        from frontier_runtime.harness import codex_backend as cb

        binding = prov.binding
        cwd = prov.workspace.executor.workdir()
        allow_outside = getattr(binding, "allow_outside", "ask") or "ask"
        sandbox = "read-only" if allow_outside == "deny" else "workspace-write"

        def _sink(kind: str, data: dict[str, Any]) -> None:
            payload = {k: v for k, v in data.items() if k != "kind"}
            deps._emit(f"codex.{kind}", node_id=node.id, **payload)

        deps._emit("codex_node_started", node_id=node.id, agent_id=r.agent_id)
        result = cb.run_codex(
            prompt=user_prompt,
            cwd=cwd,
            model=r.model,
            sandbox=sandbox,
            on_event=_sink,
            timeout=deps.node_timeout_s if deps.node_timeout_s else 900,
        )
        if result.outcome == "unavailable":
            # No codex binary on this host — fall back to the native engine.
            deps._emit("codex_unavailable", node_id=node.id)
            return _delegate_to_swe_agent(node, r, user_prompt, deps)
        return {
            "agent_id": r.agent_id,
            "title": getattr(node, "title", node.id),
            "model": f"{r.provider}/{r.model}",
            "response": result.answer or "",
            "message": result.answer or "",
            "reasoning": result.reasoning or "",
            "summary": f"codex {result.outcome}; files={len(result.files)}",
            "patch": "",  # the real diff is captured by workspace.changed_files()
            "route": "agreed" if result.outcome == "completed" else "request_changes",
            "outcome": result.outcome,
            "mode": "codex",
            "artifacts": [{"type": "codex_files", "node": node.id, "files": result.files}],
        }
    except Exception as exc:  # noqa: BLE001
        return _plan_only_fallback(node, r, user_prompt, deps, reason=f"codex_error: {exc}")


def _analyzer_focus(agent_id: str) -> str:
    """Discipline-specific instructions for a read+exec analyzer agent."""
    aid = (agent_id or "").lower()
    if "security" in aid:
        return (
            "You are doing a SECURITY AUDIT. Use your tools to read the codebase and the "
            "change, then: (1) find vulnerabilities (injection, authn/z, secrets, SSRF, "
            "deserialization, path traversal, unsafe deps); (2) call out weak security "
            "architecture (trust boundaries, input validation, least privilege); (3) produce a "
            "concise THREAT MODEL (assets, entry points, trust boundaries, top risks, "
            "mitigations). Run available scanners via execute_bash if present (e.g. semgrep, "
            "bandit, npm audit, gitleaks, pip-audit). Report findings with severity; do not edit files."
        )
    if "performance" in aid:
        return (
            "You are doing a PERFORMANCE REVIEW. Read the changed code, identify hotspots "
            "(N+1 queries, unbounded loops, sync I/O on hot paths, missing indexes/caching), "
            "and run any benchmarks/profilers present via execute_bash/run_tests. Report "
            "concrete, prioritized fixes with expected impact; do not edit files."
        )
    # qa / sdet / default
    return (
        "You are doing QA / TEST VERIFICATION. Discover and RUN the test suite with the "
        "run_tests tool (or execute_bash, e.g. pytest / npm test). Report failures with the "
        "exact output, missing coverage, and concrete fixes the engineers should make. Do not edit files."
    )


def _delegate_to_analyzer_agent(
    node: Any, r: AgentResolution, user_prompt: str, deps: CompilerDeps
) -> dict[str, Any]:
    """Read+exec analyzer (security / QA / performance): reads code, runs
    tests/scanners in the bound workspace, and returns findings — never edits."""
    prov = deps.provisioned
    if prov is None:
        return _plan_only_fallback(node, r, user_prompt, deps, reason="no_workspace_bound")
    try:
        from frontier_runtime.harness.model_profiles import resolve_profile
        from frontier_runtime.harness.swe_agent import SweAgent, SweTask

        try:
            profile = resolve_profile(
                r.provider or "openai-compatible", r.model, profile_id=r.capability_profile or None
            )
        except Exception:  # noqa: BLE001
            profile = None

        ws = prov.workspace
        binding = prov.binding
        task = SweTask(
            instance_id=f"{deps.run_id}-{node.id}",
            problem_statement=f"{user_prompt}\n\n{_analyzer_focus(r.agent_id)}",
            executor=ws.executor,
            test_command=getattr(binding, "test_command", "") or "",
            base_ref=getattr(ws, "base_ref", "") or "HEAD",
        )
        agent = SweAgent(
            client=deps.make_chat_client(r),
            profile=profile,
            system_prompt_override=r.system_prompt,
            out_of_bounds=getattr(binding, "allow_outside", "ask") or "ask",
            allow_edits=False,  # read + exec only; analyzers never mutate files
            on_event=(lambda kind, data: deps._emit(f"analyze.{kind}", node_id=node.id, **data)),
            on_escalation=deps.on_escalation,
        )
        deps._emit("analyze_node_started", node_id=node.id, agent_id=r.agent_id)
        result = agent.solve(task)
        findings = (result.answer or "").strip()
        # Heuristic verdict: flag changes when the analyzer reports blocking issues.
        low = findings.lower()
        blocking = any(
            kw in low
            for kw in (
                "critical",
                "high severity",
                "vulnerab",
                "test failed",
                "tests failed",
                "fail:",
                "must fix",
                "blocking",
            )
        )
        route = "request_changes" if blocking else "agreed"
        return {
            "agent_id": r.agent_id,
            "title": getattr(node, "title", node.id),
            "model": f"{r.provider}/{r.model}",
            "response": findings or "(no findings reported)",
            "message": findings,
            "summary": findings[:240],
            "route": route,
            "verdict": "request_changes" if blocking else "approve",
            "steps": result.steps,
            "mode": "analyze",
        }
    except Exception as exc:  # noqa: BLE001
        return _plan_only_fallback(node, r, user_prompt, deps, reason=f"analyze_error: {exc}")


def _delegate_to_collaborative_team(
    node: Any, r: AgentResolution, user_prompt: str, deps: CompilerDeps
) -> dict[str, Any]:
    prov = deps.provisioned
    if prov is None:
        return _plan_only_fallback(node, r, user_prompt, deps, reason="no_workspace_bound")
    try:
        from frontier_runtime.harness.collaboration import build_collaborative_team
        from frontier_runtime.harness.swe_agent import SweTask

        team = build_collaborative_team(
            client_for=lambda role: deps.make_chat_client(r),
            repo_root=deps.repo_root,
            max_discussion_rounds=2,
            max_build_rounds=2,
            out_of_bounds=getattr(prov.binding, "allow_outside", "ask") or "ask",
            on_event=(lambda kind, data: deps._emit(f"team.{kind}", node_id=node.id, **data)),
            on_escalation=deps.on_escalation,
        )
        task = SweTask(
            instance_id=f"{deps.run_id}-{node.id}",
            problem_statement=user_prompt,
            executor=prov.workspace.executor,
            test_command=getattr(prov.binding, "test_command", "") or "",
        )
        deps._emit("team_node_started", node_id=node.id)
        cr = team.run(task, spec=user_prompt)
        return {
            "agent_id": r.agent_id,
            "title": getattr(node, "title", node.id),
            "response": cr.handback,
            "message": cr.handback,
            "summary": cr.handback[:240],
            "patch": cr.final_patch or "",
            "approved": bool(cr.approved),
            "route": "agreed" if cr.approved else "request_changes",
            "mode": "team",
            "artifacts": [
                {"type": "team_transcript", "node": node.id, "text": cr.chat()},
                {"type": "patch", "node": node.id, "diff": cr.final_patch or ""},
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return _plan_only_fallback(node, r, user_prompt, deps, reason=f"team_error: {exc}")


def _plan_only_fallback(
    node: Any, r: AgentResolution, user_prompt: str, deps: CompilerDeps, *, reason: str
) -> dict[str, Any]:
    """When real code execution can't run (no bound repo / harness error), have
    the agent produce an implementation plan instead of editing files."""
    note = (
        "No repository was bound to this run, so the implementer produced a plan instead of "
        "editing files. Pick a target folder with the composer's folder selector (next to the "
        "model), then re-run so code nodes write real code. (A path typed in the prompt is not a "
        "binding.)"
        if reason == "no_workspace_bound"
        else f"Code execution degraded ({reason}); produced an implementation plan instead."
    )
    deps._emit("code_node_degraded", node_id=node.id, reason=reason, note=note)
    prompt = (
        user_prompt
        + "\n\nNote: no writable workspace is bound for this run, so produce a concrete "
        "implementation plan (files to change, key functions, tests to add) rather than code."
    )
    try:
        client = deps.make_chat_client(r)
        resp = client.complete(
            [
                {"role": "system", "content": r.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=r.temperature,
            top_p=r.top_p,
            max_tokens=deps.max_chat_tokens,
        )
        text = (resp.text or "").strip()
        mode = "plan_only"
    except Exception as exc:  # noqa: BLE001
        text = f"[implementer unavailable: {exc}]"
        mode = "simulated"
    return {
        "agent_id": r.agent_id,
        "title": getattr(node, "title", node.id),
        "response": text,
        "message": text,
        "summary": text[:240],
        "patch": "",
        "route": "agreed",  # don't trap the graph in a loop when we can't build
        "mode": mode,
        "degraded_reason": reason,
    }


# --------------------------------------------------------------------------- #
# Node runner factory
# --------------------------------------------------------------------------- #
def _make_node_runner(node: Any, deps: CompilerDeps, topo: dict[str, Any]):
    ntype = _norm_type(node.type)
    node_id = node.id
    out_ports = topo["routing_nodes"].get(node_id, [])
    in_links = topo["in_links"]

    def run(state: GraphState) -> dict[str, Any]:
        node_outputs = state.get("node_outputs", {}) or {}
        incoming, incoming_by_port = _gather_incoming(node_id, in_links, node_outputs)
        deps._emit("node_started", node_id=node_id, type=ntype)
        try:
            if ntype.startswith("frontier/agent"):
                res = _run_agent_node(node, incoming, out_ports, state, deps)
            else:
                res = deps.execute_native(node, incoming, incoming_by_port)
                if not isinstance(res, dict):
                    res = {"value": res}
                # let non-agent routing nodes carry a route if they expose one
                if "route" not in res:
                    decision = res.get("decision") or res.get("logic_mode")
                    if isinstance(decision, str):
                        res["route"] = decision
        except Exception as exc:  # noqa: BLE001
            res = {"error": str(exc), "mode": "failed", "route": ""}
            deps._emit("node_failed", node_id=node_id, error=str(exc))

        counts = state.get("loop_counts", {}) or {}
        new_count = counts.get(node_id, 0) + 1
        artifacts = res.pop("artifacts", []) if isinstance(res, dict) else []
        rd = res if isinstance(res, dict) else {}
        message = str(rd.get("message") or rd.get("response") or "")
        # Rich completion event so callers can render this agent's actual message
        # + reasoning (the back-and-forth), not just a generic step.
        deps._emit(
            "node_completed",
            node_id=node_id,
            node_type=ntype,
            title=getattr(node, "title", node_id),
            agent_id=rd.get("agent_id") if ntype.startswith("frontier/agent") else None,
            response=str(rd.get("response") or rd.get("message") or ""),
            reasoning=str(rd.get("reasoning") or ""),
            route=str(rd.get("route", "")),
            mode=str(rd.get("mode", "")),
            patch=str(rd.get("patch") or ""),
        )
        update: dict[str, Any] = {
            "node_outputs": {node_id: res},
            "loop_counts": {node_id: new_count},
            "events": [
                {
                    "node_id": node_id,
                    "type": "node_completed",
                    "summary": (res.get("summary", "") if isinstance(res, dict) else ""),
                }
            ],
        }
        if message:
            update["message"] = message
        if artifacts:
            update["artifacts"] = artifacts
        return update

    return run


# --------------------------------------------------------------------------- #
# Compile + run
# --------------------------------------------------------------------------- #
def compile_frontier_graph(nodes: list[Any], links: list[Any], deps: CompilerDeps) -> CompiledGraph:
    if not LANGGRAPH_AVAILABLE:
        raise RuntimeError("langgraph is not installed; cannot compile the workflow graph.")
    if not nodes:
        raise ValueError("graph has no nodes")

    topo = _classify(nodes, links)
    out_links = topo["out_links"]
    routing_nodes = topo["routing_nodes"]
    entry = topo["entry"]
    terminals = topo["terminals"]
    ancestors_of = topo["ancestors"]

    builder = StateGraph(GraphState)
    for n in nodes:
        builder.add_node(n.id, _make_node_runner(n, deps, topo))

    builder.add_edge(START, entry)

    for n in nodes:
        edges = out_links[n.id]
        if not edges:
            builder.add_edge(n.id, END)
            continue
        if n.id in routing_nodes:
            port_targets: dict[str, str] = {}
            for e in edges:
                port_targets.setdefault(_port(e), e.to_node)
            router = _router_for(n.id, port_targets, deps, ancestors_of)
            builder.add_conditional_edges(n.id, router, port_targets)
        else:
            # single port, one-or-many targets => linear or parallel fan-out
            for e in edges:
                builder.add_edge(n.id, e.to_node)

    app = builder.compile()
    return CompiledGraph(
        app=app,
        entry=entry,
        terminals=terminals,
        has_cycle=topo["has_cycle"],
        agent_node_ids=[n.id for n in nodes if _norm_type(n.type).startswith("frontier/agent")],
        routing_nodes=routing_nodes,
        node_count=len(nodes),
    )


def run_compiled_graph(
    compiled: CompiledGraph, run_input: dict[str, Any], deps: CompilerDeps
) -> dict[str, Any]:
    """Provision the workspace (once, if any code/team node needs it), invoke the
    compiled graph with a recursion backstop, then clean up."""
    needs_workspace = bool(compiled.agent_node_ids) and deps.workspace is not None
    cleanup = None
    if needs_workspace:
        try:
            from frontier_runtime.harness.workspace_binding import (
                WorkspaceBinding,
                WorkspaceManager,
            )

            binding = WorkspaceBinding.from_payload(deps.workspace or {})
            prov = WorkspaceManager().provision(binding, run_id=deps.run_id.replace("/", "-"))
            deps.provisioned = prov
            cleanup = prov.cleanup
            deps._emit("workspace_provisioned", root=str(prov.root), branch=prov.branch)
        except Exception as exc:  # noqa: BLE001 - degrade to plan-only code nodes
            deps._emit("workspace_error", error=str(exc))
            deps.provisioned = None

    recursion_limit = deps.max_loop_iterations * max(compiled.node_count, 1) + 25
    initial: GraphState = {
        "run_input": run_input,
        "message": str(run_input.get("message") or run_input.get("spec") or ""),
        "node_outputs": {},
        "loop_counts": {},
        "events": [],
        "artifacts": [],
    }
    changed_files: list[dict[str, Any]] = []
    try:
        final_state = compiled.app.invoke(initial, config={"recursion_limit": recursion_limit})
    finally:
        # Capture the union of every agent's edits from the worktree BEFORE cleanup.
        prov = deps.provisioned
        if prov is not None:
            try:
                changed_files = prov.workspace.changed_files()
            except Exception:  # noqa: BLE001
                changed_files = []
        if cleanup is not None:
            try:
                cleanup()
            except Exception:  # noqa: BLE001
                pass
            deps.provisioned = None

    node_outputs = final_state.get("node_outputs", {})
    return {
        "node_results": node_outputs,
        "events": final_state.get("events", []),
        "artifacts": final_state.get("artifacts", []),
        "loop_counts": final_state.get("loop_counts", {}),
        "changed_files": changed_files,
        "last_output": _last_output(compiled, node_outputs),
    }


def _last_output(compiled: CompiledGraph, node_outputs: dict[str, Any]) -> Any:
    for tid in compiled.terminals:
        if tid in node_outputs:
            return node_outputs[tid]
    # otherwise the most-recently-written node result
    if node_outputs:
        return list(node_outputs.values())[-1]
    return None
