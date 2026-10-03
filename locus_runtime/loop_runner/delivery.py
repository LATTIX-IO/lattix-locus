"""Delivery side of the loop: git (branch, commit, push) and GitHub (PR, checks, merge).

Git and ``gh`` run as **platform delivery steps** of the runner -- argv lists,
never a shell, with timeouts -- the same way worktree provisioning does in
:mod:`locus_runtime.harness.workspace_binding`. They are not agent actions: the
agent never sees a push credential and every agent action stays behind the
run's gateway session. The ``gh`` CLI keeps its own credential (its keychain
login or ``GH_TOKEN``); nothing here reads or logs it.

:class:`LoopGitHub` is the seam the runner and the auto-merge guard use; tests
inject a fake, production uses :class:`GhCliLoopGitHub`.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from locus_runtime.loop_runner.merge_guard import ChangedFile, GateCheck

_BRANCH_SAFE = re.compile(r"[^a-z0-9]+")
_PLAIN_PATH = re.compile(r"[A-Za-z0-9._/-]{1,300}")


class DeliveryError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def branch_name(issue_key: str, title: str, *, prefix: str = "loop/") -> str:
    """``loop/<issue-key>-<slug>``: lower-case ``[a-z0-9-]`` only (no ref injection)."""
    key = _BRANCH_SAFE.sub("-", str(issue_key or "").lower()).strip("-") or "issue"
    slug = _BRANCH_SAFE.sub("-", str(title or "").lower()).strip("-")[:40].strip("-")
    return f"{prefix}{key}-{slug}" if slug else f"{prefix}{key}"


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #
@dataclass
class GitOps:
    """Host ``git`` for provisioning and delivery (argv only, bounded by timeouts)."""

    git: str = "git"
    timeout: int = 300

    def run(self, cwd: Path, *args: str, timeout: int | None = None) -> str:
        try:
            done = subprocess.run(
                [self.git, "-C", str(cwd), *args],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeliveryError(f"git {args[0]} timed out", transient=True) from exc
        except OSError as exc:
            raise DeliveryError(f"git is not available ({type(exc).__name__})") from exc
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
            raise DeliveryError(f"git {args[0]} failed: {detail[0][:300]}")
        return done.stdout

    def fetch(self, repo: Path, remote: str, branch: str) -> None:
        self.run(repo, "fetch", "--quiet", remote, branch)

    def add_worktree(self, repo: Path, path: Path, branch: str, base: str, *, remote: str) -> str:
        """An isolated working copy of ``base`` on a fresh ``branch``; returns the base SHA.

        A local clone (objects hard-linked, so it is cheap) rather than ``git
        worktree``: its ``.git`` lives *inside* the run's write root, so git works
        from inside the sandbox and from any path namespace, and the agent never
        needs access to the source repository's ``.git``. Its ``remote`` points at
        the source repository's real remote, so the runner pushes the branch there.
        """
        sha = self.run(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
        url = self.run(repo, "remote", "get-url", remote).strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run(
            path.parent,
            "clone",
            "--quiet",
            "--local",
            "--no-checkout",
            "--origin",
            remote,
            str(repo),
            str(path),
        )
        self.run(path, "remote", "set-url", remote, url)
        for key in ("user.name", "user.email"):
            try:
                value = self.run(repo, "config", "--get", key).strip()
            except DeliveryError:
                continue
            if value:
                self.run(path, "config", key, value)
        self.run(path, "checkout", "--quiet", "-B", branch, sha)
        return sha

    def remove_worktree(self, repo: Path, path: Path) -> None:  # noqa: ARG002 - same seam
        def _force(func: Any, target: str, _exc: Any) -> None:
            os.chmod(target, stat.S_IWRITE)  # git marks objects read-only on Windows
            func(target)

        if path.exists():
            shutil.rmtree(path, onexc=_force)

    def has_changes(self, worktree: Path) -> bool:
        return bool(self.run(worktree, "status", "--porcelain").strip())

    def changed_paths(self, worktree: Path) -> list[str]:
        """Paths the run added, modified or deleted vs ``HEAD`` (stages the change).

        ``--renormalize`` re-applies this host's line-ending rules to every tracked
        file: git inside the jail may stage files with a different ``core.autocrlf``
        (no user config there), which would otherwise list every file as changed.
        """
        self.run(worktree, "add", "-A")
        self.run(worktree, "add", "--renormalize", "--", ".")
        out = self.run(worktree, "diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD")
        return [p for p in out.split("\0") if p]

    def tracked_files(self, worktree: Path) -> list[str]:
        """Every file in the index (after :meth:`changed_paths`, includes new files)."""
        return [p for p in self.run(worktree, "ls-files", "-z").split("\0") if p]

    def commit_all(self, worktree: Path, message: str) -> str:
        self.run(worktree, "add", "-A")
        self.run(worktree, "commit", "--quiet", "-m", message)
        return self.run(worktree, "rev-parse", "HEAD").strip()

    def push(self, worktree: Path, remote: str, branch: str) -> None:
        self.run(worktree, "push", "--quiet", "-u", remote, f"HEAD:refs/heads/{branch}")


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    url: str
    state: str  # OPEN | MERGED | CLOSED
    head_sha: str = ""
    base_sha: str = ""


class LoopGitHub(Protocol):
    def open_pr(self, branch: str, base: str, title: str, body: str) -> PullRequestInfo: ...
    def pr_info(self, number: int) -> PullRequestInfo: ...
    def pr_checks(self, number: int) -> list[GateCheck]: ...
    def pr_files(self, number: int) -> list[ChangedFile]: ...
    def file_at(self, ref: str, path: str) -> str | None: ...
    def merge_pr(self, number: int, method: str, head_sha: str) -> None: ...


GhRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def default_gh_runner(cwd: Path, *, gh: str = "gh", timeout: int = 120) -> GhRunner:
    def run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [gh, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise DeliveryError("gh timed out", transient=True) from exc
        except OSError as exc:
            raise DeliveryError(f"the gh CLI is not available ({type(exc).__name__})") from exc

    return run


_BUCKETS: dict[str, tuple[str, str]] = {
    "pass": ("completed", "success"),
    "fail": ("completed", "failure"),
    "pending": ("in_progress", ""),
    "skipping": ("completed", "skipped"),
    "cancel": ("completed", "cancelled"),
}


@dataclass
class GhCliLoopGitHub:
    """:class:`LoopGitHub` over the ``gh`` CLI (argv, no shell)."""

    runner: GhRunner

    def _gh(self, *args: str) -> str:
        done = self.runner(list(args))
        if done.returncode != 0:
            detail = (done.stderr or "").strip().splitlines()[-1:] or [""]
            raise DeliveryError(f"gh {args[0]} failed: {detail[0][:300]}")
        return done.stdout or ""

    def _json(self, *args: str) -> Any:
        try:
            return json.loads(self._gh(*args) or "null")
        except ValueError as exc:
            raise DeliveryError(f"gh {args[0]} returned invalid JSON") from exc

    def _view(self, ref: str) -> PullRequestInfo:
        data = self._json("pr", "view", ref, "--json", "number,url,state,headRefOid,baseRefOid")
        return PullRequestInfo(
            number=int(data["number"]),
            url=str(data.get("url") or ""),
            state=str(data.get("state") or ""),
            head_sha=str(data.get("headRefOid") or ""),
            base_sha=str(data.get("baseRefOid") or ""),
        )

    def open_pr(self, branch: str, base: str, title: str, body: str) -> PullRequestInfo:
        # Idempotent: a retried delivery reuses the PR already open for the branch.
        existing = self._json("pr", "list", "--head", branch, "--state", "open", "--json", "number")
        if existing:
            return self._view(str(existing[0]["number"]))
        self._gh("pr", "create", "--head", branch, "--base", base, "--title", title, "--body", body)
        return self._view(branch)

    def pr_info(self, number: int) -> PullRequestInfo:
        return self._view(str(number))

    def pr_checks(self, number: int) -> list[GateCheck]:
        done = self.runner(["pr", "checks", str(number), "--json", "name,bucket"])
        # gh exits 8 while checks are pending and 1 when one failed; the JSON is still printed.
        try:
            items = json.loads(done.stdout or "[]")
        except ValueError as exc:
            raise DeliveryError("gh pr checks returned invalid JSON") from exc
        checks: list[GateCheck] = []
        for item in items or []:
            status, conclusion = _BUCKETS.get(str(item.get("bucket") or ""), ("unknown", ""))
            checks.append(GateCheck(str(item.get("name") or ""), status, conclusion))
        return checks

    def pr_files(self, number: int) -> list[ChangedFile]:
        pages = self._json(
            "api", "--paginate", "--slurp", f"repos/{{owner}}/{{repo}}/pulls/{number}/files"
        )
        files: list[ChangedFile] = []
        for page in pages or []:
            for item in page or []:
                files.append(
                    ChangedFile(
                        path=str(item.get("filename") or ""),
                        status=str(item.get("status") or "modified"),
                        previous_path=str(item.get("previous_filename") or ""),
                        patch=str(item.get("patch") or ""),
                    )
                )
        return files

    def file_at(self, ref: str, path: str) -> str | None:
        # Only plain repository paths/refs reach the API URL (None -> the guard holds).
        if not _PLAIN_PATH.fullmatch(path) or ".." in path or not _PLAIN_PATH.fullmatch(ref):
            return None
        done = self.runner(
            ["api", f"repos/{{owner}}/{{repo}}/contents/{path}?ref={ref}", "--jq", ".content"]
        )
        if done.returncode != 0:
            return None
        try:
            return base64.b64decode((done.stdout or "").strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def merge_pr(self, number: int, method: str, head_sha: str) -> None:
        flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}.get(
            method, "--squash"
        )
        # --match-head-commit: never merge a head other than the one the guard evaluated.
        self._gh("pr", "merge", str(number), flag, "--match-head-commit", head_sha)
