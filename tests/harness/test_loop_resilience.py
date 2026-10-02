"""Loop resilience fixes driven by gpt-oss:20b run 2 (contended Ollama).

1. provider errors are retried with backoff before declaring unavailable
2. editor sub-command tool names (view/str_replace/...) and common aliases
   (bash/edit/finish) are normalized to real tools instead of re-asked
"""

from __future__ import annotations

import shutil

import pytest

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.llm import ChatResponse, ScriptedChatClient, ToolCall
from frontier_runtime.harness.loop import AgentLoop, LoopBudgets, LoopOutcome, _normalize_tool_name
from frontier_runtime.harness.model_profiles import resolve_profile
from frontier_runtime.harness.tools import CodingToolset
from frontier_runtime.harness.workspace import Workspace

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")


def test_normalize_editor_subcommand_names():
    name, args = _normalize_tool_name("view", '{"path": "a.py", "line_start": 1, "line_end": 9}')
    assert name == "str_replace_editor"
    assert args["command"] == "view" and args["path"] == "a.py"

    name, args = _normalize_tool_name("str_replace", {"path": "a.py", "old_str": "x"})
    assert name == "str_replace_editor" and args["command"] == "str_replace"


def test_normalize_common_aliases():
    assert _normalize_tool_name("bash", "{}")[0] == "execute_bash"
    assert _normalize_tool_name("finish", "{}")[0] == "submit"
    assert _normalize_tool_name("edit", "{}")[0] == "str_replace_editor"
    # real names pass through untouched
    assert _normalize_tool_name("execute_bash", "{}")[0] == "execute_bash"


class _FlakyClient:
    """Raises on the first N completes, then returns a submit call."""

    provider = "flaky"
    model = "flaky"

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("connection reset by peer")
        return ChatResponse(
            tool_calls=[ToolCall(id="s", name="submit", arguments='{"answer": "ok"}')]
        )


@requires_bash
def test_provider_retry_recovers(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ts = CodingToolset(workspace=ws)
    client = _FlakyClient(fail_times=2)  # fails twice, succeeds on 3rd
    loop = AgentLoop(
        client=client,
        toolset=ts,
        profile=resolve_profile("flaky", "flaky", profile_id="local-32b-class"),
        system_prompt="sys",
        user_prompt="do it",
        budgets=LoopBudgets(max_steps=5),
        provider_max_retries=3,
        provider_retry_backoff=0.01,  # keep test fast
    )
    result = loop.run()
    assert result.outcome == LoopOutcome.SUBMITTED
    assert client.calls == 3  # 2 failures + 1 success on the same step


@requires_bash
def test_provider_gives_up_after_retries(tmp_path):
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ts = CodingToolset(workspace=ws)
    client = _FlakyClient(fail_times=99)
    loop = AgentLoop(
        client=client,
        toolset=ts,
        profile=resolve_profile("flaky", "flaky", profile_id="local-32b-class"),
        system_prompt="sys",
        user_prompt="do it",
        budgets=LoopBudgets(max_steps=5),
        provider_max_retries=2,
        provider_retry_backoff=0.01,
    )
    result = loop.run()
    assert result.outcome == LoopOutcome.PROVIDER_UNAVAILABLE
    assert client.calls == 3  # initial + 2 retries


@requires_bash
def test_loop_handles_view_called_as_tool(tmp_path):
    """End to end: model calls 'view' as a tool name, loop normalizes it."""
    (tmp_path / "core.py").write_text("def add(a, b):\n    return a - b\n")
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ts = CodingToolset(workspace=ws)
    client = ScriptedChatClient(
        responses=[
            # model calls editor sub-command 'view' directly as a tool
            ChatResponse(
                tool_calls=[ToolCall(id="v", name="view", arguments='{"path": "core.py"}')]
            ),
            ChatResponse(
                tool_calls=[ToolCall(id="s", name="submit", arguments='{"answer": "seen"}')]
            ),
        ]
    )
    loop = AgentLoop(
        client=client,
        toolset=ts,
        profile=resolve_profile("scripted", "x", profile_id="local-32b-class"),
        system_prompt="sys",
        user_prompt="look",
        budgets=LoopBudgets(max_steps=5),
    )
    result = loop.run()
    assert result.outcome == LoopOutcome.SUBMITTED
    # no re-ask was needed; the 'view' call was normalized and executed
    assert result.telemetry["reasks"] == 0
    assert result.telemetry["tool_calls_malformed"] == 0
