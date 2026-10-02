"""SpecSource + GitHubDelivery + DevFlow (Linear -> team -> GitHub PR)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.integrations import (
    DeliveryPolicy,
    FileSpecSource,
    GitHubDelivery,
    InlineSpecSource,
    LinearSpecSource,
)
from frontier_runtime.harness.swe_agent import SweTask
from frontier_runtime.harness.team import ModeratorVerdict, RoundResult, TeamResult

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


# -- spec sources -----------------------------------------------------------
def test_inline_and_file_spec_sources(tmp_path):
    s = InlineSpecSource("do the thing", title="Task A").fetch_spec()
    assert s.source == "inline" and s.title == "Task A" and "do the thing" in s.as_prompt()

    p = tmp_path / "spec.md"
    p.write_text("# Big Feature\nimplement it")
    fs = FileSpecSource(p).fetch_spec()
    assert fs.source == "file" and "implement it" in fs.body


def test_linear_spec_source_normalizes_issue():
    captured = {}

    def fetcher(issue_id):
        captured["id"] = issue_id
        return {
            "identifier": "FRONT-123",
            "title": "Add retry to client",
            "description": "The client should retry transient errors.",
            "url": "https://linear.app/x/issue/FRONT-123",
            "state": "In Progress",
        }

    spec = LinearSpecSource("FRONT-123", fetcher).fetch_spec()
    assert captured["id"] == "FRONT-123"
    assert spec.source == "linear" and spec.id == "FRONT-123"
    assert "retry transient" in spec.body
    assert spec.url.endswith("FRONT-123")
    assert spec.metadata.get("state") == "In Progress"


# -- delivery policy from settings -----------------------------------------
def test_delivery_policy_from_settings():
    p = DeliveryPolicy.from_settings({"auto_merge_on_reapprove": True, "target_branch": "develop"})
    assert p.auto_open_pr is True and p.auto_merge_on_reapprove is True
    assert p.target_branch == "develop"


# -- fake GitHub client -----------------------------------------------------
@dataclass
class FakeGitHub:
    open_pr_for: dict[str, dict] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def push_branch(self, branch: str) -> None:
        self.calls.append(f"push:{branch}")

    def find_open_pr(self, branch: str):
        self.calls.append(f"find:{branch}")
        return self.open_pr_for.get(branch)

    def open_pr(self, branch, base, title, body):
        self.calls.append(f"open:{branch}->{base}")
        return {"url": "https://github.com/x/y/pull/1", "number": 1}

    def merge_pr(self, number, method):
        self.calls.append(f"merge:{number}:{method}")
        return {"merged": True}

    def ci_status(self, branch):
        return "all checks passed"


def _team_result(approved: bool) -> TeamResult:
    verdict = ModeratorVerdict(
        decision="approve" if approved else "request_changes",
        rationale="ok",
        deferred=["minor: rename later"],
    )
    return TeamResult(
        spec="s",
        approved=approved,
        final_patch="diff",
        rounds=[RoundResult(index=0, implement=None, reviews=[], verdict=verdict)],
    )


def _git_repo(root: Path):
    (root / "f.txt").write_text("v1\n")
    for a in (
        ("init", "-q"),
        ("config", "user.email", "t@e.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-qm", "init"),
    ):
        subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True)


@requires_bash
@requires_git
def test_github_delivery_opens_pr_on_approve(tmp_path):
    _git_repo(tmp_path)
    ex = LocalDirectExecutor(tmp_path)
    (tmp_path / "f.txt").write_text("v2 (the approved change)\n")  # an approved edit

    gh = FakeGitHub()
    delivery = GitHubDelivery(github=gh)
    task = SweTask(instance_id="FRONT-123", problem_statement="x", executor=ex)
    from frontier_runtime.harness.integrations import Spec

    res = delivery.deliver(
        task,
        Spec(id="FRONT-123", title="Add retry", body="..."),
        _team_result(True),
        DeliveryPolicy(),
    )
    assert res.action == "opened_pr"
    assert res.branch == "frontier/FRONT-123"
    assert res.pr_url.endswith("/pull/1")
    assert res.ci_status == "all checks passed"
    # it actually created the branch + commit locally
    branches = ex.run_shell("git branch --format='%(refname:short)'").stdout
    assert "frontier/FRONT-123" in branches
    assert any(c.startswith("open:") for c in gh.calls)


@requires_bash
@requires_git
def test_github_delivery_merges_open_pr_on_reapprove(tmp_path):
    _git_repo(tmp_path)
    ex = LocalDirectExecutor(tmp_path)
    from frontier_runtime.harness.integrations import Spec

    gh = FakeGitHub(open_pr_for={"frontier/FRONT-9": {"number": 7, "url": "u", "state": "open"}})
    delivery = GitHubDelivery(github=gh)
    task = SweTask(instance_id="FRONT-9", problem_statement="x", executor=ex)
    policy = DeliveryPolicy(auto_merge_on_reapprove=True, merge_method="squash")

    res = delivery.deliver(
        task, Spec(id="FRONT-9", title="t", body="b"), _team_result(True), policy
    )
    assert res.action == "merged" and res.pr_number == 7
    assert "merge:7:squash" in gh.calls

    # with auto-merge disabled, it waits
    gh2 = FakeGitHub(open_pr_for={"frontier/FRONT-9": {"number": 7, "url": "u", "state": "open"}})
    res2 = GitHubDelivery(github=gh2).deliver(
        task,
        Spec(id="FRONT-9", title="t", body="b"),
        _team_result(True),
        DeliveryPolicy(auto_merge_on_reapprove=False),
    )
    assert res2.action == "awaiting_merge"
    assert not any(c.startswith("merge:") for c in gh2.calls)


@requires_bash
@requires_git
def test_devflow_spec_to_team_to_delivery(tmp_path):
    """End to end with a scripted team + fake GitHub: spec drives the run, an
    approved result opens a PR."""
    import json as _json

    from frontier_runtime.harness.integrations import DevFlow
    from frontier_runtime.harness.llm import ChatResponse, ScriptedChatClient, ToolCall
    from frontier_runtime.harness.loop import LoopBudgets
    from frontier_runtime.harness.model_profiles import resolve_profile
    from frontier_runtime.harness.team import TEAM_ROLE_AGENTS, TeamFlow

    # repo with a fixable bug
    (tmp_path / "mathlib").mkdir()
    (tmp_path / "mathlib" / "__init__.py").write_text("")
    (tmp_path / "mathlib" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "runtests.py").write_text(
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
        subprocess.run(["git", *a], cwd=str(tmp_path), check=True, capture_output=True)

    def py():
        for c in ("python3", "python"):
            if (
                subprocess.run(["bash", "-c", f"{c} --version"], capture_output=True).returncode
                == 0
            ):
                return c
        return "python3"

    def tc(cid, name, **kw):
        return ToolCall(id=cid, name=name, arguments=_json.dumps(kw))

    clients = {
        "architect": ScriptedChatClient(responses=[ChatResponse(text="PLAN")]),
        "implementer": ScriptedChatClient(
            responses=[
                ChatResponse(
                    tool_calls=[
                        tc(
                            "e",
                            "str_replace_editor",
                            command="str_replace",
                            path="mathlib/core.py",
                            old_str="return a - b",
                            new_str="return a + b",
                        )
                    ]
                ),
                ChatResponse(tool_calls=[tc("s", "submit", answer="fixed")]),
            ]
        ),
        "code-review": ScriptedChatClient(responses=[ChatResponse(text='{"verdict":"approve"}')]),
        "security": ScriptedChatClient(responses=[ChatResponse(text='{"verdict":"approve"}')]),
        "performance": ScriptedChatClient(responses=[ChatResponse(text='{"verdict":"approve"}')]),
        "moderator": ScriptedChatClient(responses=[ChatResponse(text='{"decision":"approve"}')]),
    }
    prof = resolve_profile("scripted", "x", profile_id="local-32b-class")
    team = TeamFlow(
        client_for=lambda r: clients[r],
        prompts={r: "x" for r in TEAM_ROLE_AGENTS},
        profiles={r: prof for r in TEAM_ROLE_AGENTS},
        budgets=LoopBudgets(max_steps=6),
        max_rounds=2,
    )

    spec_source = LinearSpecSource(
        "FRONT-1",
        lambda i: {
            "identifier": "FRONT-1",
            "title": "Fix add",
            "description": "add(2,3) must equal 5",
            "url": "https://linear.app/x/issue/FRONT-1",
        },
    )
    gh = FakeGitHub()
    flow = DevFlow(
        team=team,
        spec_source=spec_source,
        delivery=GitHubDelivery(github=gh),
        policy=DeliveryPolicy(),
    )
    task = SweTask(
        instance_id="repo",
        problem_statement="(from spec)",
        executor=LocalDirectExecutor(tmp_path),
        test_command=f"{py()} runtests.py",
    )
    result = flow.run(task)

    assert result.spec.source == "linear" and result.spec.id == "FRONT-1"
    assert result.approved is True
    assert result.delivery.action == "opened_pr"
    assert result.delivery.branch == "frontier/FRONT-1"
