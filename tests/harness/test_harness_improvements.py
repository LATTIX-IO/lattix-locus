"""Regression tests for harness fixes driven by real gpt-oss:20b trajectories.

Run 1 (gpt-oss:20b via Ollama, syn-add-sign) surfaced three defects, fixed here:
1. submitted patch included __pycache__/*.pyc (breaks SWE-bench git apply)
2. editor call {path, line_start, line_end} with no 'command' wasted a re-ask
3. execute_bash accepted timeout=10000 (could hang a run)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.tools import BASH_TIMEOUT_CEILING, CodingToolset
from frontier_runtime.harness.workspace import Workspace

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _repo(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@e.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-q", "-m", "x"),
    ):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _toolset(root: Path) -> CodingToolset:
    return CodingToolset(workspace=Workspace(run_id="t", executor=LocalDirectExecutor(root)))


# -- fix 2: editor command inference / aliases ------------------------------
@requires_bash
@requires_git
def test_editor_infers_view_from_line_range(tmp_path):
    _repo(tmp_path)
    ts = _toolset(tmp_path)
    # gpt-oss's actual malformed shape: no 'command', uses line_start/line_end
    out = ts.dispatch("str_replace_editor", {"path": "pkg/core.py", "line_start": 1, "line_end": 2})
    assert "def add" in out and "return a - b" in out
    assert "[error]" not in out


@requires_bash
@requires_git
def test_editor_infers_str_replace_and_create(tmp_path):
    _repo(tmp_path)
    ts = _toolset(tmp_path)
    # no 'command' but old_str present -> str_replace
    out = ts.dispatch(
        "str_replace_editor",
        {"path": "pkg/core.py", "old_str": "return a - b", "new_str": "return a + b"},
    )
    assert "Edit applied" in out
    assert "return a + b" in (tmp_path / "pkg" / "core.py").read_text()
    # no 'command' but file_text present -> create
    out2 = ts.dispatch("str_replace_editor", {"path": "pkg/new.py", "file_text": "X = 1\n"})
    assert "written" in out2.lower()
    assert (tmp_path / "pkg" / "new.py").read_text() == "X = 1\n"


# -- fix 3: bash timeout clamp ----------------------------------------------
@requires_bash
@requires_git
def test_bash_timeout_is_clamped(tmp_path, monkeypatch):
    _repo(tmp_path)
    ts = _toolset(tmp_path)
    captured = {}
    real = ts.workspace.executor.run_shell

    def spy(script, *, timeout=60):
        captured["timeout"] = timeout
        return real(script, timeout=timeout)

    monkeypatch.setattr(ts.workspace.executor, "run_shell", spy)
    ts.dispatch("execute_bash", {"command": "echo hi", "timeout": 10000})
    assert captured["timeout"] == BASH_TIMEOUT_CEILING


# -- fix 1: clean patch (no pycache/pyc) ------------------------------------
@requires_bash
@requires_git
def test_submit_diff_excludes_pycache(tmp_path):
    _repo(tmp_path)
    ts = _toolset(tmp_path)
    # make a real source edit
    ts.dispatch(
        "str_replace_editor",
        {
            "command": "str_replace",
            "path": "pkg/core.py",
            "old_str": "return a - b",
            "new_str": "return a + b",
        },
    )
    # simulate test side effects: a pyc + a __pycache__ dir
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "core.cpython-312.pyc").write_bytes(b"\x00\x01binary")
    ts.dispatch("submit", {"answer": "done"})
    patch = ts.submission["patch"]
    assert "return a + b" in patch  # real change present
    assert "__pycache__" not in patch and ".pyc" not in patch  # noise excluded
    assert "binary" not in patch
