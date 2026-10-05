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
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
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
#: Git control files inside a working copy's ``.git`` that make *host* git run
#: code or redirect data: config (filter/diff/textconv drivers, fsmonitor,
#: sshCommand, credential helpers, insteadOf, remote URLs, include.path), hooks,
#: info/ (attributes, excludes) and object alternates. The agent can write the
#: working copy (its ``.git`` is inside the run's write root), so host git only
#: touches it while these are unchanged since provisioning.
_SEALED_GIT_FILES = (
    "config",
    "config.worktree",
    "commondir",
    "HEAD",
    "objects/info/alternates",
    "objects/info/http-alternates",
)
_SEALED_GIT_DIRS = ("hooks", "info")
_SEAL_SUFFIX = ".gitseal"
_NO_HOOKS_DIR: str | None = None


def _no_hooks_dir() -> str:
    """An empty, runner-owned directory used as ``core.hooksPath`` (no hooks run)."""
    global _NO_HOOKS_DIR
    if _NO_HOOKS_DIR is None or not os.path.isdir(_NO_HOOKS_DIR):
        _NO_HOOKS_DIR = tempfile.mkdtemp(prefix="locus-nohooks-")
    return _NO_HOOKS_DIR


def _hardening_args() -> list[str]:
    return ["-c", f"core.hooksPath={_no_hooks_dir()}", "-c", "core.fsmonitor=false"]


#: Extra settings for fetching a repository the runner only reads (LOCUS-382):
#: no ``ext::`` transport, no external diff, no symlinks, no submodules, objects
#: checked on receipt, line endings never converted.
_FETCH_HARDENING: tuple[str, ...] = (
    "protocol.ext.allow=never",
    "diff.external=",
    "core.symlinks=false",
    "core.autocrlf=false",
    "submodule.recurse=false",
    "transfer.fsckObjects=true",
)
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
_HEX_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _last_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1][:300] if lines else ""


def git_metadata_digest(worktree: Path) -> str:
    """Digest of the sealed git control files of ``worktree`` (absent files count)."""
    git_dir = worktree / ".git"
    h = hashlib.sha256()
    names = list(_SEALED_GIT_FILES)
    for sub in _SEALED_GIT_DIRS:
        root = git_dir / sub
        if root.is_dir():
            names.extend(
                sorted(p.relative_to(git_dir).as_posix() for p in root.rglob("*") if p.is_file())
            )
        elif root.exists():
            names.append(sub)
    for name in names:
        target = git_dir / name
        h.update(name.encode("utf-8") + b"\0")
        if target.is_symlink():
            h.update(b"L" + os.readlink(target).encode("utf-8", "replace"))
        elif target.is_file():
            h.update(b"F" + target.read_bytes())
        elif target.exists():
            h.update(b"D")
        else:
            h.update(b"-")
        h.update(b"\0")
    return h.hexdigest()


def _seal_path(worktree: Path) -> Path:
    # A sibling of the working copy: outside the run's write root.
    return worktree.parent / (worktree.name + _SEAL_SUFFIX)


@dataclass
class GitOps:
    """Host ``git`` for provisioning and delivery (argv only, bounded by timeouts).

    Every invocation runs with hooks and fsmonitor disabled, and a working copy
    provisioned by :meth:`add_worktree` is sealed: before host git touches it
    again, its git control files must match the seal (see
    :data:`_SEALED_GIT_FILES`), or the call is refused.
    """

    git: str = "git"
    timeout: int = 300

    def seal(self, worktree: Path) -> None:
        _seal_path(worktree).write_text(git_metadata_digest(worktree), encoding="utf-8")

    def verify_seal(self, worktree: Path) -> None:
        seal = _seal_path(worktree)
        if not seal.is_file():
            return
        if seal.read_text(encoding="utf-8").strip() != git_metadata_digest(worktree):
            raise DeliveryError(
                "workspace_git_tampered: the working copy's git configuration, hooks or "
                "info files changed during the run; refusing to run host git on it"
            )

    def run(
        self,
        cwd: Path,
        *args: str,
        timeout: int | None = None,
        config: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run host git; ``config`` adds ``-c key=value`` overrides, ``env`` replaces
        the environment (``None`` inherits it)."""
        done = self._exec(cwd, args, timeout=timeout, config=config, env=env, text=True)
        if done.returncode != 0:
            raise DeliveryError(f"git {args[0]} failed: {_last_line(done.stderr or done.stdout)}")
        return str(done.stdout)

    def run_bytes(
        self,
        cwd: Path,
        *args: str,
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> bytes:
        """Like :meth:`run`, but stdout as raw bytes (blob contents, no newline translation)."""
        done = self._exec(cwd, args, timeout=timeout, config=(), env=env, text=False)
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace") if done.stderr else ""
            raise DeliveryError(f"git {args[0]} failed: {_last_line(err)}")
        return bytes(done.stdout)

    def _exec(
        self,
        cwd: Path,
        args: Sequence[str],
        *,
        timeout: int | None,
        config: Sequence[str],
        env: Mapping[str, str] | None,
        text: bool,
    ) -> subprocess.CompletedProcess[Any]:
        self.verify_seal(cwd)
        overrides = [part for item in config for part in ("-c", item)]
        try:
            return subprocess.run(
                [self.git, *_hardening_args(), *overrides, "-C", str(cwd), *args],
                capture_output=True,
                text=text,
                timeout=timeout or self.timeout,
                env=None if env is None else dict(env),
            )
        except subprocess.TimeoutExpired as exc:
            raise DeliveryError(f"git {args[0]} timed out", transient=True) from exc
        except OSError as exc:
            raise DeliveryError(f"git is not available ({type(exc).__name__})") from exc

    # ------------------------------------------------- read-only fetch (LOCUS-382)
    def clone_ref(
        self,
        url: str,
        dest: Path,
        ref: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
    ) -> str:
        """``git clone --depth 1 --branch <ref>`` of one tag or branch; returns HEAD's sha.

        No checkout: files are read as blobs (:meth:`tree_blobs`, :meth:`read_blob`),
        so no smudge/clean filter, attribute, symlink, submodule or hook of the
        cloned repository runs on the host. Credentials come from the user's own
        git credential helper; nothing here reads or stores them.
        """
        if not _SAFE_REF.fullmatch(ref) or ".." in ref or ref.endswith((".lock", "/")):
            raise DeliveryError(f"refusing git ref {ref!r}")
        if not url or url.startswith("-") or url.lower().startswith("ext::"):
            raise DeliveryError("refusing the repository URL (looks like an option or transport)")
        if dest.exists():
            raise DeliveryError(f"clone target already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.run(
            dest.parent,
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            ref,
            "--single-branch",
            "--no-checkout",
            "--no-recurse-submodules",
            "--",
            url,
            str(dest),
            timeout=timeout,
            config=_FETCH_HARDENING,
            env=env,
        )
        return self.run(dest, "rev-parse", "--verify", "HEAD^{commit}", env=env).strip()

    def tree_blobs(self, repo: Path, rev: str = "HEAD") -> list[tuple[str, str, str]]:
        """``(mode, object sha, path)`` of every entry of ``rev``'s tree (recursive);
        a non-blob entry's mode is suffixed with its type (``160000:commit``)."""
        out = self.run(repo, "ls-tree", "-r", "-z", "--full-tree", rev)
        entries: list[tuple[str, str, str]] = []
        for item in out.split("\0"):
            if not item:
                continue
            meta, _, path = item.partition("\t")
            parts = meta.split()
            if len(parts) != 3:
                raise DeliveryError("git ls-tree returned an unexpected line")
            mode, kind, sha = parts
            entries.append((mode if kind == "blob" else f"{mode}:{kind}", sha, path))
        return entries

    def read_blob(self, repo: Path, sha: str) -> bytes:
        if not _HEX_SHA.fullmatch(sha):
            raise DeliveryError("refusing a blob id that is not a hex sha")
        return self.run_bytes(repo, "cat-file", "blob", sha)

    def tag_object(self, repo: Path, tag: str) -> tuple[str, str]:
        """``(kind, body)`` of ``refs/tags/<tag>``: ``("tag", <annotated tag text>)``,
        ``("commit", "")`` for a lightweight tag, ``("", "")`` when there is no such tag."""
        name = f"refs/tags/{tag}"
        try:
            kind = self.run(repo, "cat-file", "-t", name).strip()
        except DeliveryError:
            return "", ""
        if kind != "tag":
            return kind, ""
        return kind, self.run(repo, "cat-file", "tag", name)

    def verify_tag(
        self, repo: Path, tag: str, *, env: Mapping[str, str] | None = None
    ) -> tuple[int, str]:
        """``git verify-tag --raw <tag>``: ``(exit code, combined output)``. The
        verifier (gpg, gpgsm or ssh-keygen) is the user's own configuration."""
        done = self._exec(
            repo,
            ("verify-tag", "--raw", f"refs/tags/{tag}"),
            timeout=None,
            config=(),
            env=env,
            text=True,
        )
        return int(done.returncode), f"{done.stdout or ''}\n{done.stderr or ''}".strip()

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
        self.seal(path)
        return sha

    def remove_worktree(self, repo: Path, path: Path) -> None:  # noqa: ARG002 - same seam
        def _force(func: Any, target: str, _exc: Any) -> None:
            os.chmod(target, stat.S_IWRITE)  # git marks objects read-only on Windows
            func(target)

        if path.exists():
            shutil.rmtree(path, onexc=_force)
        _seal_path(path).unlink(missing_ok=True)

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

    def diff(self, worktree: Path, base: str = "HEAD", pathspecs: Sequence[str] = ()) -> str:
        """Unified diff of the working copy vs ``base`` (stages the change, like
        :meth:`changed_paths`). Diff and textconv drivers are off: the agent can
        write ``.gitattributes``, and host git must not run a driver for it."""
        if not base or base.startswith("-") or any(str(p).startswith("-") for p in pathspecs):
            raise DeliveryError("git diff refused: an argument looks like an option")
        self.run(worktree, "add", "-A", "--", ".", *pathspecs)
        self.run(worktree, "add", "--renormalize", "--", ".")
        return self.run(
            worktree,
            "diff",
            "--cached",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            base,
            "--",
            ".",
            *pathspecs,
        )

    def has_tracked_changes(self, worktree: Path) -> bool:
        return bool(self.run(worktree, "status", "--porcelain", "--untracked-files=no").strip())

    def tracked_files(self, worktree: Path) -> list[str]:
        """Every file in the index (after :meth:`changed_paths`, includes new files)."""
        return [p for p in self.run(worktree, "ls-files", "-z").split("\0") if p]

    def commit_all(self, worktree: Path, message: str) -> str:
        self.run(worktree, "add", "-A")
        self.run(worktree, "commit", "--quiet", "--no-verify", "-m", message)
        return self.run(worktree, "rev-parse", "HEAD").strip()

    def push(self, worktree: Path, remote: str, branch: str) -> None:
        # No ``-u``: an upstream entry would rewrite the sealed .git/config.
        self.run(worktree, "push", "--quiet", "--no-verify", remote, f"HEAD:refs/heads/{branch}")


@dataclass
class HostWorkspaceGit:
    """:class:`~locus_runtime.harness.workspace.HostGit` over :class:`GitOps`.

    Gives the agent's ``submit`` and the verify gate a real diff on Windows,
    where git cannot run inside the AppContainer (LOCUS-362). Host git here is a
    platform step with fixed argv: hooks and fsmonitor off and the ``.git`` seal
    verified on every call (a tampered working copy raises
    :class:`DeliveryError`, which fails the run).
    """

    git: GitOps
    worktree: Path

    def diff(self, base: str, pathspecs: Sequence[str]) -> str:
        return self.git.diff(self.worktree, base, pathspecs)

    def has_uncommitted_changes(self) -> bool:
        return self.git.has_tracked_changes(self.worktree)


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
