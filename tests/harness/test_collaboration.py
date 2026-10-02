"""Collaborative cross-functional team: discuss → consensus → build → verify → handback.

Verifies genuine collaboration mechanics deterministically: each engineer reasons
(chain-of-thought captured), the Tech Lead drives consensus, the team builds + tests
against the agreed design, verifies it, and hands back a completed feature.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.collaboration import (
    COLLAB_ROLE_AGENTS,
    CollaborativeTeam,
    build_collaborative_team,
)
from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.integrations import Spec
from frontier_runtime.harness.llm import ChatResponse, ScriptedChatClient, ToolCall
from frontier_runtime.harness.loop import LoopBudgets
from frontier_runtime.harness.model_profiles import resolve_profile
from frontier_runtime.harness.swe_agent import SweTask

REPO_ROOT = Path(__file__).resolve().parents[2]
requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _py():
    for c in ("python3", "python"):
        if subprocess.run(["bash", "-c", f"{c} --version"], capture_output=True).returncode == 0:
            return c
    return "python3"


def _repo(root: Path) -> None:
    (root / "mathlib").mkdir()
    (root / "mathlib" / "__init__.py").write_text("")
    (root / "mathlib" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "runtests.py").write_text(
        "import os,sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "from mathlib.core import add\nassert add(2,3)==5\nprint('OK')\n"
    )
    for a in (
        ("init", "-q"),
        ("config", "user.email", "t@e.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-qm", "init"),
    ):
        subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True)


def _j(**kw):
    return ChatResponse(text=json.dumps(kw))


def _tc(cid, name, **kw):
    return ChatResponse(tool_calls=[ToolCall(id=cid, name=name, arguments=json.dumps(kw))])


def test_shipped_collab_team_loads_all_seats():
    team = build_collaborative_team(lambda role: None, repo_root=REPO_ROOT)
    # all collab roles have a prompt loaded from examples/agents
    for role in COLLAB_ROLE_AGENTS:
        assert role in team.prompts, role
    assert "Tech Lead" in team.prompts["tech-lead"]
    assert "Backend Engineer" in team.prompts["backend"]
    assert "Frontend Engineer" in team.prompts["frontend"]


@requires_bash
@requires_git
def test_team_discusses_to_consensus_then_builds_and_completes(tmp_path):
    _repo(tmp_path)
    participants = ("backend", "sdet", "security")
    clients = {
        "tech-lead": ScriptedChatClient(
            responses=[
                _j(
                    thinking="small bug-fix; need add() correct",
                    message="Intent: add(2,3)=5. Backend+SDET+Security, weigh in.",
                ),
                _j(
                    thinking="backend has the fix, sdet has a test, security sees no risk — converged",
                    consensus=True,
                    message="We agree.",
                    agreed_design="Change `return a - b` to `return a + b` in mathlib/core.py; cover with the existing add test; no security impact.",
                ),
                _j(
                    thinking="tests pass, matches intent, no blocking concerns",
                    message="Approved — ship it.",
                    decision="approve",
                    required_changes=[],
                ),
            ]
        ),
        "backend": ScriptedChatClient(
            responses=[
                _j(
                    thinking="the operator is wrong; flip - to +",
                    message="Root cause is the operator; I'll flip it.",
                    proposal="edit mathlib/core.py add() to use +",
                    concerns=[],
                ),
                _j(
                    thinking="diff flips operator, correct",
                    message="Backend correct.",
                    verdict="approve",
                    concerns=[],
                ),
            ]
        ),
        "sdet": ScriptedChatClient(
            responses=[
                _j(
                    thinking="need a test asserting add(2,3)==5",
                    message="There's a runtests check; it'll prove the fix.",
                    proposal="run the existing test",
                    concerns=[],
                ),
                # implement phase: SweAgent tool calls
                _tc(
                    "e",
                    "str_replace_editor",
                    command="str_replace",
                    path="mathlib/core.py",
                    old_str="return a - b",
                    new_str="return a + b",
                ),
                _tc("t", "run_tests"),
                _tc("s", "submit", answer="Implemented agreed fix"),
                _j(
                    thinking="tests pass",
                    message="Verified functional.",
                    verdict="approve",
                    concerns=[],
                ),
            ]
        ),
        "security": ScriptedChatClient(
            responses=[
                _j(
                    thinking="pure arithmetic, no input handling",
                    message="No security surface here.",
                    proposal="",
                    concerns=[],
                ),
                _j(
                    thinking="no security risk in the diff",
                    message="No concerns.",
                    verdict="approve",
                    concerns=[],
                ),
            ]
        ),
    }
    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = CollaborativeTeam(
        client_for=lambda role: clients[role],
        prompts={r: f"{r} persona" for r in ("tech-lead", *participants)},
        profiles={r: prof for r in ("tech-lead", *participants)},
        budgets=LoopBudgets(max_steps=6),
        participants=participants,
        max_discussion_rounds=1,
        max_build_rounds=2,
    )
    task = SweTask(
        instance_id="FRONT-1",
        problem_statement="(spec)",
        executor=LocalDirectExecutor(tmp_path),
        test_command=f"{_py()} runtests.py",
    )
    result = team.run(task, Spec(id="FRONT-1", title="Fix add", body="add(2,3) must equal 5"))

    assert result.approved is True
    assert "return a + b" in result.agreed_design or "+" in result.agreed_design
    assert "return a + b" in result.final_patch
    # genuine collaboration: each seat reasoned (chain-of-thought) and spoke
    turns = result.conversation.turns
    assert any(t.role == "backend" and t.phase == "discuss" and t.thinking for t in turns)
    assert any(t.role == "security" and t.phase == "discuss" for t in turns)
    assert any(t.role == "tech-lead" and t.phase == "facilitate" for t in turns)
    assert any(t.phase == "verify" for t in turns)
    assert any(
        t.role == "tech-lead" and t.phase == "gate" and t.verdict == "approve" for t in turns
    )
    # chain-of-thought is captured in the transcript, hidden from the visible stream
    assert "(thinking)" in result.conversation.transcript()
    assert "thinking" not in result.conversation.visible()
    assert "FEATURE COMPLETE" in result.chat()
    assert "complete and approved" in result.handback


@requires_bash
@requires_git
def test_team_returns_incomplete_when_no_passing_solution(tmp_path):
    _repo(tmp_path)
    participants = ("backend", "sdet")
    clients = {
        "tech-lead": ScriptedChatClient(
            responses=[
                _j(message="frame"),
                _j(consensus=True, message="go", agreed_design="fix it"),
                _j(
                    message="not done",
                    decision="request_changes",
                    required_changes=["actually fix add"],
                ),
                _j(
                    message="still not done",
                    decision="request_changes",
                    required_changes=["fix add"],
                ),
            ]
        ),
        "backend": ScriptedChatClient(
            responses=[_j(message="b", proposal="p")]
            + [_j(message="b", verdict="request_changes")] * 2
        ),
        "sdet": ScriptedChatClient(
            responses=[
                _j(message="s"),
                # build round 1: never submits a real fix (just echoes), budget exhausts
                *[_tc(f"b{i}", "execute_bash", command="echo working") for i in range(8)],
            ]
            + [_j(message="s", verdict="request_changes")]
            + [
                *[_tc(f"c{i}", "execute_bash", command="echo working") for i in range(8)],
            ]
            + [_j(message="s", verdict="request_changes")]
        ),
    }
    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = CollaborativeTeam(
        client_for=lambda role: clients[role],
        prompts={r: "p" for r in ("tech-lead", *participants)},
        profiles={r: prof for r in ("tech-lead", *participants)},
        budgets=LoopBudgets(max_steps=3),
        participants=participants,
        max_discussion_rounds=1,
        max_build_rounds=2,
    )
    task = SweTask(
        instance_id="f",
        problem_statement="(spec)",
        executor=LocalDirectExecutor(tmp_path),
        test_command=f"{_py()} runtests.py",
    )
    result = team.run(task, "make add correct")
    assert result.approved is False
    assert result.final_patch == ""
    assert "NOT complete" in result.handback
