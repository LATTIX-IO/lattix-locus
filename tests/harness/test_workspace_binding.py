"""Workspace binding: tie a task to a repo, git-worktree isolation, out-of-bounds boundary."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.tools import CodingToolset
from frontier_runtime.harness.workspace import Workspace
from frontier_runtime.harness.workspace_binding import WorkspaceBinding, WorkspaceManager

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _repo(root: Path) -> None:
    (root / "app.py").write_text("x = 1\n")
    for a in (
        ("init", "-q"),
        ("config", "user.email", "t@e.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-qm", "init"),
    ):
        subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True)


# -- binding payload round-trip --------------------------------------------
def test_binding_payload_roundtrip():
    b = WorkspaceBinding(
        repo_path="/repo",
        base_ref="main",
        branch="feat/x",
        allow_outside="deny",
        extra_paths=["/shared"],
        test_command="pytest",
    )
    p = b.to_payload()
    assert p["isolation"] == "worktree" and p["allow_outside"] == "deny"
    b2 = WorkspaceBinding.from_payload(p)
    assert b2.base_ref == "main" and b2.extra_paths == ["/shared"]


# -- executor confinement ---------------------------------------------------
def test_executor_allows_within_root_and_extras(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    ex = LocalDirectExecutor(root, extra_paths=[str(other)])
    assert ex.allows("app.py") is True
    assert ex.allows(str(root / "sub" / "f.py")) is True
    assert ex.allows(str(other / "lib.py")) is True  # granted extra
    assert ex.allows(str(tmp_path / "secret.txt")) is False  # outside both


# -- out-of-bounds escalation in the toolset --------------------------------
@requires_bash
@requires_git
def test_editor_escalates_out_of_bounds(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _repo(root)
    ts = CodingToolset(
        workspace=Workspace(run_id="t", executor=LocalDirectExecutor(root)),
        out_of_bounds="ask",
    )
    # in-bounds edit works
    ok = ts.dispatch(
        "str_replace_editor", {"command": "create", "path": "new.py", "file_text": "y = 2\n"}
    )
    assert "written" in ok.lower()
    # out-of-bounds edit is blocked + escalated, NOT executed
    outside = str((tmp_path / "evil.py"))
    res = ts.dispatch(
        "str_replace_editor", {"command": "create", "path": outside, "file_text": "pwn"}
    )
    assert "permission required" in res.lower()
    assert not (tmp_path / "evil.py").exists()  # nothing written outside
    assert ts.escalations and ts.escalations[0]["path"] == outside


@requires_bash
@requires_git
def test_out_of_bounds_deny_policy_blocks_without_escalation_invite(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _repo(root)
    seen = []
    ts = CodingToolset(
        workspace=Workspace(run_id="t", executor=LocalDirectExecutor(root)),
        out_of_bounds="deny",
        on_escalation=seen.append,
    )
    res = ts.dispatch(
        "str_replace_editor", {"command": "view", "path": str(tmp_path / "outside.py")}
    )
    assert "[denied]" in res
    assert len(seen) == 1  # escalation still recorded for audit


@requires_bash
@requires_git
def test_extra_path_grant_allows_outside_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _repo(root)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "lib.py").write_text("Z = 9\n")
    ts = CodingToolset(
        workspace=Workspace(
            run_id="t", executor=LocalDirectExecutor(root, extra_paths=[str(shared)])
        ),
        out_of_bounds="ask",
    )
    res = ts.dispatch("str_replace_editor", {"command": "view", "path": str(shared / "lib.py")})
    assert "Z = 9" in res and "permission required" not in res.lower()


# -- worktree isolation -----------------------------------------------------
@requires_bash
@requires_git
def test_worktree_provision_isolates_and_cleans_up(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    mgr = WorkspaceManager(worktrees_root=tmp_path / "wt")
    binding = WorkspaceBinding(repo_path=str(repo), isolation="worktree", test_command="true")
    prov = mgr.provision(binding, run_id="FRONT-42")
    try:
        # the worktree is a separate dir, checked out, on a task branch
        assert prov.root != repo and prov.root.exists()
        assert (prov.root / "app.py").read_text() == "x = 1\n"
        assert prov.branch == "frontier/FRONT-42"
        # editing in the worktree does NOT touch the main repo working tree
        prov.workspace.executor.write_file("app.py", "x = 2\n")
        assert (repo / "app.py").read_text() == "x = 1\n"
        assert (prov.root / "app.py").read_text() == "x = 2\n"
    finally:
        prov.cleanup()
    assert not prov.root.exists()  # cleaned up


@requires_bash
@requires_git
def test_build_task_from_binding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    mgr = WorkspaceManager(worktrees_root=tmp_path / "wt")
    binding = WorkspaceBinding(repo_path=str(repo), test_command="true")
    task, prov = mgr.build_task(binding, "FRONT-7", "implement the thing")
    try:
        assert task.instance_id == "FRONT-7"
        assert task.problem_statement == "implement the thing"
        assert task.executor.workdir() == str(prov.root)
        assert task.metadata["binding"]["repo_path"] == str(repo.resolve())
    finally:
        prov.cleanup()
