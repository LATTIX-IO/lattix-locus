"""Multi-agent TeamFlow: implement -> review panel -> moderate -> fix loop."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.llm import ChatResponse, ScriptedChatClient, ToolCall
from frontier_runtime.harness.loop import LoopBudgets
from frontier_runtime.harness.model_profiles import resolve_profile
from frontier_runtime.harness.swe_agent import SweTask
from frontier_runtime.harness.team import (
    TEAM_ROLE_AGENTS,
    TeamFlow,
    build_team_from_shipped,
    extract_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _shell_python() -> str:
    for cand in ("python3", "python"):
        try:
            if (
                subprocess.run(["bash", "-c", f"{cand} --version"], capture_output=True).returncode
                == 0
            ):
                return cand
        except OSError:
            continue
    return "python3"


PY = _shell_python()
RUNTESTS = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
    "from mathlib.core import add\n"
    "assert add(2, 3) == 5\n"
    "print('OK')\n"
)


def _repo(root: Path) -> None:
    (root / "mathlib").mkdir()
    (root / "mathlib" / "__init__.py").write_text("")
    (root / "mathlib" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "runtests.py").write_text(RUNTESTS)
    for a in (
        ("init", "-q"),
        ("config", "user.email", "t@e.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-qm", "x"),
    ):
        subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True)


def _tc(cid, name, **args):
    return ToolCall(id=cid, name=name, arguments=json.dumps(args))


def _review_json(verdict, findings=None, summary=""):
    return ChatResponse(
        text=json.dumps({"verdict": verdict, "findings": findings or [], "summary": summary})
    )


def _moderator_json(decision, required=None, rationale=""):
    return ChatResponse(
        text=json.dumps(
            {
                "decision": decision,
                "required_changes": required or [],
                "deferred": [],
                "rationale": rationale,
            }
        )
    )


# -- extract_json -----------------------------------------------------------
def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('here is the review {"verdict": "approve"} done')["verdict"] == "approve"
    assert extract_json("no json here") is None


def test_shipped_team_loads_all_roles():
    team = build_team_from_shipped(lambda role: None, repo_root=REPO_ROOT)
    # all six roles have a prompt loaded from examples/agents
    assert set(team.prompts) == set(TEAM_ROLE_AGENTS)
    assert "Software Development Engineer in Test" in team.prompts["implementer"]
    assert "security" in team.prompts["security"].lower()


# -- full team flow: send-back then approve ---------------------------------
@requires_bash
@requires_git
def test_team_requests_changes_then_approves(tmp_path):
    _repo(tmp_path)
    executor = LocalDirectExecutor(tmp_path)

    clients = {
        "architect": ScriptedChatClient(responses=[ChatResponse(text="PLAN: fix add to use +")]),
        "implementer": ScriptedChatClient(
            responses=[
                # round 0: apply the core fix, test, submit
                _resp(
                    _tc(
                        "e0",
                        "str_replace_editor",
                        command="str_replace",
                        path="mathlib/core.py",
                        old_str="return a - b",
                        new_str="return a + b",
                    )
                ),
                _resp(_tc("t0", "run_tests")),
                _resp(_tc("s0", "submit", answer="fixed add")),
                # round 1: address the moderator's required change (add a docstring), submit
                _resp(
                    _tc(
                        "e1",
                        "str_replace_editor",
                        command="str_replace",
                        path="mathlib/core.py",
                        old_str="def add(a, b):",
                        new_str='def add(a, b):\n    """Add two numbers."""',
                    )
                ),
                _resp(_tc("s1", "submit", answer="added docstring")),
            ]
        ),
        # round 0 reviews: security requests changes; round 1: all approve
        "code-review": ScriptedChatClient(
            responses=[_review_json("approve"), _review_json("approve")]
        ),
        "security": ScriptedChatClient(
            responses=[
                _review_json(
                    "request_changes",
                    [
                        {
                            "severity": "major",
                            "issue": "no docstring/contract",
                            "fix": "document add",
                        }
                    ],
                ),
                _review_json("approve"),
            ]
        ),
        "performance": ScriptedChatClient(
            responses=[_review_json("approve"), _review_json("approve")]
        ),
        "moderator": ScriptedChatClient(
            responses=[
                _moderator_json(
                    "request_changes", ["Document the add() contract with a docstring"]
                ),
                _moderator_json("approve", rationale="tests pass, concerns addressed"),
            ]
        ),
    }

    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = TeamFlow(
        client_for=lambda role: clients[role],
        prompts={r: f"{r} instructions" for r in TEAM_ROLE_AGENTS},
        profiles={r: prof for r in TEAM_ROLE_AGENTS},
        budgets=LoopBudgets(max_steps=8),
        max_rounds=3,
    )
    task = SweTask(
        instance_id="team-add",
        problem_statement="add(2,3) must equal 5",
        executor=executor,
        test_command=f"{PY} runtests.py",
    )
    result = team.run(task, spec="add(2,3) must equal 5; document the function")

    assert result.plan.startswith("PLAN")
    assert result.round_count == 2  # one send-back, then approve
    assert result.approved is True
    assert "return a + b" in result.final_patch
    assert '"""Add two numbers."""' in result.final_patch
    # round 0 was a send-back driven by the security reviewer
    assert result.rounds[0].verdict.decision == "request_changes"
    assert any(r.role == "security" and r.requests_changes for r in result.rounds[0].reviews)


@requires_bash
@requires_git
def test_team_approves_first_round_when_clean(tmp_path):
    _repo(tmp_path)
    executor = LocalDirectExecutor(tmp_path)
    clients = {
        "architect": ScriptedChatClient(responses=[ChatResponse(text="PLAN")]),
        "implementer": ScriptedChatClient(
            responses=[
                _resp(
                    _tc(
                        "e",
                        "str_replace_editor",
                        command="str_replace",
                        path="mathlib/core.py",
                        old_str="return a - b",
                        new_str="return a + b",
                    )
                ),
                _resp(_tc("s", "submit", answer="done")),
            ]
        ),
        "code-review": ScriptedChatClient(responses=[_review_json("approve")]),
        "security": ScriptedChatClient(responses=[_review_json("approve")]),
        "performance": ScriptedChatClient(responses=[_review_json("approve")]),
        "moderator": ScriptedChatClient(responses=[_moderator_json("approve")]),
    }
    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = TeamFlow(
        client_for=lambda role: clients[role],
        prompts={r: "x" for r in TEAM_ROLE_AGENTS},
        profiles={r: prof for r in TEAM_ROLE_AGENTS},
        budgets=LoopBudgets(max_steps=6),
        max_rounds=3,
    )
    task = SweTask(
        instance_id="clean",
        problem_statement="fix add",
        executor=executor,
        test_command=f"{PY} runtests.py",
    )
    result = team.run(task, spec="fix add")
    assert result.approved and result.round_count == 1


def _resp(tool_call: ToolCall) -> ChatResponse:
    return ChatResponse(tool_calls=[tool_call])
