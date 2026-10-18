"""LOCUS-433 non-host-mutating pod workspace staging qualification."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from locus_runtime.pod_workspace_staging import (
    WorkspaceStagingDenied,
    staged_workspace,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"),
    reason="fd-relative O_NOFOLLOW staging is intentionally unsupported on this host",
)


def test_snapshot_skips_control_and_secret_material_without_host_mutation(tmp_path):
    src = tmp_path / "project"
    src.mkdir()
    (src / "main.py").write_text("print('safe')\n")
    (src / "src").mkdir()
    (src / "src" / "a.txt").write_text("ordinary\n")
    for protected in (".git", ".locus", ".ssh", ".aws", ".kube", ".gnupg", ".azure"):
        p = src / protected
        p.mkdir()
        (p / "canary.txt").write_text("credential-control-canary")
    (src / ".config").mkdir()
    (src / ".config" / "gcloud").mkdir()
    (src / ".config" / "gcloud" / "secret.json").write_text("credential-control-canary")
    (src / ".env").write_text("SECRET=canary")
    (src / "nested").mkdir()
    (src / "nested" / ".GIT").mkdir()
    (src / "nested" / ".GIT" / "config").write_text("canary")
    before = {p.relative_to(src).as_posix(): p.read_bytes()
              for p in src.rglob("*") if p.is_file()}
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        assert snapshot.files == 2
        assert snapshot.bytes == len(b"print('safe')\n") + len(b"ordinary\n")
        assert snapshot.excluded >= 10
        assert (snapshot.staged / "main.py").read_text() == "print('safe')\n"
        assert (snapshot.staged / "src" / "a.txt").read_text() == "ordinary\n"
        assert not (snapshot.staged / ".git").exists()
        assert not (snapshot.staged / ".env").exists()
        assert not (snapshot.staged / ".config" / "gcloud").exists()
        assert not (snapshot.staged / "nested" / ".GIT").exists()
        assert len(snapshot.sha256) == 64
        assert stat.S_IMODE(snapshot.staged.stat().st_mode) == 0o700
        staged_root = snapshot.staged
        (snapshot.staged / "main.py").write_text("changed only in stage")
        assert (src / "main.py").read_text() == "print('safe')\n"
    assert not staged_root.exists()
    assert {p.relative_to(src).as_posix(): p.read_bytes()
            for p in src.rglob("*") if p.is_file()} == before


def test_snapshot_manifest_stable_and_changes_with_source(tmp_path):
    src = tmp_path / "project"
    src.mkdir()
    (src / "a.txt").write_text("alpha")
    (src / "empty").mkdir()
    with staged_workspace(src, staging_parent=tmp_path) as first:
        digest = first.sha256
    with staged_workspace(src, staging_parent=tmp_path) as second:
        assert second.sha256 == digest
    (src / "a.txt").write_text("beta")
    with staged_workspace(src, staging_parent=tmp_path) as third:
        assert third.sha256 != digest


def test_snapshot_not_created_within_original_workspace(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    with pytest.raises(WorkspaceStagingDenied, match="outside workspace"):
        with staged_workspace(src, staging_parent=src):
            pass
    assert list(src.iterdir()) == []


@pytest.mark.parametrize("relative", [
    "unsafe-link", "nested/unsafe-link", ".git", "nested/.SSH",
])
def test_symlinks_never_followed_even_in_excluded_paths(tmp_path, relative):
    src = tmp_path / "workspace"
    src.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (external / "canary").write_text("outside")
    target = src / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(external, target_is_directory=True)
    with pytest.raises(WorkspaceStagingDenied, match="symlink denied"):
        with staged_workspace(src, staging_parent=tmp_path):
            pass
    assert (external / "canary").read_text() == "outside"
    assert not list(tmp_path.glob(".locus-pod-stage-*"))


def test_symlinked_ancestor_refused_before_staging(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(WorkspaceStagingDenied, match="unsafe ancestor"):
        with staged_workspace(alias, staging_parent=tmp_path):
            pass
    assert not list(tmp_path.glob(".locus-pod-stage-*"))


def test_hardlinks_denied_and_stage_cleaned(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    external = tmp_path / "outside"
    external.write_text("linked secret")
    os.link(external, src / "linked")
    with pytest.raises(WorkspaceStagingDenied, match="hardlinked"):
        with staged_workspace(src, staging_parent=tmp_path):
            pass
    assert external.read_text() == "linked secret"
    assert not list(tmp_path.glob(".locus-pod-stage-*"))


def test_bounds_fail_closed_and_cleanup(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "b.txt").write_text("world")
    for kwargs in ({"max_files": 1}, {"max_bytes": 7}, {"max_depth": 1}):
        if "max_depth" in kwargs:
            (src / "nested").mkdir(exist_ok=True)
            (src / "nested" / "deep").mkdir(exist_ok=True)
        with pytest.raises(WorkspaceStagingDenied, match="bounds|depth"):
            with staged_workspace(src, staging_parent=tmp_path, **kwargs):
                pass
        assert not list(tmp_path.glob(".locus-pod-stage-*"))
    for invalid in ({"max_files": True}, {"max_bytes": 0}, {"max_depth": -1}):
        with pytest.raises(WorkspaceStagingDenied, match="positive integer"):
            with staged_workspace(src, staging_parent=tmp_path, **invalid):
                pass


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_fifo_never_opened_or_copied(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    os.mkfifo(src / "fifo")
    with pytest.raises(WorkspaceStagingDenied, match="special"):
        with staged_workspace(src, staging_parent=tmp_path):
            pass
    assert not list(tmp_path.glob(".locus-pod-stage-*"))


def test_executable_mode_restricted_and_stage_is_transient(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    executable = src / "tool"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    regular = src / "data"
    regular.write_text("read-only")
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        assert stat.S_IMODE((snapshot.staged / "tool").stat().st_mode) == 0o700
        assert stat.S_IMODE((snapshot.staged / "data").stat().st_mode) == 0o600
        assert not snapshot.staged.is_relative_to(src)


def test_no_hidden_stage_after_caller_exception(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "file").write_text("hello")
    with pytest.raises(RuntimeError, match="simulated"):
        with staged_workspace(src, staging_parent=tmp_path):
            raise RuntimeError("simulated tool preparation failure")
    assert not list(tmp_path.glob(".locus-pod-stage-*"))


def test_reject_casefolded_sibling_aliases(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "Thing").write_text("one")
    (src / "thing").write_text("two")
    if len(list(src.iterdir())) < 2:
        pytest.skip("filesystem is case insensitive")
    with pytest.raises(WorkspaceStagingDenied, match="case-aliased sibling"):
        with staged_workspace(src, staging_parent=tmp_path):
            pass
    assert not list(tmp_path.glob(".locus-pod-stage-*"))



def test_empty_protected_paths_remain_absent_in_original(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "main.txt").write_text("content")
    before = tuple(p.name for p in src.iterdir())
    with staged_workspace(src, staging_parent=tmp_path) as snapshot:
        assert snapshot.files == 1
        assert not (src / ".git").exists()
        assert not (src / ".aws").exists()
        assert not (src / ".config").exists()
        assert not (snapshot.staged / ".git").exists()
    assert tuple(p.name for p in src.iterdir()) == before
    assert not list(tmp_path.glob(".locus-pod-stage-*"))


def test_too_many_directory_entries_bounded_before_staging(tmp_path):
    src = tmp_path / "workspace"
    src.mkdir()
    for name in ("a", "b", "c"):
        (src / name).write_text("tiny")
    # With one allowed file, no directory may even be fully inventoried when
    # it has more than two entries. The temporary staging root is cleaned.
    with pytest.raises(WorkspaceStagingDenied, match="resource bounds"):
        with staged_workspace(src, staging_parent=tmp_path, max_files=1):
            pass
    assert sorted(p.name for p in src.iterdir()) == ["a", "b", "c"]
    assert not list(tmp_path.glob(".locus-pod-stage-*"))
