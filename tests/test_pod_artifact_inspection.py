"""Regression checks for the read-only artifact preflight (no Docker authority)."""
import os
from pathlib import Path

import pytest

from locus_runtime.pod_artifact_inspection import inspect_staged_artifacts
from locus_runtime.pod_workspace_staging import WorkspaceStagingDenied, staged_workspace


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="POSIX anchored descriptor checks required",
)


def test_staged_change_is_reported_but_original_untouched(tmp_path: Path) -> None:
    original = tmp_path / "source"
    original.mkdir()
    (original / "code.txt").write_text("before", encoding="utf-8")
    (original / ".git").mkdir()
    (original / ".git" / "config").write_text("private", encoding="utf-8")
    with staged_workspace(original, staging_parent=tmp_path) as snapshot:
        (snapshot.staged / "code.txt").write_text("after", encoding="utf-8")
        result = inspect_staged_artifacts(
            original, snapshot.staged,
            allowed_output_paths=frozenset({"code.txt"}),
        )
        assert len(result.changes) == 1
        assert result.changes[0].path == "code.txt"
        assert result.changes[0].kind == "modified"
        assert (original / "code.txt").read_text() == "before"
        assert not (snapshot.staged / ".git").exists()


@pytest.mark.parametrize("unsafe", [".aws", ".git", ".config", ".locus", "private.key"])
def test_new_protected_stage_paths_are_rejected(tmp_path: Path, unsafe: str) -> None:
    original = tmp_path / "source"
    original.mkdir()
    with staged_workspace(original, staging_parent=tmp_path) as snapshot:
        (snapshot.staged / unsafe).write_text("secret", encoding="utf-8")
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(
                original, snapshot.staged,
                allowed_output_paths=frozenset({unsafe}),
            )


def test_unscoped_change_rejected(tmp_path: Path) -> None:
    original = tmp_path / "source"
    original.mkdir()
    (original / "source.txt").write_text("a", encoding="utf-8")
    with staged_workspace(original, staging_parent=tmp_path) as snapshot:
        (snapshot.staged / "source.txt").write_text("b", encoding="utf-8")
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(
                original, snapshot.staged,
                allowed_output_paths=frozenset({"other.txt"}),
            )


def test_stage_symlink_and_hardlink_denied(tmp_path: Path) -> None:
    original = tmp_path / "source"
    original.mkdir()
    (original / "code.txt").write_text("a", encoding="utf-8")
    with staged_workspace(original, staging_parent=tmp_path) as snapshot:
        (snapshot.staged / "link").symlink_to(original / "code.txt")
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(original, snapshot.staged, allowed_output_paths=frozenset())
        (snapshot.staged / "link").unlink()
        os.link(original / "code.txt", snapshot.staged / "link")
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(original, snapshot.staged, allowed_output_paths=frozenset())


def test_no_source_path_alias_and_traversal_allowlist(tmp_path: Path) -> None:
    original = tmp_path / "source"
    original.mkdir()
    with pytest.raises(WorkspaceStagingDenied):
        inspect_staged_artifacts(original, original, allowed_output_paths=frozenset())
    with staged_workspace(original, staging_parent=tmp_path) as snapshot:
        with pytest.raises(WorkspaceStagingDenied):
            inspect_staged_artifacts(
                original, snapshot.staged,
                allowed_output_paths=frozenset({"../escape"}),
            )
