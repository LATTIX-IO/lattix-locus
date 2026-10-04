"""LOCUS-348 (D-27/D-28): the agent runtime port contract.

One parametrized suite every runtime behind ``create_runtime`` must pass:

* mediation -- every model call and every side effect had a gateway decision
  first, and the model is offered no tool outside the mediated set;
* ask -> blocked -- an unapproved gateway ask never runs and ends the run blocked;
* budget stop -- the step budget is a hard stop;
* done-criteria verification -- ``done`` only after the verify gate passed;
* denied tool -- a policy deny is an observation, the run goes on.

Model turns go through the production path (``GatedChatClient`` ->
``ModelRouter`` -> ``ModelClient`` -> ``GatewayModelGate``) against a scripted
OpenAI-compatible endpoint (httpx ``MockTransport``); tools run through
``CodingToolset`` on a real ``LocalDirectExecutor`` bound to a real
``Gateway`` session (``FakeEngine`` policies). The ``deep-agents`` cases skip
when the optional ``deepagents`` dependency is not installed.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from locus_runtime import gateway as gw
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, GatedChatClient
from locus_runtime.harness.mediation import MediationMonitor
from locus_runtime.harness.model_profiles import resolve_profile
from locus_runtime.harness.run_envelope import CommandCheck, FileCheck, RunBudget, RunEnvelope
from locus_runtime.harness.runtime_contract import (
    CONTROL_TOOLS,
    PORT_VERSION,
    AgentRuntime,
    ApprovalRequest,
    RuntimeRequest,
    RuntimeResult,
)
from locus_runtime.harness.runtimes import (
    DEEP_AGENTS,
    VERIFIED_LOOP,
    create_runtime,
    runtime_available,
)
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from locus_runtime.model_client import (
    GatewayModelGate,
    ModelClient,
    ModelEndpoint,
    ModelRouter,
    ModelTier,
)
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git, tool_response
from tests.harness.test_swe_agent_e2e import FIXED_LINE_NEW, FIXED_LINE_OLD, TEST_CMD, _make_repo

pytestmark = [requires_bash, requires_git]

PROFILE = resolve_profile("scripted", "x", profile_id="local-32b-class")
RUNTIMES = [
    pytest.param(VERIFIED_LOOP, id=VERIFIED_LOOP),
    pytest.param(
        DEEP_AGENTS,
        id=DEEP_AGENTS,
        marks=pytest.mark.skipif(
            not runtime_available(DEEP_AGENTS), reason="optional 'deepagents' not installed"
        ),
    ),
]


# --------------------------------------------------------------------------- #
# Scripted OpenAI-compatible endpoint behind the real gated model client
# --------------------------------------------------------------------------- #
class ScriptedEndpoint:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        item = self.responses.pop(0) if self.responses else ChatResponse(text="(script done)")
        if callable(item):
            item = item(body)
        message: dict[str, Any] = {"role": "assistant", "content": item.text or None}
        if item.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments
                        if isinstance(tc.arguments, str)
                        else json.dumps(tc.arguments),
                    },
                }
                for tc in item.tool_calls
            ]
        return httpx.Response(
            200,
            json={
                "id": f"cmpl-{len(self.requests)}",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "scripted"),
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if item.tool_calls else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            },
        )


def gated_client(session: gw.GatewaySession, endpoint: ScriptedEndpoint) -> GatedChatClient:
    target = ModelEndpoint(provider="ollama", model="scripted", base_url="http://127.0.0.1:9/v1")
    http = httpx.Client(transport=httpx.MockTransport(endpoint.handler))
    gate = GatewayModelGate(session=session)
    router = ModelRouter(
        [ModelTier("ollama", "scripted")],
        client_factory=lambda _tier: ModelClient(
            target, gate=gate, http_client=http, max_retries=0
        ),
    )
    return GatedChatClient(router)


class AskForCommands:
    """IntentGate double: asks (R3-style decision point) for matching commands."""

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def review(
        self, action: gw.GatewayAction, capabilities: gw.Capabilities
    ) -> tuple[gw.Outcome, tuple[str, ...]]:
        if action.kind == "process_exec" and self.needle in action.command_summary:
            return "ask", ("test.intent_gate_ask",)
        return "allow", ()


@dataclass
class Run:
    result: RuntimeResult
    monitor: MediationMonitor
    endpoint: ScriptedEndpoint
    root: Path
    connections: list[Any]


def run_task(
    runtime_name: str,
    root: Path,
    responses: list[Any],
    *,
    envelope: RunEnvelope,
    engine: FakeEngine | None = None,
    intent_gate: Any = None,
    approver: Callable[[ApprovalRequest], bool] | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> Run:
    monitor = MediationMonitor()
    gateway = gw.Gateway(engine or FakeEngine(), monitor.audit_sink, intent_gate=intent_gate)
    session = gateway.open_session(
        run_id="run-contract",
        principal="tester",
        engine="contract",
        capabilities=envelope.gateway_capabilities(),
    )
    executor = LocalDirectExecutor(root, gateway_session=session)
    monitor.attach(executor)
    # Platform git (the diff for submit) on its own allow-all session: not an agent action.
    platform = gw.Gateway(FakeEngine(), lambda _r: None).open_session(
        run_id="platform", principal="platform", engine="git", capabilities=gw.Capabilities()
    )
    workspace = Workspace(
        run_id="run-contract",
        executor=executor,
        test_command=TEST_CMD,
        git_executor=LocalDirectExecutor(root, gateway_session=platform),
    )
    toolset = CodingToolset(workspace=workspace)
    endpoint = ScriptedEndpoint(responses)
    client = monitor.wrap_client(gated_client(session, endpoint))

    connections: list[Any] = []
    real_connect = socket.socket.connect

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        connections.append(address)
        return real_connect(sock, address)

    runtime: AgentRuntime = create_runtime(runtime_name)
    request = RuntimeRequest(
        envelope=envelope,
        toolset=toolset,
        client=client,
        profile=PROFILE,
        system_prompt="You fix bugs.",
        user_prompt="Fix add() in mathlib/core.py.",
        run_id="run-contract",
        approver=approver,
        options={"provider_retry_backoff": 0},
    )
    with monkeypatch.context() as patch:
        patch.setattr(socket.socket, "connect", guarded_connect)
        result = runtime.run(request)
    return Run(result, monitor, endpoint, root, connections)


def plan_step(runtime_name: str, call_id: str = "plan") -> ChatResponse:
    """Each runtime's own planning tool (verified loop: update_plan; Deep Agents: write_todos)."""
    if runtime_name == DEEP_AGENTS:
        return tool_response(
            call_id, "write_todos", todos=[{"content": "fix add()", "status": "in_progress"}]
        )
    return tool_response(
        call_id,
        "update_plan",
        steps=["view the code", "fix the operator", "run the tests", "submit"],
        verification=[{"criterion_id": "tests", "method": "run the test command"}],
    )


def fix_step(call_id: str = "fix") -> ChatResponse:
    return tool_response(
        call_id,
        "str_replace_editor",
        command="str_replace",
        path="mathlib/core.py",
        old_str=FIXED_LINE_OLD,
        new_str=FIXED_LINE_NEW,
    )


def _tests_envelope(**budget: Any) -> RunEnvelope:
    return RunEnvelope(
        goal="add(2, 3) must return 5",
        done_criteria=(CommandCheck(id="tests", command=TEST_CMD, timeout_seconds=120),),
        budget=RunBudget(**{"max_steps": 12, "max_seconds": 300, **budget}),
    )


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_factory_builds_a_port_implementation(runtime_name: str) -> None:
    runtime = create_runtime(runtime_name)
    assert isinstance(runtime, AgentRuntime)
    assert runtime.name == runtime_name
    assert runtime.port_version == PORT_VERSION


@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_every_model_call_and_side_effect_is_mediated(
    runtime_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    run = run_task(
        runtime_name,
        tmp_path,
        [
            plan_step(runtime_name),
            tool_response("v", "str_replace_editor", command="view", path="mathlib/core.py"),
            # A framework built-in / unknown tool: must never execute.
            tool_response("w", "write_file", file_path="pwned.txt", content="x"),
            tool_response("b", "execute_bash", command="echo probe"),
            fix_step(),
            tool_response("t", "run_tests"),
            tool_response("s", "submit", answer="fixed add"),
        ],
        envelope=_tests_envelope(),
        monkeypatch=monkeypatch,
    )
    result, report = run.result, run.monitor.report()

    assert result.end_state == "done" and result.verified
    # Model channel: every HTTP request to the engine had a model_call decision.
    assert len(run.endpoint.requests) == result.usage["model_calls"]
    assert report.model_calls_observed == len(run.endpoint.requests)
    assert report.model_call_decisions == len(run.endpoint.requests)
    # Side-effect channel: every spawn/read/write was allowed by the gateway first.
    assert report.side_effects_observed > 0
    assert report.unmediated == []
    assert report.complete
    # No tool outside the envelope tools + control tools was ever offered.
    offered = set(result.offered_tools)
    assert offered, "the runtime must report the tools it offered"
    assert offered <= set(_tests_envelope().capabilities.tools) | CONTROL_TOOLS
    for request in run.endpoint.requests:
        names = {t["function"]["name"] for t in request.get("tools") or []}
        assert names <= set(_tests_envelope().capabilities.tools) | CONTROL_TOOLS
    # The unknown tool did nothing; the runtime opened no network connection.
    assert not (tmp_path / "pwned.txt").exists()
    assert run.connections == []


@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_unapproved_gateway_ask_never_runs_and_ends_blocked(
    runtime_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    run = run_task(
        runtime_name,
        tmp_path,
        [
            plan_step(runtime_name),
            tool_response("d", "execute_bash", command="echo shipped > deployed.txt # deploy"),
            fix_step(),
            tool_response("s", "submit", answer="done"),
        ],
        envelope=_tests_envelope(),
        intent_gate=AskForCommands("deploy"),
        monkeypatch=monkeypatch,
    )
    result = run.result

    assert result.end_state == "blocked"
    assert result.blocker is not None and result.blocker["kind"] == "gateway"
    assert "approve gateway request" in result.blocker["unblock"]
    assert [a.source for a in result.interrupts] == ["gateway"]
    assert result.interrupts[0].fingerprint
    assert not (tmp_path / "deployed.txt").exists()
    assert run.monitor.report().outcomes.get("ask") == 1
    # Nothing after the ask ran.
    assert "a + b" not in (tmp_path / "mathlib" / "core.py").read_text()


@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_step_budget_is_a_hard_stop(
    runtime_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    view = tool_response("v", "str_replace_editor", command="view", path="mathlib/core.py")
    run = run_task(
        runtime_name,
        tmp_path,
        [plan_step(runtime_name), *[view] * 10],
        envelope=_tests_envelope(max_steps=3),
        monkeypatch=monkeypatch,
    )
    result = run.result

    assert result.end_state == "stopped"
    assert result.stop == {
        "kind": "budget",
        "detail": "step budget exhausted",
        "dimension": "steps",
    }
    assert result.usage["steps"] == 3
    assert len(run.endpoint.requests) == 3


@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_done_only_after_the_verify_gate_passes(
    runtime_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    run = run_task(
        runtime_name,
        tmp_path,
        [
            plan_step(runtime_name),
            tool_response("s1", "submit", answer="premature"),
            fix_step(),
            tool_response("s2", "submit", answer="fixed add"),
        ],
        envelope=_tests_envelope(),
        monkeypatch=monkeypatch,
    )
    result = run.result

    assert result.end_state == "done" and result.verified
    assert result.verification_attempts == 2
    assert result.run is not None
    first, second = result.run.verification
    assert first["passed"] is False and first["results"][0]["evidence"]["exit_code"] != 0
    assert second["passed"] is True
    assert result.evidence is not None and result.evidence["commands"][0]["exit_code"] == 0
    rejected = [
        str(m.get("content"))
        for m in result.run.messages
        if str(m.get("content") or "").startswith("Submit rejected: verification attempt 1")
    ]
    assert rejected, "the failed verification must come back to the agent"


@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_policy_deny_is_an_observation_and_the_run_continues(
    runtime_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    envelope = RunEnvelope(
        goal="add(2, 3) must return 5",
        done_criteria=(FileCheck(id="fixed", path="mathlib/core.py", contains=FIXED_LINE_NEW),),
        budget=RunBudget(max_steps=10, max_seconds=300),
    )
    run = run_task(
        runtime_name,
        tmp_path,
        [
            plan_step(runtime_name),
            tool_response("b", "execute_bash", command="echo denied > denied.txt"),
            fix_step(),
            tool_response("s", "submit", answer="fixed"),
        ],
        envelope=envelope,
        engine=FakeEngine(allow={"tool_jail": False}),
        monkeypatch=monkeypatch,
    )
    result = run.result

    assert result.end_state == "done" and result.verified
    assert result.telemetry["gateway_denied"] >= 1
    assert not (tmp_path / "denied.txt").exists()
    assert result.run is not None
    assert any("[denied by policy]" in str(m.get("content")) for m in result.run.messages)
    assert run.monitor.report().unmediated == []


# --------------------------------------------------------------------------- #
# Deep Agents specifics (HITL resume)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not runtime_available(DEEP_AGENTS), reason="optional 'deepagents' not installed"
)
def test_deep_agents_approved_ask_resumes_and_runs_the_exact_action_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    approvals: list[ApprovalRequest] = []

    def approver(request: ApprovalRequest) -> bool:
        approvals.append(request)
        return True

    envelope = RunEnvelope(
        goal="ship it",
        done_criteria=(FileCheck(id="shipped", path="deployed.txt", contains="shipped"),),
        budget=RunBudget(max_steps=8, max_seconds=300),
    )
    run = run_task(
        DEEP_AGENTS,
        tmp_path,
        [
            plan_step(DEEP_AGENTS),
            tool_response("d", "execute_bash", command="echo shipped >> deployed.txt # deploy"),
            tool_response("s", "submit", answer="shipped"),
        ],
        envelope=envelope,
        intent_gate=AskForCommands("deploy"),
        approver=approver,
        monkeypatch=monkeypatch,
    )

    assert run.result.end_state == "done"
    assert len(approvals) == 1 and approvals[0].source == "gateway"
    assert (tmp_path / "deployed.txt").read_text().count("shipped") == 1
    outcomes = run.monitor.report().outcomes
    assert outcomes.get("ask") == 1


def test_mediation_monitor_flags_a_side_effect_without_a_decision(tmp_path: Path) -> None:
    """The measuring instrument itself: a sink call with no allow first is counted."""
    monitor = MediationMonitor()
    gateway = gw.Gateway(FakeEngine(), monitor.audit_sink)
    session = gateway.open_session(
        run_id="r", principal="p", engine="e", capabilities=gw.Capabilities()
    )
    executor = LocalDirectExecutor(tmp_path, gateway_session=session)
    monitor.attach(executor)

    executor.write_file("gated.txt", "ok")  # decision, then sink
    executor._write_bytes(tmp_path / "bypass.txt", b"x")  # noqa: SLF001 - simulated bypass

    report = monitor.report()
    assert report.side_effects_observed == 2
    assert report.side_effects_mediated == 1
    assert report.unmediated == ["_write_bytes without a preceding file_write allow"]
    assert not report.complete
