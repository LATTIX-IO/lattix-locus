"""The read-only, hash-verified suite store the evaluator runs from (LOCUS-351).

At run time the task files (dev and held-out tasks and their graders) are copied
from the trusted sources -- the dev split from the runner's own checkout, the
held-out split from the private, synced folder (LOCUS-382,
:func:`locus_tooling.evals_sync.resolve_heldout`: ``LOCUS_EVAL_HELDOUT_DIR``,
else ``<app_home>/evals/heldout/<digest>/``) -- to
``<app_home>/evals/suite-store/<digest>/`` with a
``MANIFEST.json`` of sha256 hashes, and the files are made read-only. The
store is outside every agent's write root (eval runs get a temp workspace; the
loop's agent gets its working copy) and outside every eval agent's read roots.

The evaluator keeps the manifest it computed at install time **in memory**
(:class:`SealedSuite`) and calls :func:`verify` before and after every sample:
a changed, added, removed or re-writable file raises :class:`TamperError` and
fails the whole run (scorecard status ``tampered``, never promoted). Re-reading
the on-disk ``MANIFEST.json`` is never trusted for verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from locus_runtime.rsi.readonly import is_writable, make_read_only, make_writable
from locus_tooling.evals_heldout import HELDOUT_DIR_ENV
from locus_tooling.evals_sync import HeldoutResolution, resolve_heldout

from locus_evals.suite import SUITE_VERSION
from locus_evals.suite.loader import load_tasks, split_digest, task_files
from locus_evals.suite.model import SPLITS

MANIFEST = "MANIFEST.json"
HELDOUT_ENV = HELDOUT_DIR_ENV


class TamperError(RuntimeError):
    """The suite store no longer matches the manifest taken at install time."""


@dataclass(frozen=True)
class SealedSuite:
    root: Path
    digest: str
    files: Mapping[str, str] = field(default_factory=dict)
    split_digests: Mapping[str, str] = field(default_factory=dict)
    version: str = SUITE_VERSION
    #: Where the held-out split came from (LOCUS-382); ``origin == "none"`` means
    #: it was not available and the store holds the dev split only.
    heldout: HeldoutResolution = field(
        default_factory=lambda: HeldoutResolution(None, "none", reason="not resolved")
    )

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_of(tasks_root: Path) -> dict[str, str]:
    """``{tasks/<split>/<file>: sha256}`` of every file under ``tasks_root``."""
    out: dict[str, str] = {}
    if not tasks_root.is_dir():
        return out
    for path in sorted(tasks_root.rglob("*")):
        if path.is_symlink():
            out[f"tasks/{path.relative_to(tasks_root).as_posix()}"] = "symlink"
        elif path.is_file():
            out[f"tasks/{path.relative_to(tasks_root).as_posix()}"] = _sha(path)
    return out


def digest_of(files: Mapping[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(f"{name}\0{files[name]}\n".encode())
    return h.hexdigest()


def default_store_root() -> Path:
    """``<app_home>/evals/suite-store`` of the evaluator (the trusted side)."""
    from locus_runtime.win_toolchain import toolchain_app_home

    return toolchain_app_home() / "evals" / "suite-store"


def source_dirs(
    tasks_dir: Path, heldout_dir: Path | None = None
) -> tuple[dict[str, Path], HeldoutResolution]:
    """Where each split comes from: ``dev`` from the suite's ``tasks/dev``; ``heldout``
    only from a private source (LOCUS-382): ``heldout_dir``, else
    ``LOCUS_EVAL_HELDOUT_DIR``, else the synced folder. The repository holds no
    held-out tasks, and a ``tasks/heldout`` folder there is ignored, so a public
    copy can never stand in for the private split."""
    resolution = resolve_heldout(explicit=heldout_dir)
    dirs = {"dev": Path(tasks_dir) / "dev"}
    if resolution.path is not None:
        dirs["heldout"] = resolution.path
    return dirs, resolution


_make_writable = make_writable
_make_read_only = make_read_only
_writable = is_writable


def install(
    tasks_dir: Path,
    store_root: Path | None = None,
    *,
    heldout_dir: Path | None = None,
) -> SealedSuite:
    """Copy the suite into the read-only store and seal it (idempotent per digest)."""
    sources, heldout = source_dirs(tasks_dir, heldout_dir)
    staged_files: dict[str, Path] = {}
    for split, src in sources.items():
        for path in task_files(src, "."):
            staged_files[f"tasks/{split}/{path.name}"] = path
    if not staged_files:
        raise TamperError(f"no task files found under {tasks_dir}")
    files = {name: _sha(path) for name, path in staged_files.items()}
    digest = digest_of(files)
    base = Path(store_root or default_store_root())
    base.mkdir(parents=True, exist_ok=True)
    dest = base / digest[:20]
    if dest.exists():
        if manifest_of(dest / "tasks") == files and all(
            not _writable(dest / name) for name in files
        ):
            return _sealed(dest, digest, files, heldout)
        _make_writable(dest)
        shutil.rmtree(dest)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=base))
    try:
        for name, path in staged_files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        if manifest_of(staging / "tasks") != files:
            raise TamperError("the suite source changed while it was being copied")
        load_tasks(staging / "tasks")  # schema-valid before it is sealed
        (staging / MANIFEST).write_text(
            json.dumps(
                {"version": SUITE_VERSION, "digest": digest, "files": files},
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _make_read_only(dest)
    return _sealed(dest, digest, files, heldout)


def _sealed(
    dest: Path, digest: str, files: Mapping[str, str], heldout: HeldoutResolution
) -> SealedSuite:
    tasks = dest / "tasks"
    return SealedSuite(
        root=dest,
        digest=digest,
        files=dict(files),
        # Only splits that have tasks: an absent held-out split has no digest
        # (it can never look comparable to a baseline's).
        split_digests={
            split: split_digest(tasks, split) for split in SPLITS if task_files(tasks, split)
        },
        heldout=heldout,
    )


def verify(sealed: SealedSuite) -> None:
    """Raise :class:`TamperError` unless the store still matches the sealed manifest."""
    actual = manifest_of(sealed.tasks_dir)
    expected = dict(sealed.files)
    if actual != expected:
        changed = sorted(n for n in expected if n in actual and actual[n] != expected[n])
        missing = sorted(set(expected) - set(actual))
        added = sorted(set(actual) - set(expected))
        raise TamperError(
            "suite store tampered: "
            + "; ".join(
                part
                for part in (
                    f"changed {changed[:5]}" if changed else "",
                    f"missing {missing[:5]}" if missing else "",
                    f"added {added[:5]}" if added else "",
                )
                if part
            )
        )
    writable = sorted(n for n in expected if _writable(sealed.root / n))
    if writable:
        raise TamperError(f"suite store files became writable: {writable[:5]}")
    if digest_of(actual) != sealed.digest:
        raise TamperError("suite store digest mismatch")
