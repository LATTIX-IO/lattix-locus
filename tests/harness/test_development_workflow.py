"""The callable cross-functional DevelopmentWorkflow: plan→execute→test→secure→deploy."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.development import DevelopmentWorkflow, build_development_workflow
from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.integrations import Spec
from frontier_runtime.harness.llm import ChatResponse, ScriptedChatClient, ToolCall
from frontier_runtime.harness.loop import LoopBudgets
from frontier_runtime.harness.model_profiles import resolve_profile
from frontier_runtime.harness.swe_agent import SweTask
from frontier_runtime.harness.team import TEAM_ROLE_AGENTS, TeamFlow

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


def _tc(cid, name, **kw):
    return ToolCall(id=cid, name=name, arguments=json.dumps(kw))


def test_builds_from_shipped_team_with_azure_deploy_prompt():
    wf = build_development_workflow(lambda role: None, repo_root=REPO_ROOT)
    assert set(wf.team.prompts) == set(TEAM_ROLE_AGENTS)
    # the deploy-prep phase is backed by the shipped Azure agent prompt
    assert "Azure" in wf.deploy_prompt


@requires_bash
@requires_git
def test_development_workflow_runs_full_chat_and_approves(tmp_path):
    _repo(tmp_path)
    clients = {
        "architect": ScriptedChatClient(
            responses=[ChatResponse(text="PLAN: change - to + in add()")]
        ),
        "implementer": ScriptedChatClient(
            responses=[
                ChatResponse(
                    tool_calls=[
                        _tc(
                            "e",
                            "str_replace_editor",
                            command="str_replace",
                            path="mathlib/core.py",
                            old_str="return a - b",
                            new_str="return a + b",
                        )
                    ]
                ),
                ChatResponse(tool_calls=[_tc("t", "run_tests")]),
                ChatResponse(tool_calls=[_tc("s", "submit", answer="Fixed the operator")]),
            ]
        ),
        "code-review": ScriptedChatClient(
            responses=[ChatResponse(text='{"verdict":"approve","summary":"clean"}')]
        ),
        "security": ScriptedChatClient(
            responses=[ChatResponse(text='{"verdict":"approve","summary":"no issues"}')]
        ),
        "performance": ScriptedChatClient(
            responses=[ChatResponse(text='{"verdict":"approve","summary":"fine"}')]
        ),
        "moderator": ScriptedChatClient(
            responses=[
                ChatResponse(
                    text='{"decision":"approve","rationale":"tests pass, no blocking findings"}'
                )
            ]
        ),
        "azure": ScriptedChatClient(
            responses=[
                ChatResponse(text="Deployment readiness: no infra change; merge PR after CI green.")
            ]
        ),
    }
    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = TeamFlow(
        client_for=lambda r: clients[r],
        prompts={r: "x" for r in TEAM_ROLE_AGENTS},
        profiles={r: prof for r in TEAM_ROLE_AGENTS},
        budgets=LoopBudgets(max_steps=6),
        max_rounds=2,
    )
    wf = DevelopmentWorkflow(
        team=team,
        deploy_client=clients["azure"],
        deploy_prompt="azure deploy prep",
        deploy_profile=prof,
    )
    task = SweTask(
        instance_id="featX",
        problem_statement="(spec)",
        executor=LocalDirectExecutor(tmp_path),
        test_command=f"{_py()} runtests.py",
    )
    result = wf.run(task, Spec(id="FRONT-1", title="Fix add", body="add(2,3) must equal 5"))

    assert result.approved is True
    # transcript covers every phase as a chat
    phases = {t.phase for t in result.transcript}
    assert {"plan", "execute", "test", "secure", "moderate", "deploy"} <= phases
    speakers = {t.speaker for t in result.transcript}
    assert {
        "Spec Architect",
        "SDET",
        "Security Auditor",
        "Quality Moderator",
        "Azure Cloud Engineer",
    } <= speakers
    assert "no infra change" in result.deploy_readiness
    # the rendered chat reads as a conversation
    chat = result.chat()
    assert "=== PLAN ===" in chat and "=== DEPLOY ===" in chat and "APPROVED" in chat


@requires_bash
@requires_git
def test_no_deploy_phase_when_not_approved(tmp_path):
    _repo(tmp_path)
    clients = {
        "architect": ScriptedChatClient(responses=[ChatResponse(text="PLAN")] * 3),
        # implementer never submits a real fix -> budget exhausted, not approved
        "implementer": ScriptedChatClient(
            responses=[
                ChatResponse(tool_calls=[_tc(f"b{i}", "execute_bash", command="echo working")])
                for i in range(20)
            ]
        ),
        "code-review": ScriptedChatClient(
            responses=[ChatResponse(text='{"verdict":"approve"}')] * 3
        ),
        "security": ScriptedChatClient(
            responses=[
                ChatResponse(
                    text='{"verdict":"request_changes","findings":[{"severity":"high","issue":"x"}]}'
                )
            ]
            * 3
        ),
        "performance": ScriptedChatClient(
            responses=[ChatResponse(text='{"verdict":"approve"}')] * 3
        ),
        "moderator": ScriptedChatClient(
            responses=[
                ChatResponse(text='{"decision":"request_changes","required_changes":["fix it"]}')
            ]
            * 3
        ),
        "azure": ScriptedChatClient(responses=[ChatResponse(text="should not be called")]),
    }
    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = TeamFlow(
        client_for=lambda r: clients[r],
        prompts={r: "x" for r in TEAM_ROLE_AGENTS},
        profiles={r: prof for r in TEAM_ROLE_AGENTS},
        budgets=LoopBudgets(max_steps=3),
        max_rounds=2,
    )
    wf = DevelopmentWorkflow(
        team=team, deploy_client=clients["azure"], deploy_prompt="x", deploy_profile=prof
    )
    task = SweTask(
        instance_id="f",
        problem_statement="(spec)",
        executor=LocalDirectExecutor(tmp_path),
        test_command=f"{_py()} runtests.py",
    )
    result = wf.run(task, "do the thing")
    assert result.approved is False
    assert all(t.phase != "deploy" for t in result.transcript)  # no deploy phase if not shipped
    assert result.deploy_readiness == ""
