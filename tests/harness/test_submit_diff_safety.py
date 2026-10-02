"""Submit must not silently ship an empty patch after a real edit.

Driven by sdet-bench seed0 syn-max-empty: the model edited the file and tests
passed, but a transient git stat-cache miss made workspace.diff() return empty,
so an empty patch was submitted and graded unresolved. The harness must reject
that submit and let the model recover.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.tools import CodingToolset
from frontier_runtime.harness.workspace import Workspace

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _repo(root: Path) -> None:
    (root / "u.py").write_text("def f():\n    return 0\n")
    for a in (
        ("init", "-q"),
        ("config", "user.email", "t@e.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-qm", "x"),
    ):
        subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True)


@requires_bash
@requires_git
def test_submit_rejected_when_edit_applied_but_diff_empty(tmp_path):
    _repo(tmp_path)
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ts = CodingToolset(workspace=ws)

    # Simulate the failure mode: telemetry says an edit was applied, but the
    # working tree matches HEAD (diff empty) — patch a diff() that returns "".
    ts.telemetry.edits_applied = 1
    ws.diff = lambda: ""  # type: ignore[method-assign]
    ws.has_uncommitted_changes = lambda: False  # type: ignore[method-assign]

    out = ts.dispatch("submit", {"answer": "done"})
    assert "rejected" in out.lower()
    assert ts.submitted is False  # loop must NOT terminate
    assert ts.submission is None


@requires_bash
@requires_git
def test_submit_accepts_genuine_noop(tmp_path):
    _repo(tmp_path)
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ts = CodingToolset(workspace=ws)
    # no edits applied -> an empty diff is a legitimate (if unusual) submission
    out = ts.dispatch("submit", {"answer": "nothing to change"})
    assert ts.submitted is True
    assert "Submission recorded" in out


@requires_bash
@requires_git
def test_submit_succeeds_with_real_edit(tmp_path):
    _repo(tmp_path)
    ws = Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path))
    ts = CodingToolset(workspace=ws)
    ts.dispatch(
        "str_replace_editor",
        {"command": "str_replace", "path": "u.py", "old_str": "return 0", "new_str": "return 1"},
    )
    out = ts.dispatch("submit", {"answer": "fixed"})
    assert ts.submitted is True
    assert "return 1" in ts.submission["patch"]
    assert "rejected" not in out.lower()
