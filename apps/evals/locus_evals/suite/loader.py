"""Load and digest suite tasks (LOCUS-351)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

import yaml

from locus_evals.suite.model import SPLITS, SuiteTask


class SuiteError(ValueError):
    """The suite on disk is malformed (bad YAML, schema, duplicate ids, split mismatch)."""


def task_files(tasks_dir: Path, split: str) -> list[Path]:
    root = Path(tasks_dir) / split
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.yaml") if p.is_file())


def load_task(path: Path, split: str) -> SuiteTask:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SuiteError(f"{path.name}: unreadable task file ({type(exc).__name__})") from exc
    if not isinstance(raw, dict):
        raise SuiteError(f"{path.name}: a task file must be a mapping")
    raw.setdefault("split", split)
    try:
        task = SuiteTask.model_validate(raw)
    except ValueError as exc:
        raise SuiteError(f"{path.name}: {exc}") from exc
    if task.split != split:
        raise SuiteError(f"{path.name}: declares split {task.split!r} but lives in {split}/")
    if path.stem != task.id:
        raise SuiteError(f"{path.name}: file name must equal the task id {task.id!r}")
    return task


def load_tasks(tasks_dir: Path, splits: Iterable[str] = SPLITS) -> list[SuiteTask]:
    """Every task of ``splits`` (sorted by split order, then id); ids are unique suite-wide."""
    tasks: list[SuiteTask] = []
    for split in splits:
        if split not in SPLITS:
            raise SuiteError(f"unknown split {split!r}")
        tasks.extend(load_task(path, split) for path in task_files(tasks_dir, split))
    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            raise SuiteError(f"duplicate task id {task.id!r}")
        seen.add(task.id)
    return tasks


def content_digest(path: Path) -> str:
    """sha256 of the file with CRLF normalized to LF (the same on every OS)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def split_digest(tasks_dir: Path, split: str) -> str:
    """sha256 over the split's task files (name + content), independent of order and
    line endings: it identifies the suite across machines (scorecard comparability),
    unlike the store manifest, which seals the exact bytes of one install."""
    h = hashlib.sha256()
    for path in task_files(tasks_dir, split):
        h.update(f"{split}/{path.name}\0{content_digest(path)}\n".encode())
    return h.hexdigest()
