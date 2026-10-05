"""Host git over an agent-writable working copy: no hooks, sealed git metadata."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from locus_runtime.harness.executor import ExecResult
from locus_runtime.harness.workspace import Workspace
from locus_runtime.loop_runner.delivery import (
    DeliveryError,
    GitOps,
    HostWorkspaceGit,
    git_metadata_digest,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture()
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "src"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.name", "Locus Test")
    _git(repo, "config", "user.email", "locus-test@example.invalid")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "init")
    _git(repo, "remote", "add", "origin", str(repo))
    return repo


@pytest.fixture()
def worktree(tmp_path: Path, source_repo: Path) -> Path:
    path = tmp_path / "worktrees" / "run-1"
    GitOps().add_worktree(source_repo, path, "loop/t-1", "main", remote="origin")
    return path


def test_provisioning_seals_outside_the_working_copy(worktree: Path) -> None:
    seal = worktree.parent / "run-1.gitseal"
    assert seal.is_file()
    assert seal.read_text(encoding="utf-8") == git_metadata_digest(worktree)
    assert not (worktree / ".gitseal").exists()


def test_normal_delivery_passes_the_seal(worktree: Path) -> None:
    git = GitOps()
    (worktree / "a.txt").write_text("two\n", encoding="utf-8")
    assert git.changed_paths(worktree) == ["a.txt"]
    sha = git.commit_all(worktree, "change")
    assert len(sha) == 40
    # rev-parse and diff after the commit still pass: commit does not touch config.
    git.run(worktree, "diff", "HEAD")


def test_tampered_config_is_refused(worktree: Path) -> None:
    with (worktree / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write('[filter "x"]\n\tclean = echo pwned\n')
    with pytest.raises(DeliveryError, match="workspace_git_tampered"):
        GitOps().changed_paths(worktree)


def test_planted_hook_is_refused(worktree: Path) -> None:
    hooks = worktree / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    with pytest.raises(DeliveryError, match="workspace_git_tampered"):
        GitOps().commit_all(worktree, "change")


def test_alternates_injection_is_refused(worktree: Path, tmp_path: Path) -> None:
    (worktree / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (worktree / ".git" / "objects" / "info" / "alternates").write_text(
        str(tmp_path / "elsewhere" / "objects") + "\n", encoding="utf-8"
    )
    with pytest.raises(DeliveryError, match="workspace_git_tampered"):
        GitOps().has_changes(worktree)


def test_hooks_never_run_on_the_host_even_unsealed(tmp_path: Path, source_repo: Path) -> None:
    # An unsealed repository (no seal file): the hardening flags alone keep hooks off.
    # post-commit is not skipped by --no-verify, so this exercises core.hooksPath.
    marker = tmp_path / "hook-ran"
    hook = source_repo / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    # Positive control: plain git does run the hook in this environment.
    (source_repo / "a.txt").write_text("three\n", encoding="utf-8")
    _git(source_repo, "commit", "--quiet", "-am", "control")
    assert marker.exists()
    marker.unlink()

    (source_repo / "a.txt").write_text("four\n", encoding="utf-8")
    GitOps().commit_all(source_repo, "change")
    assert not marker.exists()


def test_remove_worktree_drops_the_seal(worktree: Path, source_repo: Path) -> None:
    GitOps().remove_worktree(source_repo, worktree)
    assert not worktree.exists()
    assert not (worktree.parent / "run-1.gitseal").exists()


# --------------------------------------------------------------------------- #
# Host-side workspace diff for the verify gate (LOCUS-362)
# --------------------------------------------------------------------------- #
class _NoGitInJail:
    """An executor where git cannot run, like the Windows AppContainer."""

    backend = "windows-appcontainer"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []

    def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult:
        self.calls.append(script)
        return ExecResult(
            128, "", "fatal: Unable to read current working directory: Permission denied", 0.0
        )

    def workdir(self) -> str:
        return str(self.root)


def test_host_diff_does_not_depend_on_git_in_the_jail(worktree: Path) -> None:
    (worktree / "a.txt").write_text("two\n", encoding="utf-8")
    (worktree / "b.txt").write_text("new\n", encoding="utf-8")
    (worktree / "__pycache__").mkdir()
    (worktree / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\x00junk")
    jail = _NoGitInJail(worktree)
    workspace = Workspace(
        run_id="run-1",
        executor=jail,  # type: ignore[arg-type]
        base_ref="HEAD",
        host_git=HostWorkspaceGit(GitOps(), worktree),
    )

    diff = workspace.diff()

    assert "+two" in diff and "-one" in diff
    assert "b/b.txt" in diff and "+new" in diff
    assert "__pycache__" not in diff and ".pyc" not in diff
    assert workspace.has_uncommitted_changes() is True
    assert jail.calls == []  # nothing ran git through the jail
    # Without host_git the same jail yields the empty diff the bake-off saw.
    assert Workspace(run_id="r", executor=jail).diff() == ""  # type: ignore[arg-type]


def test_host_diff_refuses_a_tampered_working_copy(worktree: Path) -> None:
    (worktree / "a.txt").write_text("two\n", encoding="utf-8")
    with (worktree / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write('[diff "x"]\n\ttextconv = echo pwned\n')
    workspace = Workspace(
        run_id="run-1",
        executor=_NoGitInJail(worktree),  # type: ignore[arg-type]
        host_git=HostWorkspaceGit(GitOps(), worktree),
    )
    with pytest.raises(DeliveryError, match="workspace_git_tampered"):
        workspace.diff()


def test_host_diff_runs_no_diff_driver_named_by_the_working_copy(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A textconv / external diff driver the *user's* git config defines (simulated
    # through GIT_CONFIG_*), selected by an agent-written .gitattributes.
    marker = tmp_path / "driver-ran"
    command = f'sh -c \'touch "{marker.as_posix()}"; cat "$1"\' -'
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.evil.textconv")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", command)
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "diff.evil.command")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", command)
    (worktree / ".gitattributes").write_text("*.txt diff=evil\n", encoding="utf-8")
    (worktree / "a.txt").write_text("two\n", encoding="utf-8")
    # Positive control: plain git diff does run the driver in this environment.
    _git(worktree, "diff", "HEAD")
    assert marker.exists()
    marker.unlink()

    diff = GitOps().diff(worktree, "HEAD")

    assert "+two" in diff
    assert not marker.exists()


def test_host_diff_rejects_option_like_arguments(worktree: Path) -> None:
    with pytest.raises(DeliveryError, match="option"):
        GitOps().diff(worktree, "--output=/tmp/x")
    with pytest.raises(DeliveryError, match="option"):
        GitOps().diff(worktree, "HEAD", ["--no-index"])
