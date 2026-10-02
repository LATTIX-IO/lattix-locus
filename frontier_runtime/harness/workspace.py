"""A workspace = an executor bound to a repo root, plus git helpers.

The workspace is the agent's world. It computes the final unified diff
(``submit`` returns this) by running ``git diff`` through the same executor
the agent uses, so it works identically for host repos and container repos.

Git operations run through the executor; for the local sandbox the ``.git``
directory is read-only inside the sandbox by design, so diffs are taken via a
host-side direct executor when one is supplied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from frontier_runtime.harness.executor import Executor


@dataclass
class Workspace:
    run_id: str
    executor: Executor
    test_command: str = ""
    base_ref: str = ""
    # Optional separate executor for git/diff (host-side when the main
    # executor sandboxes .git read-only).
    git_executor: Executor | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def _git_exec(self) -> Executor:
        return self.git_executor or self.executor

    def root(self) -> str:
        return self.executor.workdir()

    #: Build artefacts / caches that must never appear in a graded patch — they
    #: pollute the prediction and can break ``git apply`` during SWE-bench grading.
    DIFF_EXCLUDES = (
        "':(exclude)**/__pycache__/**'",
        "':(exclude)*.pyc'",
        "':(exclude)**/*.pyc'",
        "':(exclude).pytest_cache/**'",
        "':(exclude)**/.pytest_cache/**'",
        "':(exclude)**/*.egg-info/**'",
        "':(exclude).mypy_cache/**'",
    )

    def diff(self) -> str:
        """Clean unified diff of source changes vs base_ref/HEAD.

        Excludes build/cache artefacts (``__pycache__``, ``*.pyc``, …) created as
        a side effect of running tests, so the prediction patch is apply-clean.

        Defensive against git's stat-cache missing a change under heavy
        concurrent load: refreshes the index and tries staged then working-tree
        diffs, returning the first non-empty result.
        """
        ex = self._git_exec()
        excludes = " ".join(self.DIFF_EXCLUDES)
        base = self.base_ref or "HEAD"
        # Refresh stat cache (defeats "racy git") then re-hash + stage real changes.
        ex.run_shell("git update-index -q --really-refresh >/dev/null 2>&1 || true", timeout=60)
        ex.run_shell(f"git add -A -- . {excludes}", timeout=60)
        for cmd in (
            f"git diff --no-color --cached {base} -- . {excludes}",
            f"git diff --no-color --cached -- . {excludes}",
            f"git diff --no-color {base} -- . {excludes}",
            f"git diff --no-color -- . {excludes}",
        ):
            res = ex.run_shell(cmd, timeout=60)
            if res.stdout.strip():
                return res.stdout
        return ""

    def changed_files(self) -> list[dict[str, Any]]:
        """Per-file changes vs base_ref: ``[{path, status, additions, deletions, diff}]``.

        Combines name-status (A/M/D/R) + numstat (line counts) + the unified diff
        (split per file). Used to show "files touched + diffs" for a run.
        """
        ex = self._git_exec()
        excludes = " ".join(self.DIFF_EXCLUDES)
        base = self.base_ref or "HEAD"
        ex.run_shell("git update-index -q --really-refresh >/dev/null 2>&1 || true", timeout=60)
        ex.run_shell(f"git add -A -- . {excludes}", timeout=60)

        def _first(*cmds: str) -> str:
            for cmd in cmds:
                res = ex.run_shell(cmd, timeout=60)
                if res.stdout.strip():
                    return res.stdout
            return ""

        name_status = _first(
            f"git diff --no-color --cached {base} --name-status -- . {excludes}",
            f"git diff --no-color {base} --name-status -- . {excludes}",
        )
        numstat = _first(
            f"git diff --no-color --cached {base} --numstat -- . {excludes}",
            f"git diff --no-color {base} --numstat -- . {excludes}",
        )
        full = self.diff()

        status_map: dict[str, str] = {}
        for line in name_status.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[-1]:
                status_map[parts[-1]] = parts[0][:1].upper()
        count_map: dict[str, tuple[int, int]] = {}
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                adds, dels, path = parts[0], parts[1], parts[2]
                count_map[path] = (
                    int(adds) if adds.isdigit() else 0,
                    int(dels) if dels.isdigit() else 0,
                )

        # Split the unified diff into per-file blocks (keyed by the b/ path).
        blocks: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in full.splitlines(keepends=True):
            if line.startswith("diff --git "):
                if current is not None:
                    blocks[current] = "".join(buf)
                match = re.search(r" b/(.+?)\s*$", line)
                current = match.group(1) if match else line.strip()
                buf = [line]
            else:
                buf.append(line)
        if current is not None:
            blocks[current] = "".join(buf)

        paths = list(dict.fromkeys([*blocks, *status_map, *count_map]))
        files: list[dict[str, Any]] = []
        for path in paths:
            adds, dels = count_map.get(path, (0, 0))
            files.append(
                {
                    "path": path,
                    "status": status_map.get(path, "M"),
                    "additions": adds,
                    "deletions": dels,
                    "diff": blocks.get(path, ""),
                }
            )
        return files

    def has_uncommitted_changes(self) -> bool:
        """True if git sees any tracked-file change (used to detect a lost diff)."""
        ex = self._git_exec()
        res = ex.run_shell("git status --porcelain --untracked-files=no", timeout=30)
        return bool(res.stdout.strip())

    def reset(self) -> None:
        ex = self._git_exec()
        ex.run_shell("git reset --hard HEAD && git clean -fd", timeout=60)

    def run_tests(self, command: str = "", *, timeout: int = 600):
        cmd = command or self.test_command
        if not cmd:
            return None
        return self.executor.run_shell(cmd, timeout=timeout)
