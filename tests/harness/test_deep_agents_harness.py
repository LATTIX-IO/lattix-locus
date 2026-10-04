"""LOCUS-361: Deep Agents as the base harness, extended and hardened.

* pure parts (compaction, LangSmith scrub, version audit) run everywhere;
* the vendor-SDK isolation test imports the backend in a clean interpreter;
* the runtime parts (built-in file tools neutralized, text-turn nudge and
  compaction as middleware, no egress except the gated model client, durable
  resume with no duplicated side effects, an approved action exactly once
  across a restart) skip when the Deep Agents stack is not installed.

Model turns go through the production path (``GatedChatClient`` ->
``ModelRouter`` -> ``ModelClient`` -> ``GatewayModelGate``); tools run through
``CodingToolset`` on a real ``LocalDirectExecutor`` bound to a real ``Gateway``
session. A "crash" is a ``BaseException`` raised at a chosen point, which
unwinds the whole runtime like a killed process; "after the restart" builds a
fresh gateway, executor, toolset and client over the same workspace and run DB.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from locus_runtime import gateway as gw
from locus_runtime import hosted_tracing
from locus_runtime.harness.deep_agents import compaction as comp
from locus_runtime.harness.deep_agents import library
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, GatedChatClient
from locus_runtime.harness.mediation import MediationMonitor
from locus_runtime.harness.run_envelope import FileCheck, RunBudget, RunEnvelope
from locus_runtime.harness.run_store import RunStore
from locus_runtime.harness.runtime_contract import (
    CONTROL_TOOLS,
    ApprovalRequest,
    RuntimeRequest,
    RuntimeResult,
)
from locus_runtime.harness.runtime_controller import NOT_REEXECUTED
from locus_runtime.harness.runtimes import (
    DEEP_AGENTS,
    RuntimeUnavailable,
    create_runtime,
    runtime_available,
)
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from locus_runtime.model_client import GatewayModelGate, ModelClient, ModelEndpoint, ModelRouter
from locus_runtime.model_client import ModelTier
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git, tc, tool_response
from tests.harness.test_runtime_contract import (
    PROFILE,
    AskForCommands,
    ScriptedEndpoint,
    _tests_envelope,
    fix_step,
    gated_client,
    plan_step,
)
from tests.harness.test_swe_agent_e2e import FIXED_LINE_NEW, TEST_CMD, _make_repo

REPO_ROOT = Path(__file__).resolve().parents[2]
needs_deep_agents = pytest.mark.skipif(
    not runtime_available(DEEP_AGENTS), reason="Deep Agents stack not installed"
)
#: Built-in Deep Agents tools that must never reach the model.
BUILTIN_FS_TOOLS = {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}


# --------------------------------------------------------------------------- #
# Pure parts
# --------------------------------------------------------------------------- #
def test_compaction_caps_old_tool_output_and_keeps_recent() -> None:
    policy = comp.CompactionPolicy(keep_recent=1, max_chars=500, old_chars=200)
    big = "x" * 1_000
    messages = [("system", "s"), ("user", "u"), ("tool", big), ("ai", "a"), ("tool", big)]
    out = comp.compact(messages, policy)
    assert set(out) == {2, 4}
    assert len(out[2]) < 400 and "characters of this tool output omitted" in out[2]
    assert len(out[4]) < 700  # the newest is only hard-capped
    assert out[2].startswith("x") and out[2].endswith("x")
    assert comp.saved_chars(messages, out) > 1_000


def test_compaction_tightens_under_context_pressure_and_leaves_small_output() -> None:
    policy = comp.CompactionPolicy(
        keep_recent=0, old_chars=1_000, context_chars=2_000, pressure_chars=100
    )
    assert comp.compact([("tool", "short")], policy) == {}
    out = comp.compact([("tool", "y" * 1_500), ("tool", "z" * 1_500)], policy)
    assert all(len(text) < 250 for text in out.values())
    with pytest.raises(ValueError):
        comp.CompactionPolicy(old_chars=10)


def test_force_langsmith_off_scrubs_and_pins_switches() -> None:
    env = {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_API_KEY": "lsv2-not-a-real-key",
        "langsmith_endpoint": "https://example.invalid",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "x",
        "LANGCHAIN_PROJECT": "p",
        "OPENAI_API_KEY": "kept",
    }
    assert hosted_tracing.tracing_env_active(env)
    removed = hosted_tracing.force_langsmith_off(env)
    assert "LANGSMITH_API_KEY" in removed and "langsmith_endpoint" in removed
    assert env == {
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "OPENAI_API_KEY": "kept",
    }
    assert not hosted_tracing.tracing_env_active(env)
    assert hosted_tracing.force_langsmith_off(env) == ()  # idempotent


def test_unaudited_stack_is_refused() -> None:
    library.check_audited(dict(library.AUDITED_VERSIONS))
    drifted = {**library.AUDITED_VERSIONS, "deepagents": "0.8.0"}
    with pytest.raises(library.UnauditedVersion, match="deepagents 0.8.0"):
        library.check_audited(drifted)
    missing = {k: v for k, v in library.AUDITED_VERSIONS.items() if k != "langgraph"}
    with pytest.raises(ImportError, match="langgraph not installed"):
        library.check_audited(missing)


def test_desktop_entry_point_forces_langsmith_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from locus_tooling import desktop_main

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-not-a-real-key")
    monkeypatch.setattr(sys, "argv", ["locus-backend", "--self-check"])
    monkeypatch.setattr(desktop_main, "self_check", lambda: 0)
    assert desktop_main.main() == 0
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert "LANGSMITH_API_KEY" not in os.environ


_PROBE = """
import json, os, sys
sys.path[:0] = [{backend!r}, {root!r}]
import app.main  # the backend entry point
from locus_runtime.harness.runtimes import create_runtime
create_runtime({runtime!r})
vendor = ("anthropic", "langchain_anthropic", "langchain_google_genai", "deepagents",
          "google.genai", "google.auth")
print(json.dumps({{
    "vendor": sorted(m for m in sys.modules if m in vendor or m.startswith(
        tuple(v + "." for v in vendor))),
    "env": {{k: v for k, v in os.environ.items() if k.upper().startswith(
        ("LANGSMITH_", "LANGCHAIN_"))}},
}}))
"""


def _probe(runtime: str) -> dict[str, Any]:
    env = {
        k: v for k, v in os.environ.items() if not k.upper().startswith(("LANGSMITH", "LANGCHAIN"))
    }
    env.update({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "lsv2-not-a-real-key"})
    code = _PROBE.format(
        backend=str(REPO_ROOT / "apps" / "backend"), root=str(REPO_ROOT), runtime=runtime
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    result: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return result


def test_backend_without_the_runtime_imports_no_vendor_sdk_and_tracing_is_off() -> None:
    probe = _probe("verified-loop")
    assert probe["vendor"] == []
    # The backend entry point scrubbed LangSmith before anything else ran.
    assert probe["env"] == {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}


@needs_deep_agents
def test_vendor_sdks_load_only_with_the_deep_agents_runtime() -> None:
    """Positive control for the isolation test: the probe does see them."""
    probe = _probe(DEEP_AGENTS)
    assert "deepagents" in probe["vendor"] and "anthropic" in probe["vendor"]
    assert probe["env"] == {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}


# --------------------------------------------------------------------------- #
# Runtime harness
# --------------------------------------------------------------------------- #
class SimulatedCrash(BaseException):
    """Unwinds the runtime like a killed process (not an Exception: nothing catches it)."""


@dataclass
class Live:
    """One "process": the live objects a run is built from."""

    result: RuntimeResult | None
    monitor: MediationMonitor
    endpoint: ScriptedEndpoint
    executor: LocalDirectExecutor


def run_deep_agents(
    root: Path,
    responses: list[Any],
    *,
    envelope: RunEnvelope,
    db: Path | None = None,
    intent_gate: Any = None,
    approver: Callable[[ApprovalRequest], bool] | None = None,
    should_stop: Callable[[], bool] | None = None,
    patch_executor: Callable[[LocalDirectExecutor], None] | None = None,
    client_factory: Callable[[gw.GatewaySession, ScriptedEndpoint], Any] = gated_client,
    options: dict[str, Any] | None = None,
) -> Live:
    monitor = MediationMonitor()
    gateway = gw.Gateway(FakeEngine(), monitor.audit_sink, intent_gate=intent_gate)
    session = gateway.open_session(
        run_id="run-361",
        principal="tester",
        engine="contract",
        capabilities=envelope.gateway_capabilities(),
    )
    executor = LocalDirectExecutor(root, gateway_session=session)
    monitor.attach(executor)
    if patch_executor is not None:
        patch_executor(executor)
    platform = gw.Gateway(FakeEngine(), lambda _r: None).open_session(
        run_id="platform", principal="platform", engine="git", capabilities=gw.Capabilities()
    )
    workspace = Workspace(
        run_id="run-361",
        executor=executor,
        test_command=TEST_CMD,
        git_executor=LocalDirectExecutor(root, gateway_session=platform),
    )
    endpoint = ScriptedEndpoint(responses)
    live = Live(None, monitor, endpoint, executor)
    request = RuntimeRequest(
        envelope=envelope,
        toolset=CodingToolset(workspace=workspace),
        client=monitor.wrap_client(client_factory(session, endpoint)),
        profile=PROFILE,
        system_prompt="You fix bugs.",
        user_prompt="Fix add() in mathlib/core.py.",
        run_id="run-361",
        approver=approver,
        should_stop=should_stop,
        checkpoint_path=db,
        options={"provider_retry_backoff": 0, **(options or {})},
    )
    live.result = create_runtime(DEEP_AGENTS).run(request)
    return live


def _offered(endpoint: ScriptedEndpoint) -> set[str]:
    return {t["function"]["name"] for r in endpoint.requests for t in r.get("tools") or []}


@needs_deep_agents
@requires_bash
@requires_git
def test_builtin_file_tools_are_never_offered_and_refused_on_call(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    live = run_deep_agents(
        tmp_path,
        [
            plan_step(DEEP_AGENTS),
            tool_response("r", "read_file", file_path="/mathlib/core.py"),
            tool_response("l", "ls", path="/"),
            tool_response("e", "execute", command="echo pwned > pwned.txt"),
            fix_step(),
            tool_response("s", "submit", answer="fixed"),
        ],
        envelope=_tests_envelope(),
    )
    result = live.result
    assert result is not None and result.end_state == "done"
    offered = _offered(live.endpoint) | set(result.offered_tools)
    assert not offered & BUILTIN_FS_TOOLS
    assert offered <= set(_tests_envelope().capabilities.tools) | CONTROL_TOOLS
    assert result.run is not None
    refusals = [
        str(m.get("content"))
        for m in result.run.messages
        if str(m.get("content") or "").startswith("[not executed]")
    ]
    assert len(refusals) == 3
    assert not (tmp_path / "pwned.txt").exists()
    assert live.monitor.report().complete


@needs_deep_agents
@requires_bash
@requires_git
def test_text_turn_is_nudged_inside_the_graph_and_old_output_compacted(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    live = run_deep_agents(
        tmp_path,
        [
            plan_step(DEEP_AGENTS),
            tool_response("b", "execute_bash", command="python3 -c \"print('Q' * 3000)\""),
            ChatResponse(text="I think I am done."),  # no tool call
            tool_response("v1", "str_replace_editor", command="view", path="mathlib/core.py"),
            tool_response("v2", "str_replace_editor", command="view", path="mathlib/core.py"),
            fix_step(),
            tool_response("s", "submit", answer="fixed"),
        ],
        envelope=_tests_envelope(),
        options={"compaction": comp.CompactionPolicy(keep_recent=1, old_chars=300)},
    )
    result = live.result
    assert result is not None and result.end_state == "done" and result.verified
    # The nudge after the text turn reached the very next model request.
    after_text = live.endpoint.requests[3]["messages"]
    assert after_text[-1]["role"] == "user"
    assert "call `submit`" in after_text[-1]["content"]

    # The 3,000-char output was whole while recent, compacted once it was old.
    def bash_output(request: dict[str, Any]) -> str:
        outs = [m for m in request["messages"] if m["role"] == "tool" and "QQQ" in m["content"]]
        return str(outs[0]["content"]) if outs else ""

    assert len(bash_output(live.endpoint.requests[2])) >= 3000
    assert len(bash_output(live.endpoint.requests[-1])) < 600
    assert result.telemetry["context_compactions"] >= 1
    # The trajectory (the run record) keeps the full output.
    assert result.run is not None and result.run.trajectory is not None
    assert any("Q" * 3000 in str(m.get("content")) for m in result.run.trajectory.messages())


# --------------------------------------------------------------------------- #
# No outbound network except through the gated model client
# --------------------------------------------------------------------------- #
@pytest.fixture()
def http_endpoint() -> Iterator[tuple[str, list[ScriptedEndpoint]]]:
    """A real OpenAI-compatible endpoint on loopback, serving a ScriptedEndpoint."""
    holder: list[ScriptedEndpoint] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            response = holder[0].handler(httpx.Request("POST", "http://x/", content=body))
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", holder
    finally:
        server.shutdown()
        server.server_close()


@needs_deep_agents
@requires_bash
@requires_git
def test_no_outbound_network_except_the_gated_model_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_endpoint: tuple[str, list[ScriptedEndpoint]],
) -> None:
    base_url, holder = http_endpoint
    port = int(base_url.rsplit(":", 1)[1].split("/")[0])
    _make_repo(tmp_path)
    # Hosted tracing switched on in the environment, pointing at a loopback port:
    # if anything honoured it, the guard below would see the connection.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-not-a-real-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "*")

    connections: list[tuple[Any, bool]] = []
    lookups: list[Any] = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        stack = "".join(traceback.format_stack())
        from_model_client = f"locus_runtime{os.sep}model_client.py" in stack
        connections.append((address, from_model_client))
        if not (from_model_client and address[1] == port):
            raise ConnectionRefusedError(f"egress blocked by test guard: {address}")
        return real_connect(sock, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        lookups.append(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def real_http_client(session: gw.GatewaySession, endpoint: ScriptedEndpoint) -> Any:
        holder.append(endpoint)
        target = ModelEndpoint(provider="ollama", model="scripted", base_url=base_url)
        gate = GatewayModelGate(session=session)
        router = ModelRouter(
            [ModelTier("ollama", "scripted")],
            client_factory=lambda _tier: ModelClient(target, gate=gate, max_retries=0),
        )
        return GatedChatClient(router)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    live = run_deep_agents(
        tmp_path,
        [
            plan_step(DEEP_AGENTS),
            tool_response("v", "str_replace_editor", command="view", path="mathlib/core.py"),
            fix_step(),
            tool_response("s", "submit", answer="fixed"),
        ],
        envelope=_tests_envelope(),
        client_factory=real_http_client,
    )
    monkeypatch.undo()

    result = live.result
    assert result is not None and result.end_state == "done", result
    assert connections, "the model calls must have used the network"
    assert all(ok and addr[1] == port for addr, ok in connections), connections
    assert set(lookups) <= {"127.0.0.1", "localhost"}, lookups
    report = live.monitor.report()
    assert report.model_calls_observed == report.model_call_decisions == len(live.endpoint.requests)
    assert report.complete


# --------------------------------------------------------------------------- #
# Durable runs: kill, resume, no duplicated side effects
# --------------------------------------------------------------------------- #
APPEND = "echo once >> side.txt"


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


@needs_deep_agents
@requires_bash
@requires_git
def test_crash_between_steps_resumes_same_run_without_repeating_effects(tmp_path: Path) -> None:
    repo, db = tmp_path / "repo", tmp_path / "runs.db"
    repo.mkdir()
    _make_repo(repo)

    def crash(_body: dict[str, Any]) -> ChatResponse:
        raise SimulatedCrash("killed before model call 3")

    with pytest.raises(SimulatedCrash):
        run_deep_agents(
            repo,
            [plan_step(DEEP_AGENTS), tool_response("b", "execute_bash", command=APPEND), crash],
            envelope=_tests_envelope(),
            db=db,
        )
    assert _lines(repo / "side.txt") == ["once"]

    live = run_deep_agents(  # "after the restart"
        repo,
        [fix_step(), tool_response("s", "submit", answer="fixed")],
        envelope=_tests_envelope(),
        db=db,
    )
    result = live.result
    assert result is not None and result.end_state == "done" and result.verified
    assert result.run_id == "run-361"
    assert _lines(repo / "side.txt") == ["once"]
    assert len(live.endpoint.requests) == 2  # continued at the model call that died
    assert result.usage["model_calls"] >= 4  # usage carried over the restart
    statuses = [(a.tool, a.status) for a in _ledger(db)]
    assert ("execute_bash", "done") in statuses and ("str_replace_editor", "done") in statuses
    # A finished run resumed again is returned as is, with no model call.
    again = run_deep_agents(repo, [], envelope=_tests_envelope(), db=db)
    assert again.result is not None and again.result.end_state == "done"
    assert again.endpoint.requests == []


def _ledger(db: Path) -> list[Any]:
    store = RunStore(db)
    try:
        return store.actions("run-361")
    finally:
        store.close()


@needs_deep_agents
@requires_bash
@requires_git
def test_crash_during_an_action_never_reruns_it(tmp_path: Path) -> None:
    repo, db = tmp_path / "repo", tmp_path / "runs.db"
    repo.mkdir()
    _make_repo(repo)

    def crash_after_append(executor: LocalDirectExecutor) -> None:
        original = executor._spawn  # noqa: SLF001 - simulated kill inside the action

        def spawn(*args: Any, **kwargs: Any) -> Any:
            out = original(*args, **kwargs)
            if (repo / "side.txt").exists():
                raise SimulatedCrash("killed right after the side effect")
            return out

        executor._spawn = spawn  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(SimulatedCrash):
        run_deep_agents(
            repo,
            [plan_step(DEEP_AGENTS), tool_response("b", "execute_bash", command=APPEND)],
            envelope=_tests_envelope(),
            db=db,
            patch_executor=crash_after_append,
        )
    assert _lines(repo / "side.txt") == ["once"]
    assert [a.status for a in _ledger(db) if a.tool == "execute_bash"] == ["started"]

    live = run_deep_agents(
        repo,
        [fix_step(), tool_response("s", "submit", answer="fixed")],
        envelope=_tests_envelope(),
        db=db,
    )
    result = live.result
    assert result is not None and result.end_state == "done"
    assert _lines(repo / "side.txt") == ["once"]  # not run again
    assert result.run is not None
    assert any(
        NOT_REEXECUTED in str(m.get("content")) for m in live.endpoint.requests[0]["messages"]
    )
    assert [a.status for a in _ledger(db) if a.tool == "execute_bash"] == ["interrupted"]


@needs_deep_agents
@requires_bash
@requires_git
def test_approved_action_runs_exactly_once_across_a_restart(tmp_path: Path) -> None:
    repo, db = tmp_path / "repo", tmp_path / "runs.db"
    repo.mkdir()
    _make_repo(repo)
    envelope = RunEnvelope(
        goal="ship it",
        done_criteria=(FileCheck(id="shipped", path="deployed.txt", contains="shipped"),),
        budget=RunBudget(max_steps=8, max_seconds=300),
    )
    deploy = "echo shipped >> deployed.txt # deploy"
    approvals: list[ApprovalRequest] = []

    def approve(request: ApprovalRequest) -> bool:
        approvals.append(request)
        return True

    def stop_after_approval() -> bool:
        if approvals:
            raise SimulatedCrash("killed after the approval, before the action ran")
        return False

    with pytest.raises(SimulatedCrash):
        run_deep_agents(
            repo,
            [plan_step(DEEP_AGENTS), tool_response("d", "execute_bash", command=deploy)],
            envelope=envelope,
            db=db,
            intent_gate=AskForCommands("deploy"),
            approver=approve,
            should_stop=stop_after_approval,
        )
    assert len(approvals) == 1
    assert not (repo / "deployed.txt").exists()

    # After the restart the pending decision is presented again (an approval is
    # never carried across a restart), then the action runs once.
    second: list[ApprovalRequest] = []
    live = run_deep_agents(
        repo,
        [tool_response("s", "submit", answer="shipped")],
        envelope=envelope,
        db=db,
        intent_gate=AskForCommands("deploy"),
        approver=lambda r: second.append(r) is None,
    )
    result = live.result
    assert result is not None and result.end_state == "done", result
    assert len(second) == 1 and second[0].fingerprint == approvals[0].fingerprint
    assert _lines(repo / "deployed.txt") == ["shipped"]
    assert live.monitor.report().outcomes.get("ask", 0) == 0  # approved: no new ask
    assert [a.status for a in _ledger(db) if a.tool == "execute_bash"] == ["done"]


@needs_deep_agents
@requires_bash
@requires_git
def test_parallel_asks_in_one_turn_are_each_approved_and_run_once(tmp_path: Path) -> None:
    """Two gated calls in one model turn interrupt separately; one resume answers both."""
    _make_repo(tmp_path)
    envelope = RunEnvelope(
        goal="ship both",
        done_criteria=(
            FileCheck(id="a", path="a.txt", contains="a"),
            FileCheck(id="b", path="b.txt", contains="b"),
        ),
        budget=RunBudget(max_steps=8, max_seconds=300),
    )
    both = ChatResponse(
        text="",
        tool_calls=[
            tc("pa", "execute_bash", command="echo a >> a.txt # deploy"),
            tc("pb", "execute_bash", command="echo b >> b.txt # deploy"),
        ],
    )
    approvals: list[ApprovalRequest] = []
    live = run_deep_agents(
        tmp_path,
        [plan_step(DEEP_AGENTS), both, tool_response("s", "submit", answer="shipped")],
        envelope=envelope,
        intent_gate=AskForCommands("deploy"),
        approver=lambda r: approvals.append(r) is None,
    )
    result = live.result
    assert result is not None and result.end_state == "done", result
    assert len(approvals) == 2 and len({a.fingerprint for a in approvals}) == 2
    assert _lines(tmp_path / "a.txt") == ["a"] and _lines(tmp_path / "b.txt") == ["b"]


@needs_deep_agents
@requires_bash
@requires_git
def test_resume_with_a_different_envelope_is_blocked(tmp_path: Path) -> None:
    repo, db = tmp_path / "repo", tmp_path / "runs.db"
    repo.mkdir()
    _make_repo(repo)

    def crash(_body: dict[str, Any]) -> ChatResponse:
        raise SimulatedCrash("killed")

    with pytest.raises(SimulatedCrash):
        run_deep_agents(repo, [plan_step(DEEP_AGENTS), crash], envelope=_tests_envelope(), db=db)
    live = run_deep_agents(repo, [], envelope=_tests_envelope(max_steps=50), db=db)
    assert live.result is not None and live.result.end_state == "blocked"
    assert live.result.blocker is not None
    assert "different envelope" in live.result.blocker["detail"]
    assert FIXED_LINE_NEW not in (repo / "mathlib" / "core.py").read_text()


def test_unavailable_stack_is_a_runtime_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        library, "installed_versions", lambda: {**library.AUDITED_VERSIONS, "langgraph": "9.9.9"}
    )
    with pytest.raises(RuntimeUnavailable, match="unaudited"):
        create_runtime(DEEP_AGENTS)
