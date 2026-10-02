"""Tie a task / chat session to a specific repo — "where am I working".

A ``WorkspaceBinding`` is what you attach to an inbox task or a multi-agent chat
session: it says *which repo* the team works in, *what ref/branch* to base work
on, whether to isolate the work in a **git worktree** (so concurrent tasks don't
collide), and the **out-of-bounds policy** — by default the team is confined to
the bound repo and must ask permission to touch anything outside it.

``WorkspaceManager.provision`` turns a binding into a ready ``Workspace`` (with a
git worktree checked out at the base ref on a task branch) and a cleanup handle.
This is the same model T3 Code / Codex use: a task knows its working folder, and
work is delivered into that codebase.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from frontier_runtime.harness.executor import LocalDirectExecutor
from frontier_runtime.harness.swe_agent import SweTask
from frontier_runtime.harness.workspace import Workspace


def _sandbox_executor_requested() -> bool:
    """Whether agent tool-execution should run under the OS sandbox
    (bwrap/seatbelt/AppContainer via SandboxManager) instead of the unconfined
    LocalDirectExecutor. Opt-in (default off) so existing deploys are unchanged;
    auto-on for the native desktop profile."""
    flag = str(os.getenv("FRONTIER_SANDBOX_AGENTS") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    profile = str(os.getenv("FRONTIER_RUNTIME_PROFILE") or "").strip().lower()
    return profile in {"local-native", "native", "local_native"}


def _make_executor(root: str | Path, extra_paths: list[str]):
    """Select the harness executor: an OS-sandboxed executor when requested and
    feasible in-process, else the direct executor. K8s (hosted) is handled by the
    workflow engine, not in-process, so it stays on the direct executor here."""
    if _sandbox_executor_requested() and not os.getenv("KUBERNETES_SERVICE_HOST"):
        try:
            from frontier_runtime.harness.executor import LocalSandboxExecutor

            return LocalSandboxExecutor(root, extra_paths=extra_paths)
        except Exception:  # noqa: BLE001 - never block provisioning on sandbox setup
            pass
    return LocalDirectExecutor(root, extra_paths=extra_paths)


@dataclass
class WorkspaceBinding:
    """Where a task/chat works, and the boundary around it."""

    repo_path: str
    base_ref: str = ""  # branch/tag/sha to base work on (default: current HEAD)
    branch: str = ""  # working branch to create (default: derived from task id)
    isolation: Literal["worktree", "in-place"] = "worktree"
    allow_outside: Literal["ask", "deny", "allow"] = "ask"
    extra_paths: list[str] = field(default_factory=list)  # paths granted outside the repo
    test_command: str = ""

    def resolved_repo(self) -> Path:
        return Path(self.repo_path).expanduser().resolve()

    def to_payload(self) -> dict:
        return {
            "repo_path": str(self.resolved_repo()),
            "base_ref": self.base_ref,
            "branch": self.branch,
            "isolation": self.isolation,
            "allow_outside": self.allow_outside,
            "extra_paths": list(self.extra_paths),
            "test_command": self.test_command,
        }

    @classmethod
    def from_payload(cls, data: dict) -> "WorkspaceBinding":
        return cls(
            repo_path=str(data.get("repo_path") or data.get("repo") or "."),
            base_ref=str(data.get("base_ref") or ""),
            branch=str(data.get("branch") or ""),
            isolation=str(data.get("isolation") or "worktree"),  # type: ignore[arg-type]
            allow_outside=str(data.get("allow_outside") or "ask"),  # type: ignore[arg-type]
            extra_paths=[str(p) for p in (data.get("extra_paths") or [])],
            test_command=str(data.get("test_command") or ""),
        )


@dataclass
class ProvisionedWorkspace:
    binding: WorkspaceBinding
    workspace: Workspace
    root: Path
    branch: str
    cleanup: Callable[[], None]


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout
    )


def _safe_branch(run_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_/.") else "-" for c in run_id).strip("-/")
    return f"frontier/{safe or 'task'}"


class WorkspaceManager:
    """Provision per-task workspaces (git worktree isolation) from a binding."""

    def __init__(self, worktrees_root: Path | None = None) -> None:
        self.worktrees_root = worktrees_root or Path(
            os.getenv("FRONTIER_WORKTREES_ROOT") or (Path.home() / ".frontier" / "worktrees")
        )

    def provision(self, binding: WorkspaceBinding, run_id: str) -> ProvisionedWorkspace:
        repo = binding.resolved_repo()
        if not repo.is_dir():
            raise FileNotFoundError(f"bound repo does not exist: {repo}")
        branch = binding.branch or _safe_branch(run_id)

        if binding.isolation == "in-place" or not self._is_git_repo(repo):
            executor = _make_executor(repo, binding.extra_paths)
            ws = Workspace(
                run_id=run_id,
                executor=executor,
                test_command=binding.test_command,
                base_ref=binding.base_ref or "HEAD",
            )
            return ProvisionedWorkspace(binding, ws, repo, "(in-place)", lambda: None)

        # git worktree isolation: a fresh checkout off base_ref on a task branch
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        wt_dir = (self.worktrees_root / run_id).resolve()
        if wt_dir.exists():
            self._remove_worktree(repo, wt_dir)
        base = binding.base_ref or "HEAD"
        add = _git(repo, "worktree", "add", "-B", branch, str(wt_dir), base)
        if add.returncode != 0:
            # fall back to a detached worktree at base if branch creation failed
            add2 = _git(repo, "worktree", "add", "--detach", str(wt_dir), base)
            if add2.returncode != 0:
                raise RuntimeError(f"git worktree add failed: {add.stderr or add2.stderr}")

        executor = _make_executor(wt_dir, binding.extra_paths)
        ws = Workspace(
            run_id=run_id, executor=executor, test_command=binding.test_command, base_ref=base
        )

        def cleanup() -> None:
            self._remove_worktree(repo, wt_dir)

        return ProvisionedWorkspace(binding, ws, wt_dir, branch, cleanup)

    def build_task(
        self, binding: WorkspaceBinding, run_id: str, problem_statement: str
    ) -> tuple[SweTask, ProvisionedWorkspace]:
        prov = self.provision(binding, run_id)
        task = SweTask(
            instance_id=run_id,
            problem_statement=problem_statement,
            executor=prov.workspace.executor,
            test_command=binding.test_command,
            base_ref=prov.workspace.base_ref,
            metadata={"binding": binding.to_payload(), "branch": prov.branch},
        )
        return task, prov

    @staticmethod
    def _is_git_repo(repo: Path) -> bool:
        return _git(repo, "rev-parse", "--is-inside-work-tree").returncode == 0

    @staticmethod
    def _remove_worktree(repo: Path, wt_dir: Path) -> None:
        _git(repo, "worktree", "remove", "--force", str(wt_dir))
        if wt_dir.exists():
            shutil.rmtree(wt_dir, ignore_errors=True)
        _git(repo, "worktree", "prune")
