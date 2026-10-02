"""Command + file execution backends for the coding harness.

A single ``Executor`` protocol abstracts *where* the agent's tools run:

* ``LocalDirectExecutor`` — plain subprocess in a host directory. Used for
  local-repo dev loops, tests, and Windows dev hosts that lack a kernel
  sandbox. No isolation; intended for trusted/CI use.
* ``LocalSandboxExecutor`` — wraps ``frontier_runtime.sandbox.SandboxManager``
  to run commands under bubblewrap/seatbelt/hardened-docker. Production local.
* ``DockerContainerExecutor`` — ``docker exec`` into an already-running
  container (the SWE-bench / DeepSWE per-instance environment on a remote
  ``DOCKER_HOST``).

File operations (read/write/exists) are part of the protocol because
``str_replace_editor`` must work identically whether files live on the host or
inside a container.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from frontier_runtime.sandbox import (
    ExecutionSpec,
    HostPlatform,
    SandboxManager,
    SandboxPolicy,
    detect_host_platform,
)


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    backend: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def combined(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr if not self.stdout else f"[stderr]\n{self.stderr}")
        body = "\n".join(parts).strip()
        suffix = ""
        if self.timed_out:
            suffix = f"\n[command timed out after {self.duration_seconds:.0f}s]"
        return f"{body}\n[exit code: {self.exit_code}]{suffix}".strip()


class Executor(Protocol):
    backend: str

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult: ...
    def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult: ...
    def read_file(self, path: str) -> str | None: ...
    def write_file(self, path: str, content: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def workdir(self) -> str: ...
    def allows(self, path: str) -> bool: ...


# ---------------------------------------------------------------------------
# Local direct (no sandbox) — dev/CI/Windows
# ---------------------------------------------------------------------------


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


class LocalDirectExecutor:
    """Run commands directly in a host directory (no isolation)."""

    backend = "local-direct"

    def __init__(
        self,
        root: str | Path,
        *,
        env: dict[str, str] | None = None,
        extra_paths: list[str] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.env = env
        # Additional roots the agent is explicitly permitted to touch (e.g. a
        # shared lib granted by the human). Empty by default = confined to root.
        self.extra_paths = [Path(p).expanduser().resolve() for p in (extra_paths or [])]

    def workdir(self) -> str:
        return str(self.root)

    def allows(self, path: str) -> bool:
        """True if ``path`` is inside the bound workspace (root or a granted extra)."""
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        resolved = p.resolve()
        roots = [self.root, *self.extra_paths]
        return any(resolved == r or _is_within(r, resolved) for r in roots)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        resolved = p.resolve()
        if not self.allows(str(resolved)):
            raise PermissionError(f"Path escapes workspace root: {path}")
        return resolved

    def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult:
        # Non-login shell so the caller's PATH/env (incl. the active python)
        # is inherited rather than reset by profile scripts.
        return self.run(["bash", "-c", script], timeout=timeout)

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
        import time as _time

        # Local dev/CI: a login shell resets PATH; downgrade to -c so the
        # inherited environment (active venv/python) is used.
        if len(command) == 3 and command[0] == "bash" and command[1] == "-lc":
            command = ["bash", "-c", command[2]]
        start = _time.time()
        run_env = dict(os.environ)
        if self.env:
            run_env.update(self.env)
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
            )
            return ExecResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_seconds=_time.time() - start,
                backend=self.backend,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                exit_code=124,
                stdout=(exc.stdout or b"").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                stderr=(exc.stderr or b"").decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or ""),
                duration_seconds=timeout,
                timed_out=True,
                backend=self.backend,
            )

    def read_file(self, path: str) -> str | None:
        p = self._resolve(path)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write + fsync so a command shell observing the same path through a
        # different filesystem view (e.g. WSL /mnt/c reading a Windows write,
        # or an NFS-mounted runner) sees the change immediately. No-op cost on
        # native filesystems; eliminates a read-after-write race on interop FS.
        data = content.encode("utf-8")
        with open(p, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())

    def exists(self, path: str) -> bool:
        try:
            return self._resolve(path).exists()
        except PermissionError:
            return False


# ---------------------------------------------------------------------------
# Local sandboxed — production local
# ---------------------------------------------------------------------------


class LocalSandboxExecutor:
    """Run commands under the kernel/docker sandbox; files on the host."""

    backend = "local-sandbox"

    def __init__(
        self,
        root: str | Path,
        *,
        manager: SandboxManager | None = None,
        allow_network: bool = False,
        extra_paths: list[str] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self._manager = manager or SandboxManager()
        self._allow_network = allow_network
        self._platform: HostPlatform = detect_host_platform()
        self._extra_paths = [str(Path(p).expanduser().resolve()) for p in (extra_paths or [])]
        self._direct = LocalDirectExecutor(root, extra_paths=extra_paths)

    def workdir(self) -> str:
        return str(self.root)

    def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult:
        return self.run(["bash", "-lc", script], timeout=timeout)

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
        import time as _time

        executable = command[0] if command else ""
        policy = SandboxPolicy(
            platform=self._platform,
            allow_network=self._allow_network,
            allowed_read_paths=[str(self.root), *self._extra_paths],
            allowed_write_paths=[str(self.root), *self._extra_paths],
            allowed_executables=[executable],
            timeout_seconds=timeout,
        )
        spec = ExecutionSpec(tool_id="coding", command=command, cwd=str(self.root))
        plan = self._manager.plan(spec, policy)
        if plan.backend.startswith("k8s-"):
            raise NotImplementedError(
                "K8s sandbox execution is the workflow engine's responsibility; "
                "the harness cannot exec a pod spec in-process."
            )
        start = _time.time()
        try:
            proc = subprocess.run(plan.command, capture_output=True, text=True, timeout=timeout)
            return ExecResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_seconds=_time.time() - start,
                backend=plan.backend,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr="",
                duration_seconds=timeout,
                timed_out=True,
                backend=plan.backend,
            )

    def allows(self, path: str) -> bool:
        return self._direct.allows(path)

    # File ops happen on the host (git stays read-only via sandbox invariant).
    def read_file(self, path: str) -> str | None:
        return self._direct.read_file(path)

    def write_file(self, path: str, content: str) -> None:
        self._direct.write_file(path, content)

    def exists(self, path: str) -> bool:
        return self._direct.exists(path)


# ---------------------------------------------------------------------------
# Docker container exec — SWE-bench / DeepSWE per-instance environments
# ---------------------------------------------------------------------------


class DockerContainerExecutor:
    """Execute inside an already-running container via ``docker exec``.

    ``docker_host`` maps to the ``DOCKER_HOST`` env for the spawned docker CLI,
    so the benchmark fleet runs on a remote runner box, never locally.
    """

    backend = "docker-exec"

    def __init__(
        self,
        container_id: str,
        *,
        workdir_path: str = "/testbed",
        docker_host: str | None = None,
        docker_bin: str = "docker",
    ) -> None:
        self.container_id = container_id
        self._workdir = workdir_path
        self._docker_host = docker_host or os.getenv("DOCKER_HOST") or ""
        self._docker = docker_bin

    def workdir(self) -> str:
        return self._workdir

    def allows(self, path: str) -> bool:
        abs_path = self._abs(path)
        wd = self._workdir.rstrip("/") or "/"
        return abs_path == wd or abs_path.startswith(wd + "/")

    def _docker_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        return env

    def _exec(self, inner: list[str], *, timeout: int) -> ExecResult:
        import time as _time

        cmd = [
            self._docker,
            "exec",
            "-w",
            self._workdir,
            self.container_id,
            *inner,
        ]
        start = _time.time()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=self._docker_env()
            )
            return ExecResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_seconds=_time.time() - start,
                backend=self.backend,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr="",
                duration_seconds=timeout,
                timed_out=True,
                backend=self.backend,
            )

    def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult:
        # Login shell so conda/venv activation in the SWE-bench image applies.
        return self._exec(["bash", "-lc", script], timeout=timeout)

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
        # Run through bash -lc so PATH/activate scripts behave like a shell.
        if len(command) == 3 and command[0] in ("bash", "sh") and command[1] in ("-lc", "-c"):
            return self._exec(["bash", "-lc", command[2]], timeout=timeout)
        joined = " ".join(shlex.quote(c) for c in command)
        return self._exec(["bash", "-lc", joined], timeout=timeout)

    def read_file(self, path: str) -> str | None:
        res = self._exec(["cat", "--", self._abs(path)], timeout=30)
        if res.exit_code != 0:
            return None
        return res.stdout

    def write_file(self, path: str, content: str) -> None:
        # base64-pipe to avoid quoting hazards with arbitrary content.
        import base64

        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        target = self._abs(path)
        script = (
            f'mkdir -p "$(dirname {shlex.quote(target)})" && '
            f"printf %s {shlex.quote(b64)} | base64 -d > {shlex.quote(target)}"
        )
        res = self._exec(["bash", "-lc", script], timeout=60)
        if res.exit_code != 0:
            raise RuntimeError(f"write_file failed in container: {res.stderr or res.stdout}")

    def exists(self, path: str) -> bool:
        res = self._exec(["test", "-e", self._abs(path)], timeout=15)
        return res.exit_code == 0

    def _abs(self, path: str) -> str:
        if path.startswith("/"):
            return path
        return f"{self._workdir.rstrip('/')}/{path}"
