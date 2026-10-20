"""Read-only, fail-closed inspection of a disposable pod workspace.

This is NOT artifact approval or promotion. Call only from trusted supervisor
code after independently authenticating StageLease and source snapshot.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from locus_runtime.pod_workspace_staging import (
    WorkspaceStagingDenied,
    _open_anchored_directory,
    _assert_parent_outside_workspace,
    _excluded_name,
    _require_no_follow,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_BYTES,
)


@dataclass(frozen=True, slots=True)
class ArtifactChange:
    path: str
    kind: str
    before_sha256: str | None
    after_sha256: str | None
    before_mode: int | None = None
    after_mode: int | None = None


@dataclass(frozen=True, slots=True)
class ArtifactInspection:
    changes: tuple[ArtifactChange, ...]
    source_files: int
    stage_files: int


@dataclass(frozen=True, slots=True)
class _FileInfo:
    digest: str
    mode: int
    expected_stage_mode: int


def _inventory(root: Path, *, max_files: int, max_bytes: int, max_depth: int, source: bool = False) -> dict[str, _FileInfo]:
    """Hash regular files and safe mode bits under anchored file descriptors."""
    base = _open_anchored_directory(root)
    result: dict[str, _FileInfo] = {}
    total_bytes = 0
    entries = 0

    def visit(fd: int, parents: tuple[str, ...]) -> None:
        nonlocal total_bytes, entries
        if len(parents) > max_depth:
            raise WorkspaceStagingDenied("artifact tree exceeds maximum depth")
        names: list[str] = []
        with os.scandir(fd) as scan:
            for entry in scan:
                if entries + len(names) >= max_files * 2:
                    raise WorkspaceStagingDenied("artifact entry budget exceeded")
                names.append(entry.name)
        names.sort(key=os.fsencode)
        folded: set[str] = set()
        for name in names:
            entries += 1
            if entries > max_files * 2 or name in (".", "..") or any(c in name for c in ("\x00", "\n", "\r", ",")):
                raise WorkspaceStagingDenied("invalid or excessive artifact entries")
            if name.casefold() in folded:
                raise WorkspaceStagingDenied("artifact paths collide under case folding")
            folded.add(name.casefold())
            if _excluded_name(name, parents):
                if source:
                    excluded = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(excluded.st_mode):
                        raise WorkspaceStagingDenied("source credential/control symlink denied")
                    continue
                raise WorkspaceStagingDenied("protected credential/control path present")
            parts = (*parents, name)
            if len(parts) > max_depth:
                raise WorkspaceStagingDenied("artifact depth bound exceeded")
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    current = os.fstat(child)
                    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise WorkspaceStagingDenied("artifact directory changed")
                    visit(child, parts)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1 or len(result) >= max_files:
                    raise WorkspaceStagingDenied("artifact file bounds or hardlink violation")
                if stat.S_IMODE(metadata.st_mode) & 0o7000:
                    raise WorkspaceStagingDenied("special artifact mode bits forbidden")
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    current = os.fstat(file_fd)
                    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino) or current.st_nlink != 1:
                        raise WorkspaceStagingDenied("artifact file replaced")
                    total_bytes += current.st_size
                    if total_bytes > max_bytes:
                        raise WorkspaceStagingDenied("artifact byte bound exceeded")
                    digest = hashlib.sha256()
                    read = 0
                    while chunk := os.read(file_fd, 262144):
                        digest.update(chunk)
                        read += len(chunk)
                        if read > current.st_size or total_bytes - current.st_size + read > max_bytes:
                            raise WorkspaceStagingDenied("artifact changed or exceeds byte limit")
                    after = os.fstat(file_fd)
                    if read != current.st_size or (
                        current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns
                    ) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
                    ):
                        raise WorkspaceStagingDenied("artifact mutated during inspection")
                    mode = stat.S_IMODE(after.st_mode)
                    result["/".join(parts)] = _FileInfo(
                        digest.hexdigest(), mode,
                        0o700 if mode & 0o111 else 0o600,
                    )
                finally:
                    os.close(file_fd)
            else:
                raise WorkspaceStagingDenied("symlink or special artifact entry denied")

    try:
        visit(base, ())
        return result
    except OSError as exc:
        raise WorkspaceStagingDenied("artifact inspection cannot safely traverse tree") from exc
    finally:
        os.close(base)


def inspect_staged_artifacts(
    source: str | Path, staged: str | Path, *,
    allowed_output_paths: frozenset[str],
    expected_source_sha256: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> ArtifactInspection:
    """Report staged deltas, never write, approve, or promote them.

    Exact relative output filenames are supervisor-supplied. The separate
    StageLease validator MUST run before this inspection. An expected source
    digest uses a canonical manifest produced by this same inspector; it is
    not interchangeable with WorkspaceSnapshot.sha256.
    """
    _require_no_follow()
    if any(type(v) is not int or v < 1 for v in (max_files, max_bytes, max_depth)):
        raise WorkspaceStagingDenied("invalid artifact budget")
    if not isinstance(allowed_output_paths, frozenset) or any(
        not isinstance(p, str) or not p or p.startswith("/") or
        "\\" in p or any(x in (".", "..", "") for x in p.split("/"))
        for p in allowed_output_paths
    ):
        raise WorkspaceStagingDenied("invalid exact output allowlist")
    if any(_excluded_name(part, tuple(p.split("/")[:i]))
           for p in allowed_output_paths for i, part in enumerate(p.split("/"))):
        raise WorkspaceStagingDenied("protected paths cannot be approved")
    src, dst = Path(source), Path(staged)
    if not src.is_absolute() or not dst.is_absolute() or dst.is_relative_to(src):
        raise WorkspaceStagingDenied("source and stage must be isolated absolute paths")
    source_fd = _open_anchored_directory(src)
    stage_fd = _open_anchored_directory(dst)
    try:
        source_stat, stage_stat = os.fstat(source_fd), os.fstat(stage_fd)
        if (source_stat.st_dev, source_stat.st_ino) == (stage_stat.st_dev, stage_stat.st_ino):
            raise WorkspaceStagingDenied("source/stage alias")
        _assert_parent_outside_workspace(source_fd, stage_fd)
        if stage_stat.st_uid != os.getuid() or stat.S_IMODE(stage_stat.st_mode) != 0o700:
            raise WorkspaceStagingDenied("stage must be private and supervisor owned")
        before = _inventory(src, max_files=max_files, max_bytes=max_bytes, max_depth=max_depth, source=True)
        after = _inventory(dst, max_files=max_files, max_bytes=max_bytes, max_depth=max_depth)
        if expected_source_sha256 is not None:
            canonical = hashlib.sha256()
            for path, info in sorted(before.items()):
                canonical.update(path.encode("utf-8") + b"\x00" + bytes.fromhex(info.digest))
            if canonical.hexdigest() != expected_source_sha256:
                raise WorkspaceStagingDenied("original source inventory drifted")
        changes = tuple(
            ArtifactChange(
                path=path,
                kind="added" if path not in before else "deleted" if path not in after else "modified",
                before_sha256=before[path].digest if path in before else None,
                after_sha256=after[path].digest if path in after else None,
                before_mode=before[path].mode if path in before else None,
                after_mode=after[path].mode if path in after else None,
            )
            for path in sorted(set(before) | set(after))
            if (path not in before or path not in after
                or before[path].digest != after[path].digest
                or before[path].expected_stage_mode != after[path].mode)
        )
        if any(change.path not in allowed_output_paths for change in changes):
            raise WorkspaceStagingDenied("artifact outside explicitly approved output scope")
        if (os.fstat(source_fd).st_dev, os.fstat(source_fd).st_ino) != (source_stat.st_dev, source_stat.st_ino):
            raise WorkspaceStagingDenied("source root was substituted")
        if (os.fstat(stage_fd).st_dev, os.fstat(stage_fd).st_ino) != (stage_stat.st_dev, stage_stat.st_ino):
            raise WorkspaceStagingDenied("stage root was substituted")
        return ArtifactInspection(changes, len(before), len(after))
    finally:
        os.close(stage_fd)
        os.close(source_fd)
