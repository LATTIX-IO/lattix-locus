"""Read-only folders for the RSI exam (LOCUS-351, LOCUS-382). Stdlib only.

The sealed suite store (``apps/evals/locus_evals/suite/store.py``) and the
synced private held-out split (:mod:`locus_tooling.evals_sync`) are installed
the same way: copied into a staging folder next to the destination, renamed
into place, then every file made read-only (and, on POSIX, every folder). Both
live under ``<app_home>/evals/``, a folder no jail grants: the candidate and the
tool jail cannot read them (LOCUS-379 grants only their temp home and
workspace), and a same-user change is caught by hash verification.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def make_writable(root: Path) -> None:
    """Undo :func:`make_read_only` (before replacing a damaged install)."""
    for path in [root, *root.rglob("*")]:
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0))
        except OSError:
            pass


def make_read_only(root: Path) -> None:
    """Every file read-only; folders too where the OS honours it (not Windows)."""
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            os.chmod(path, stat.S_IREAD)
        elif path.is_dir() and sys.platform != "win32":
            os.chmod(path, stat.S_IREAD | stat.S_IEXEC)
    if sys.platform != "win32":
        os.chmod(root, stat.S_IREAD | stat.S_IEXEC)


def is_writable(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def force_rmtree(path: Path) -> None:
    """Remove ``path`` even when it holds read-only files (sealed installs, git objects)."""

    def force(func: Callable[..., Any], target: str, _exc: BaseException) -> None:
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            parent = os.path.dirname(target)
            if parent:
                os.chmod(parent, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(target)
        except OSError:
            pass

    if path.exists() or path.is_symlink():
        shutil.rmtree(path, onexc=force)
