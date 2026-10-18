"""LOCUS-433: bounded, non-mutating snapshot for an ephemeral local pod.

This is trusted supervisor preparation only, NOT an executable container
mount or authority to promote modified files into the principal's workspace.
Never hand the source file descriptors or this builder to an untrusted worker.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from locus_runtime.sandbox import ALWAYS_READONLY_SUBPATHS

DEFAULT_MAX_FILES = 4096
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_DEPTH = 24
_CHUNK_BYTES = 256 * 1024
_BLOCKED_COMPONENTS = frozenset(
    part.casefold() for part in ALWAYS_READONLY_SUBPATHS if "/" not in part
)
_SECRET_NAMES = frozenset({
    ".env", ".envrc", ".netrc", ".npmrc", ".pypirc", ".git-credentials",
    "id_rsa", "id_ed25519", "secrets.json", "secrets.yaml",
})
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)


class WorkspaceStagingDenied(PermissionError):
    """The supervisor cannot safely snapshot this untrusted workspace."""


class _Hasher(Protocol):
    def update(self, data: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """An immutable *initial-state receipt*, not a post-execution artifact."""

    source: Path
    staged: Path
    sha256: str
    files: int
    bytes: int
    excluded: int


@dataclass(slots=True)
class _Limits:
    max_files: int
    max_bytes: int
    max_depth: int
    files: int = 0
    bytes: int = 0
    excluded: int = 0
    entries: int = 0


def _require_no_follow() -> None:
    # A pathname-based test followed by open() is a symlink TOCTOU on Windows.
    # Deny unsupported hosts until a handle-based Windows staging backend ships.
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise WorkspaceStagingDenied("handle-safe workspace staging unavailable on this platform")


def _components(path: Path) -> tuple[str, ...]:
    if not path.is_absolute():
        raise WorkspaceStagingDenied("workspace paths must be absolute")
    parts = path.parts
    if ".." in parts or any(
        any(c in part for c in ("\x00", "\n", "\r", ","))
        for part in parts
    ):
        raise WorkspaceStagingDenied("unsafe workspace pathname")
    return parts


def _open_anchored_directory(path: Path) -> int:
    """Open every directory component relative to a pinned parent FD.

    No component may traverse a symlink, including an ancestor of the root.
    Keep the returned descriptor open while reading the entire snapshot.
    """
    _require_no_follow()
    _components(path)
    fd = os.open("/", _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(
                component, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise WorkspaceStagingDenied("source root is not a directory")
        return fd
    except (OSError, ValueError) as exc:
        os.close(fd)
        raise WorkspaceStagingDenied("workspace contains an unsafe ancestor") from exc


def _excluded_name(name: str, parents: tuple[str, ...]) -> bool:
    folded = name.casefold()
    # Treat control paths at every depth as protected. Match case variants even
    # on case-sensitive hosts, so moving to Windows never exposes a control tree.
    if folded in _BLOCKED_COMPONENTS or (
        folded == "gcloud" and parents and parents[-1].casefold() == ".config"
    ):
        return True
    if folded in _SECRET_NAMES or folded.startswith(".env.") or folded.startswith("credentials."):
        return True
    if folded.endswith((".pem", ".p12", ".pfx", ".key")):
        return True
    return False


def _hash_path(hasher: _Hasher, kind: bytes, parts: tuple[str, ...]) -> None:
    try:
        raw = "/".join(parts).encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise WorkspaceStagingDenied("non-UTF8 workspace path denied") from exc
    hasher.update(kind + len(raw).to_bytes(4, "big") + raw)


def _copy_file(
    parent_fd: int, name: str, dst: Path,
    before: os.stat_result, parts: tuple[str, ...],
    limits: _Limits, manifest: _Hasher,
) -> None:
    if before.st_nlink != 1:
        raise WorkspaceStagingDenied("hardlinked files cannot enter a pod snapshot")
    if limits.files >= limits.max_files or limits.bytes + before.st_size > limits.max_bytes:
        raise WorkspaceStagingDenied("workspace snapshot resource bounds exceeded")
    flags = _FILE_FLAGS | os.O_NOFOLLOW
    try:
        src = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise WorkspaceStagingDenied("unsafe workspace file open") from exc
    try:
        opened = os.fstat(src)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_nlink != 1
        ):
            raise WorkspaceStagingDenied("workspace entry changed during snapshot")
        sha = hashlib.sha256()
        copied = 0
        output_mode = 0o700 if opened.st_mode & 0o111 else 0o600
        # This destination is inside a private, newly created 0700 tree.
        dst_fd = os.open(
            dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            output_mode,
        )
        try:
            while True:
                chunk = os.read(src, _CHUNK_BYTES)
                if not chunk:
                    break
                copied += len(chunk)
                if limits.bytes + copied > limits.max_bytes:
                    raise WorkspaceStagingDenied("workspace snapshot resource bounds exceeded")
                sha.update(chunk)
                pending = memoryview(chunk)
                while pending:
                    written = os.write(dst_fd, pending)
                    if written <= 0:
                        raise WorkspaceStagingDenied("snapshot file write failed")
                    pending = pending[written:]
        finally:
            os.close(dst_fd)
        after = os.fstat(src)
        if (
            copied != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise WorkspaceStagingDenied("workspace file changed during copy")
        _hash_path(manifest, b"F", parts)
        manifest.update(bytes.fromhex(sha.hexdigest()))
        limits.files += 1
        limits.bytes += copied
    finally:
        os.close(src)


def _scan(
    parent_fd: int, dst: Path, parents: tuple[str, ...],
    limits: _Limits, manifest: _Hasher,
) -> None:
    if len(parents) > limits.max_depth:
        raise WorkspaceStagingDenied("workspace snapshot depth exceeded")
    try:
        names: list[str] = []
        with os.scandir(parent_fd) as it:
            for entry in it:
                # Bound the inventory *before* sorting or allocating the full
                # directory: a single giant directory must not exhaust memory.
                if limits.entries + len(names) >= limits.max_files * 2:
                    raise WorkspaceStagingDenied(
                        "workspace snapshot resource bounds exceeded"
                    )
                names.append(entry.name)
        names.sort(key=os.fsencode)
    except OSError as exc:
        raise WorkspaceStagingDenied("workspace directory cannot be enumerated") from exc
    folded: set[str] = set()
    for name in names:
        limits.entries += 1
        if name.casefold() in folded:
            raise WorkspaceStagingDenied("case-aliased sibling in workspace")
        folded.add(name.casefold())
        if any(c in name for c in ("\x00", "\n", "\r", ",")) or name in {".", ".."}:
            raise WorkspaceStagingDenied("unsafe workspace entry name")
        parts = (*parents, name)
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise WorkspaceStagingDenied("workspace entry changed during snapshot") from exc
        if stat.S_ISLNK(info.st_mode):
            # Even excluded credential symlinks are rejected, never resolved.
            raise WorkspaceStagingDenied("workspace symlink denied")
        if _excluded_name(name, parents):
            limits.excluded += 1
            continue
        if stat.S_ISDIR(info.st_mode):
            if len(parts) > limits.max_depth:
                raise WorkspaceStagingDenied("workspace snapshot depth exceeded")
            try:
                child_fd = os.open(
                    name, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=parent_fd,
                )
            except OSError as exc:
                raise WorkspaceStagingDenied("workspace directory changed during snapshot") from exc
            try:
                child_stat = os.fstat(child_fd)
                if (child_stat.st_dev, child_stat.st_ino) != (info.st_dev, info.st_ino):
                    raise WorkspaceStagingDenied("workspace directory changed during snapshot")
                (dst / name).mkdir(mode=0o700)
                _hash_path(manifest, b"D", parts)
                _scan(child_fd, dst / name, parts, limits, manifest)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            _copy_file(parent_fd, name, dst / name, info, parts, limits, manifest)
        else:
            # FIFOs, sockets, devices, and platform-specific reparse objects.
            raise WorkspaceStagingDenied("special workspace entries are prohibited")


@contextmanager
def staged_workspace(
    source: str | Path,
    *,
    staging_parent: str | Path | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Iterator[WorkspaceSnapshot]:
    """Build an isolated snapshot and delete it on every exit path.

    This function is intentionally not wired to a Docker launcher. Any
    container mount, agent execution, artifact publication or promotion
    requires a separate trusted integration and operating-system acceptance.
    """
    if any(type(v) is not int or v < 1 for v in (max_files, max_bytes, max_depth)):
        raise WorkspaceStagingDenied("positive integer staging limits required")
    _require_no_follow()
    root = Path(source).expanduser().absolute()
    parent = Path(staging_parent or tempfile.gettempdir()).expanduser().absolute()
    _components(root)
    _components(parent)
    if parent.is_relative_to(root):
        raise WorkspaceStagingDenied("staging parent must be outside workspace")
    # Verify the staging destination root is a real, non-symlink directory.
    parent_fd = _open_anchored_directory(parent)
    os.close(parent_fd)
    source_fd = _open_anchored_directory(root)
    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".locus-pod-stage-", dir=parent))
        os.chmod(stage, 0o700)
        limits = _Limits(max_files, max_bytes, max_depth)
        manifest = hashlib.sha256(b"locus.workspace-snapshot.v1\x00")
        _scan(source_fd, stage, (), limits, manifest)
        yield WorkspaceSnapshot(
            source=root, staged=stage, sha256=manifest.hexdigest(),
            files=limits.files, bytes=limits.bytes, excluded=limits.excluded,
        )
    finally:
        os.close(source_fd)
        if stage is not None:
            shutil.rmtree(stage)
