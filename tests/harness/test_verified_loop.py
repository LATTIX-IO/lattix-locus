"""LOCUS-337: the verified run loop -- plan, verify gate, end states, budgets, resume.

Scripted ChatClient + real workspace (git repo, real test command) behind the
allow-all gateway double, except where a test opens a real Gateway (FakeEngine
for budget_policy, the real Rego through OPA for the tool_jail deny case).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, ScriptedChatClient
from locus_runtime.harness.loop import LoopBudgets, LoopOutcome
from locus_runtime.harness.model_profiles import resolve_profile
from locus_runtime.harness.run_envelope import (
    AcceptanceCriterion,
    CommandCheck,
    FileCheck,
    RunBudget,
    RunEnvelope,
)
from locus_runtime.harness.swe_agent import SweAgent, SweTask
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.trajectory import TrajectoryRecorder
from locus_runtime.harness.verified_loop import (
    EndState,
    ModelPricing,
    VerifiedLoop,
    read_checkpoint,
)
from locus_runtime.harness.workspace import Workspace
from locus_runtime.policy_engine import OpaSidecarEngine, find_opa_binary
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git, tool_response
from tests.harness.test_swe_agent_e2e import (
    FIXED_LINE_NEW,
    FIXED_LINE_OLD,
    TEST_CMD,
    _make_repo,
)

PROFILE = resolve_profile("scripted", "x", profile_id="local-32b-class")
pytestmark = [requires_bash, requires_git]


def _plan(call_id: str = "p1") -> ChatResponse:
    return tool_response(
        call_id,
        "update_plan",
        steps=["find the bug in add()", "fix the operator", "run the tests", "submit"],
        verification=[{"criterion_id": "tests", "method": "run the test command"}],
    )


def _fix(call_id: str = "fix") -> ChatResponse:
    return tool_response(
        call_id,
        "str_replace_editor",
        command="str_replace",
        path="mathlib/core.py",
        old_str=FIXED_LINE_OLD,
        new_str=FIXED_LINE_NEW,
    )


def _submit(call_id: str = "s", answer: str = "fixed add") -> ChatResponse:
    return tool_response(call_id, "submit", answer=answer)


def _envelope(*extra: Any, **budget: Any) -> RunEnvelope:
    return RunEnvelope(
        goal="add(2, 3) must return 5",
        done_criteria=(
            CommandCheck(id="tests", command=TEST_CMD, timeout_seconds=120),
            *extra,
        ),
        budget=RunBudget(**{"max_steps": 12, "max_seconds": 300, **budget}),
    )


def _toolset(root: Path, *, session: gw.GatewaySession | None = None) -> CodingToolset:
    executor = LocalDirectExecutor(root, gateway_session=session)
    return CodingToolset(workspace=Workspace(run_id="run-1", executor=executor))


def _loop(
    root: Path,
    responses: list[Any],
    *,
    envelope: RunEnvelope | None = None,
    toolset: CodingToolset | None = None,
    **kwargs: Any,
) -> VerifiedLoop:
    return VerifiedLoop(
        client=ScriptedChatClient(responses=responses),
        toolset=toolset or _toolset(root),
        profile=PROFILE,
        envelope=envelope or _envelope(),
        system_prompt="You fix bugs.",
        user_prompt="Fix add() in mathlib/core.py.",
        run_id="run-1",
        provider_retry_backoff=0,
        **kwargs,
    )


def _tool_contents(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("content")) for m in messages if m.get("role") == "tool"]


# --------------------------------------------------------------------------- #
# Verify gate: failing tests -> revise -> pass -> done (with evidence)
# --------------------------------------------------------------------------- #
def test_failing_tests_are_rejected_then_revised_to_done_with_evidence(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    loop = _loop(tmp_path, [_plan(), _submit("s1"), _fix(), _submit("s2")])

    result = loop.run()

    assert result.end_state is EndState.DONE
    assert result.blocker is None and result.stop is None
    # Two verification attempts: the first failed on the real test command.
    assert len(result.verification) == 2
    first, second = result.verification
    assert first["passed"] is False
    assert first["results"][0]["evidence"]["exit_code"] != 0
    assert second["passed"] is True
    # The failure came back to the agent as the submit observation.
    tool_msgs = _tool_contents(result.messages)
    assert any(c.startswith("Submit rejected: verification attempt 1 failed") for c in tool_msgs)
    # Evidence: commands + exit codes + diff + plan version.
    evidence = result.evidence
    assert evidence is not None
    assert evidence["commands"] == [
        {
            "id": "tests",
            "command": TEST_CMD,
            "exit_code": 0,
            "expected_exit_code": 0,
            "duration_seconds": evidence["commands"][0]["duration_seconds"],
            "output_tail": evidence["commands"][0]["output_tail"],
        }
    ]
    assert "return a + b" in evidence["diff"]
    assert evidence["plan_version"] == 1 and evidence["verification_attempts"] == 2
    assert result.legacy_outcome is LoopOutcome.SUBMITTED
    # The plan is a recorded artifact in the trajectory.
    assert result.trajectory is not None
    plans = [r for r in result.trajectory.records if r.get("note") == "plan"]
    assert plans and plans[0]["plan"]["verification"] == {"tests": "run the test command"}
    assert result.trajectory.final_outcome()["status"] == "done"


def test_required_plan_refuses_actions_before_a_plan_and_plan_can_be_revised(
    tmp_path: Path,
) -> None:
    _make_repo(tmp_path)
    revised = tool_response(
        "p2",
        "update_plan",
        steps=["just fix it"],
        note="simpler",
        verification=[{"criterion_id": "tests", "method": "run"}],
    )
    loop = _loop(
        tmp_path,
        [
            tool_response("e0", "execute_bash", command="echo early"),
            _plan(),
            revised,
            _fix(),
            _submit(),
        ],
    )

    result = loop.run()

    assert result.end_state is EndState.DONE
    assert _tool_contents(result.messages)[0].startswith("[not executed] Record your plan first")
    assert result.usage.actions == 1  # only the fix ran; the early bash did not
    assert [p["version"] for p in result.plan_history] == [1, 2]
    assert result.plan is not None and result.plan["note"] == "simpler"


# --------------------------------------------------------------------------- #
# Acceptance judge
# --------------------------------------------------------------------------- #
def _verdict(passed: bool, reason: str) -> ChatResponse:
    return ChatResponse(
        text=json.dumps({"results": [{"id": "ac-doc", "pass": passed, "reason": reason}]})
    )


def test_judge_failure_rejects_submit_and_the_agent_revises(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    docstring = tool_response(
        "doc",
        "str_replace_editor",
        command="str_replace",
        path="mathlib/core.py",
        old_str="def add(a, b):",
        new_str='def add(a, b):\n    """Add two numbers."""',
    )
    judge = ScriptedChatClient(
        responses=[_verdict(False, "add() has no docstring"), _verdict(True, "documented")]
    )
    envelope = _envelope(AcceptanceCriterion(id="ac-doc", text="add() documents its contract"))
    loop = _loop(
        tmp_path,
        [_plan(), _fix(), _submit("s1"), docstring, _submit("s2")],
        envelope=envelope,
        judge_client=judge,
    )

    result = loop.run()

    assert result.end_state is EndState.DONE
    first = result.verification[0]
    assert [r["status"] for r in first["results"]] == ["pass", "fail"]
    assert first["results"][1]["detail"] == "add() has no docstring"
    assert "add() has no docstring" in _tool_contents(result.messages)[2]
    assert result.evidence is not None
    assert result.evidence["judge"] == [
        {
            "id": "ac-doc",
            "criterion": "add() documents its contract",
            "pass": True,
            "reason": "documented",
        }
    ]
    # The judge saw the diff and the criteria, in its own context.
    judge_prompt = judge.calls[0][1]["content"]
    assert "ac-doc: add() documents its contract" in judge_prompt
    assert "return a + b" in judge_prompt
    assert result.usage.judge_calls == 2


def test_judge_cannot_override_a_failing_command_check(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    judge = ScriptedChatClient(responses=[_verdict(True, "looks fine")] * 3)
    envelope = _envelope(AcceptanceCriterion(id="ac-doc", text="documented"), max_steps=3)
    loop = _loop(
        tmp_path, [_plan(), _submit("s1"), _submit("s2")], envelope=envelope, judge_client=judge
    )

    result = loop.run()

    assert result.end_state is EndState.STOPPED
    assert judge.calls == []  # tests failed, so nothing was judged
    statuses = [r["status"] for r in result.verification[0]["results"]]
    assert statuses == ["fail", "skipped"]
    assert result.evidence is None


def test_unparseable_judge_output_fails_closed(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    judge = ScriptedChatClient(responses=[ChatResponse(text="LGTM!")])
    envelope = _envelope(AcceptanceCriterion(id="ac-doc", text="documented"), max_steps=3)
    loop = _loop(tmp_path, [_plan(), _fix(), _submit()], envelope=envelope, judge_client=judge)

    result = loop.run()

    assert result.end_state is EndState.STOPPED  # never verified
    ac = result.verification[0]["results"][1]
    assert ac["status"] == "fail" and ac["detail"] == "judge output was not valid JSON"


# --------------------------------------------------------------------------- #
# Budgets
# --------------------------------------------------------------------------- #
def test_action_budget_exhaustion_stops_the_run(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    busy = [tool_response(f"b{i}", "execute_bash", command="echo working") for i in range(10)]
    loop = _loop(tmp_path, [_plan(), *busy], envelope=_envelope(max_actions=3))

    result = loop.run()

    assert result.end_state is EndState.STOPPED
    assert result.stop is not None
    assert (result.stop.kind, result.stop.dimension) == ("budget", "actions")
    assert result.usage.actions == 3
    assert result.legacy_outcome is LoopOutcome.BUDGET_EXHAUSTED


def test_tokens_and_cost_accumulate_from_model_usage_and_stop_at_the_limit(
    tmp_path: Path,
) -> None:
    _make_repo(tmp_path)
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}

    def with_usage(resp: ChatResponse) -> ChatResponse:
        resp.usage = dict(usage)
        return resp

    responses = [
        with_usage(_plan()),
        *[with_usage(tool_response(f"b{i}", "execute_bash", command="echo hi")) for i in range(5)],
    ]
    events: list[tuple[str, dict[str, Any]]] = []
    loop = _loop(
        tmp_path,
        responses,
        envelope=_envelope(max_tokens=3000),
        pricing=ModelPricing(prompt_per_1k_usd=0.5, completion_per_1k_usd=2.0),
        on_event=lambda kind, data: events.append((kind, data)),
    )

    result = loop.run()

    assert result.end_state is EndState.STOPPED
    assert result.stop is not None and result.stop.dimension == "tokens"
    assert result.usage.tokens == 3300  # three calls of 1100 tokens
    assert result.usage.tokens_estimated is False
    assert result.usage.cost_usd == pytest.approx(3 * (0.5 + 0.2))
    assert any(k == "budget_warning" and d["dimension"] == "tokens" for k, d in events)


def test_budget_figures_reach_the_gateway_and_budget_policy_deny_stops_the_run(
    tmp_path: Path,
) -> None:
    _make_repo(tmp_path)
    engine = FakeEngine(allow={"budget_policy": False})
    gateway = gw.Gateway(engine, lambda _record: None)
    envelope = _envelope()
    # The session starts without budget figures; the loop reports them per action.
    caps = dataclasses.replace(envelope.gateway_capabilities(), budget=None)
    session = gateway.open_session(
        run_id="run-1", principal="alice", engine="harness", capabilities=caps
    )
    loop = _loop(
        tmp_path,
        [_plan(), tool_response("b", "execute_bash", command="echo hi")],
        envelope=envelope,
        toolset=_toolset(tmp_path, session=session),
    )

    result = loop.run()

    assert result.end_state is EndState.STOPPED
    assert result.stop is not None
    assert (result.stop.kind, result.stop.dimension) == ("policy", "budget_policy")
    budget_inputs = [payload for policy, payload in engine.calls if policy == "budget_policy"]
    assert budget_inputs, "budget_policy must be evaluated on the action"
    assert budget_inputs[0]["max_tokens"] == envelope.budget.max_tokens
    assert budget_inputs[0]["tokens_used"] > 0


def test_reported_budget_can_only_tighten() -> None:
    gateway = gw.Gateway(FakeEngine(), lambda _record: None)
    session = gateway.open_session(
        run_id="r",
        principal="p",
        engine="e",
        capabilities=gw.Capabilities(budget=gw.BudgetFigures(tokens_used=0, max_tokens=100)),
    )
    merged = session.report_budget(
        gw.BudgetFigures(tokens_used=50, max_tokens=10_000, cost_used_usd=1.0, max_cost_usd=5.0)
    )
    assert merged == gw.BudgetFigures(
        tokens_used=50, max_tokens=100, cost_used_usd=1.0, max_cost_usd=5.0
    )
    lowered = session.report_budget(gw.BudgetFigures(tokens_used=1, max_tokens=100))
    assert lowered is not None
    assert (lowered.tokens_used, lowered.max_cost_usd) == (50, 5.0)


# --------------------------------------------------------------------------- #
# Blocked
# --------------------------------------------------------------------------- #
def test_gateway_deny_of_a_required_verifier_command_blocks(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    gateway = gw.Gateway(FakeEngine(allow={"tool_jail": False}), lambda _record: None)
    envelope = _envelope()
    session = gateway.open_session(
        run_id="run-1",
        principal="alice",
        engine="harness",
        capabilities=envelope.gateway_capabilities(),
    )
    loop = _loop(tmp_path, [_plan(), _submit()], toolset=_toolset(tmp_path, session=session))

    result = loop.run()

    assert result.end_state is EndState.BLOCKED
    assert result.blocker is not None and result.blocker.kind == "gateway"
    assert "tool_jail" in result.blocker.detail
    assert "sandbox" in result.blocker.unblock


def test_real_opa_tool_jail_deny_of_the_verifier_command_blocks(tmp_path: Path) -> None:
    """Real Rego: tool_jail never accepts ``local-direct``, so the required test
    command cannot run here -> blocked with the gateway reason, never done."""
    binary = find_opa_binary()
    if binary is None:
        pytest.skip("OPA binary not available (set LOCUS_OPA_BIN)")
    _make_repo(tmp_path)
    engine = OpaSidecarEngine(opa_binary=binary, timeout_seconds=5.0)
    engine.start()
    try:
        audit: list[gw.GatewayAuditRecord] = []
        gateway = gw.Gateway(engine, audit.append)
        envelope = _envelope()
        session = gateway.open_session(
            run_id="run-opa",
            principal="alice",
            engine="harness",
            capabilities=envelope.gateway_capabilities(),
        )
        loop = _loop(tmp_path, [_plan(), _submit()], toolset=_toolset(tmp_path, session=session))
        result = loop.run()
    finally:
        engine.close()

    assert result.end_state is EndState.BLOCKED
    assert result.blocker is not None and result.blocker.kind == "gateway"
    assert "tool_jail" in result.blocker.detail
    denied = [r for r in audit if r.outcome == "deny" and TEST_CMD in r.command]
    assert denied, "the verifier command must have been decided (and denied) by the gateway"
    assert result.evidence is None


def test_agent_flagging_ambiguous_criteria_blocks(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    flag = tool_response(
        "b",
        "report_blocker",
        kind="ambiguous_criteria",
        blocker="'fast enough' has no threshold",
        unblock="state a latency threshold in the done criteria",
    )
    envelope = _envelope(AcceptanceCriterion(id="ac-fast", text="add is fast enough"))
    loop = _loop(tmp_path, [_plan(), flag, _submit()], envelope=envelope)

    result = loop.run()

    assert result.end_state is EndState.BLOCKED
    assert result.blocker is not None
    assert result.blocker.kind == "ambiguous_criteria"
    assert result.blocker.unblock == "state a latency threshold in the done criteria"
    assert result.verification == []


def test_unavailable_provider_blocks_honestly(tmp_path: Path) -> None:
    _make_repo(tmp_path)

    def down(_messages: list[dict[str, Any]]) -> ChatResponse:
        raise ConnectionError("connection refused")

    loop = _loop(tmp_path, [down] * 4, provider_max_retries=3)

    result = loop.run()

    assert result.end_state is EndState.BLOCKED
    assert result.blocker is not None and result.blocker.kind == "provider"
    assert "connection refused" in result.blocker.detail
    assert result.legacy_outcome is LoopOutcome.PROVIDER_UNAVAILABLE


def test_file_check_and_user_stop(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    envelope = _envelope(FileCheck(id="notes", path="NOTES.md", contains="fixed"))
    steps: list[int] = []
    loop = _loop(
        tmp_path,
        [_plan(), _fix(), _submit("s1"), tool_response("x", "execute_bash", command="echo")],
        envelope=envelope,
        on_event=lambda kind, data: steps.append(data["step"]) if kind == "model_step" else None,
    )
    loop.should_stop = lambda: len(steps) >= 4  # after the 4th model call

    result = loop.run()

    assert result.end_state is EndState.STOPPED
    assert result.stop is not None and result.stop.kind == "user"
    notes = result.verification[0]["results"][1]
    assert (notes["status"], notes["detail"]) == ("fail", "file does not exist")


# --------------------------------------------------------------------------- #
# Checkpoint / resume
# --------------------------------------------------------------------------- #
class _ProcessDied(BaseException):
    """Simulates the process being killed mid-run."""


def test_checkpoint_resume_in_a_fresh_process_finishes_and_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    run_dir = tmp_path / "run"
    checkpoint = run_dir / "checkpoint.json"
    trajectory = run_dir / "trajectory.jsonl"

    def die(_messages: list[dict[str, Any]]) -> ChatResponse:
        raise _ProcessDied

    first = _loop(
        repo,
        [_plan(), _fix(), die],
        checkpoint_path=checkpoint,
        recorder=TrajectoryRecorder(run_id="run-1", file_path=trajectory),
    )
    with pytest.raises(_ProcessDied):
        first.run()

    saved = read_checkpoint(checkpoint)
    assert saved["status"] == "running"
    assert saved["state"]["usage"]["steps"] == 2
    assert saved["state"]["plan"]["version"] == 1
    assert saved["toolset"]["telemetry"]["edits_applied"] == 1

    # "New process": fresh client, executor, workspace and toolset objects.
    client = ScriptedChatClient(responses=[_submit()])
    resumed = VerifiedLoop.resume(
        checkpoint, client=client, toolset=_toolset(repo), profile=PROFILE, provider_retry_backoff=0
    )
    result = resumed.run()

    assert result.end_state is EndState.DONE
    assert result.steps == 3
    assert result.plan is not None and result.plan["version"] == 1
    # The resumed model call saw the whole earlier transcript.
    assert client.calls[0][:2] == saved["state"]["messages"][:2]
    assert len(client.calls[0]) == len(saved["state"]["messages"])
    records = TrajectoryRecorder.parse(trajectory.read_text(encoding="utf-8"))
    assert [r["kind"] for r in records].count("meta") == 1
    assert any(r.get("note") == "resumed" for r in records)
    assert records[-1]["kind"] == "outcome" and records[-1]["status"] == "done"
    assert read_checkpoint(checkpoint)["status"] == "done"

    # Idempotent: resuming a finished run returns its result without model calls.
    again_client = ScriptedChatClient(responses=[])
    again = VerifiedLoop.resume(
        checkpoint, client=again_client, toolset=_toolset(repo), profile=PROFILE
    ).run()
    assert again.end_state is EndState.DONE
    assert again_client.calls == []
    assert again.evidence == result.evidence


# --------------------------------------------------------------------------- #
# SweAgent runs the verified loop by default
# --------------------------------------------------------------------------- #
def test_swe_agent_default_envelope_requires_passing_tests(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    agent = SweAgent(
        client=ScriptedChatClient(responses=[_submit("s1"), _fix(), _submit("s2")]),
        profile=PROFILE,
        budgets=LoopBudgets(max_steps=8, max_seconds=300),
        test_timeout=120,
        trajectory_dir=tmp_path / "traj",
    )
    result = agent.solve(
        SweTask(
            instance_id="swe-verified",
            problem_statement="add(2, 3) returns -1 instead of 5",
            executor=LocalDirectExecutor(repo),
            test_command=TEST_CMD,
        )
    )

    assert result.end_state == "done"
    assert result.outcome is LoopOutcome.SUBMITTED
    assert "return a + b" in result.patch
    assert result.run is not None and len(result.run.verification) == 2
    assert (tmp_path / "traj" / "swe-verified.checkpoint.json").exists()
