"""Host git over an agent-writable working copy: no hooks, sealed git metadata."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from locus_runtime.loop_runner.delivery import DeliveryError, GitOps, git_metadata_digest

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
