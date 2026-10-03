"""Command + file execution backends for the coding harness.

A single ``Executor`` protocol abstracts *where* the agent's tools run:

* ``LocalSandboxExecutor`` — wraps ``locus_runtime.sandbox.SandboxManager``
  to run commands under the platform's confining tier: bubblewrap (Linux),
  seatbelt (macOS), AppContainer + Job Object (Windows) or hardened Docker.
  This is the default (:func:`default_executor`).
* ``DockerContainerExecutor`` — ``docker exec`` into an already-running
  container (the SWE-bench / DeepSWE per-instance environment on a remote
  ``DOCKER_HOST``). tool_jail accepts it only for an ``evals`` session with the
  container's networking disabled.
* ``LocalDirectExecutor`` — plain subprocess in a host directory, no isolation.
  Kept for tests and an explicit dev opt-out (``LOCUS_SANDBOX_AGENTS=0``);
  tool_jail denies its process execution (``local-direct`` is not a jail).

Jail facts (LOCUS-332, principal decision 2026-10-03): each executor reports the
tier it actually launches with -- derived from the selected strategy, never
supplied by a caller -- and tool_jail decides whether that is a jail.

Windows toolchain (LOCUS-333): inside the AppContainer only ALL APPLICATION
PACKAGES-readable tools run, so ``LocalSandboxExecutor`` runs ``run_shell`` as
BusyBox ``sh -c`` and maps ``sh``/``bash``/``python``/``python3`` to the
Locus-owned toolchain (``locus_runtime.win_toolchain``) *after* the gateway
decision: tool_jail allowlists the logical names, never a toolchain path. The
toolchain directories come first on the sandbox PATH. Without an installed
toolchain those commands fail with an actionable message (exit 127).

Environment: agent commands never inherit the full ``os.environ``. They get
``locus_runtime.sandbox.minimal_agent_env`` (PATH, HOME/USERPROFILE, TEMP/TMP,
LANG, SystemRoot, ...) plus explicit per-run variables; LOCUS_* settings and
secret-like names (keys, tokens, passwords) are always dropped.

File operations (read/write/exists) are part of the protocol because
``str_replace_editor`` must work identically whether files live on the host or
inside a container.

Gateway (LOCUS-332, P6): every executor asks ``locus_runtime.gateway`` before a
side effect. ``run``/``run_shell`` are ``process_exec`` actions, ``write_file``
is ``file_write`` and ``read_file`` is ``file_read``. The process spawn and the
file IO live only in the private ``_spawn`` / ``_write_bytes`` / ``_read_text``
sinks, which are called only after ``_gate`` allowed the action
(``tests/harness/test_gateway_bypass.py`` enforces this). A blocked command
returns an ``ExecResult`` with exit code 126 and the decision attached; a
blocked file operation raises :class:`~locus_runtime.gateway.GatewayBlocked`.
Executors constructed without a ``gateway_session`` act as unbound callers,
which a real gateway never authenticates (deny).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from locus_runtime.gateway import (
    GatewayBlocked,
    GatewayDecision,
    GatewaySession,
    JailFacts,
    authorize_action,
    current_tool,
    current_uid_user,
)
from locus_runtime.sandbox import (
    ExecutionSpec,
    HostPlatform,
    IsolationStrategy,
    SandboxManager,
    SandboxPolicy,
    detect_host_platform,
    docker_cli_env,
    minimal_agent_env,
    select_confining_strategy,
)
from locus_runtime.win_toolchain import (
    MISSING_TOOLCHAIN_HINT,
    WindowsToolchain,
    discover_toolchain,
    needs_toolchain,
)

GATEWAY_BLOCKED_EXIT_CODE = 126
TOOLCHAIN_MISSING_EXIT_CODE = 127

#: ``toolchain=`` default for LocalSandboxExecutor: discover the installed one.
AUTO_TOOLCHAIN = "auto"


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    backend: str = ""
    gateway: GatewayDecision | None = None

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


def _blocked_result(decision: GatewayDecision, backend: str, hint: str = "") -> ExecResult:
    label = "permission required" if decision.outcome == "ask" else "denied by policy"
    detail = f"\n{hint}" if hint and decision.outcome == "deny" else ""
    return ExecResult(
        exit_code=GATEWAY_BLOCKED_EXIT_CODE,
        stdout="",
        stderr=f"[{label}] gateway {decision.describe()}{detail}",
        duration_seconds=0.0,
        backend=backend,
        gateway=decision,
    )


def _command_text(command: list[str]) -> str:
    if len(command) == 3 and command[0] in ("bash", "sh") and command[1] in ("-c", "-lc"):
        return command[2]
    return " ".join(shlex.quote(str(part)) for part in command)


class _GatedExecutor:
    """Shared gateway plumbing for the executors below."""

    backend = ""
    gateway_session: GatewaySession | None = None

    def jail_facts(self) -> JailFacts:
        return JailFacts(strategy="none", run_as_user=current_uid_user())

    def _gate(self, kind: str, target: str, *, command: list[str] | None = None) -> GatewayDecision:
        if kind == "process_exec":
            argv = list(command or [])
            return authorize_action(
                self.gateway_session,
                kind=kind,
                tool=current_tool(),
                target=target,
                command=_command_text(argv),
                executable=str(argv[0]) if argv else "",
                jail=self.jail_facts(),
            )
        return authorize_action(self.gateway_session, kind=kind, tool=current_tool(), target=target)


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


class LocalDirectExecutor(_GatedExecutor):
    """Run commands directly in a host directory (no isolation).

    Tests and explicit dev opt-out only: tool_jail never accepts ``local-direct``
    as a jail, so behind a real gateway its process execution is denied.
    """

    backend = "local-direct"

    def __init__(
        self,
        root: str | Path,
        *,
        env: dict[str, str] | None = None,
        extra_paths: list[str] | None = None,
        gateway_session: GatewaySession | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.env = env
        self.gateway_session = gateway_session
        # Additional roots the agent is explicitly permitted to touch (e.g. a
        # shared lib granted by the human). Empty by default = confined to root.
        self.extra_paths = [Path(p).expanduser().resolve() for p in (extra_paths or [])]

    def jail_facts(self) -> JailFacts:
        return JailFacts(strategy="local-direct", run_as_user=current_uid_user())

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
        # Local dev/CI: a login shell resets PATH; downgrade to -c so the
        # inherited environment (active venv/python) is used.
        if len(command) == 3 and command[0] == "bash" and command[1] == "-lc":
            command = ["bash", "-c", command[2]]
        decision = self._gate("process_exec", str(self.root), command=command)
        if not decision.allowed:
            return _blocked_result(decision, self.backend)
        return self._spawn(command, timeout=timeout)

    def _spawn(self, command: list[str], *, timeout: int) -> ExecResult:
        import time as _time

        start = _time.time()
        run_env = minimal_agent_env(self.env)
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
        decision = self._gate("file_read", str(p))
        if not decision.allowed:
            raise GatewayBlocked(decision)
        return self._read_text(p)

    @staticmethod
    def _read_text(p: Path) -> str | None:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        p = self._resolve(path)
        decision = self._gate("file_write", str(p))
        if not decision.allowed:
            raise GatewayBlocked(decision)
        self._write_bytes(p, content.encode("utf-8"))

    @staticmethod
    def _write_bytes(p: Path, data: bytes) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write + fsync so a command shell observing the same path through a
        # different filesystem view (e.g. WSL /mnt/c reading a Windows write,
        # or an NFS-mounted runner) sees the change immediately. No-op cost on
        # native filesystems; eliminates a read-after-write race on interop FS.
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


def _sandbox_jail_facts(
    strategy: IsolationStrategy, allow_network: bool, *, require_appcontainer: bool = True
) -> JailFacts:
    """Jail facts per sandbox strategy, as tool_jail sees them. Nothing is assumed:
    each fact restates what the strategy's launch command really does."""
    if strategy in (IsolationStrategy.KERNEL_BWRAP, IsolationStrategy.KERNEL_SEATBELT):
        # bwrap: --ro-bind / / (+ explicit writable binds); seatbelt: (deny default)
        # with file-write* only under the bound roots. The uid is this process's.
        return JailFacts(
            strategy=strategy.value,
            readonly_rootfs=True,
            run_as_user=current_uid_user(),
            allow_network=allow_network,
        )
    if strategy == IsolationStrategy.HARDENED_DOCKER:
        # _HardenedDockerStrategy runs --read-only --user=1000:1000.
        return JailFacts(
            strategy=strategy.value,
            readonly_rootfs=True,
            run_as_user="1000:1000",
            allow_network=allow_network,
        )
    if strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
        # _WindowsAppContainerStrategy launches win_sandbox, which places the child
        # in an AppContainer *and* a Job Object; with --require-appcontainer it fails
        # closed instead of degrading to the Job-Object-only tier. Without the flag
        # the launcher may degrade, so the facts then claim no AppContainer. Network
        # capabilities are granted only when allow_network is set.
        return JailFacts(
            strategy=strategy.value,
            allow_network=allow_network,
            appcontainer=require_appcontainer,
            job_object=True,
            require_appcontainer=require_appcontainer,
        )
    # restricted-process, k8s-*: nothing tool_jail can verify as a jail here.
    return JailFacts(strategy=strategy.value, allow_network=allow_network)


def _resolved_strategy(manager: SandboxManager) -> IsolationStrategy | None:
    try:
        return manager.active_strategy
    except Exception:  # noqa: BLE001 - an undetectable tier is not a jail
        return None


class LocalSandboxExecutor(_GatedExecutor):
    """Run commands under the kernel/docker/AppContainer sandbox; files on the host.

    ``unavailable_reason`` marks a host with no confining tier (see
    :func:`default_executor`): process execution then reports the ``unavailable``
    tier, which tool_jail denies with that actionable reason, and the spawn sink
    refuses to run even if an authorizer allowed it.
    """

    backend = "local-sandbox"

    def __init__(
        self,
        root: str | Path,
        *,
        manager: SandboxManager | None = None,
        allow_network: bool = False,
        extra_paths: list[str] | None = None,
        gateway_session: GatewaySession | None = None,
        env: dict[str, str] | None = None,
        unavailable_reason: str = "",
        toolchain: WindowsToolchain | str | None = AUTO_TOOLCHAIN,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.gateway_session = gateway_session
        self._manager = manager or SandboxManager()
        # Windows AppContainer only: the Locus-owned agent toolchain (``"auto"``
        # discovers it under the app home on first use; ``None`` = none).
        self._toolchain_arg = toolchain
        self._toolchain_resolved = False
        self._toolchain: WindowsToolchain | None = None
        self._allow_network = allow_network
        self.env = env
        self.unavailable_reason = unavailable_reason
        # Always launch the Windows tier fail-closed (no Job-Object-only downgrade).
        self._require_appcontainer = True
        self._platform: HostPlatform = detect_host_platform()
        self._extra_paths = [str(Path(p).expanduser().resolve()) for p in (extra_paths or [])]
        # Host-side file ops, gated by the same session inside LocalDirectExecutor.
        self._direct = LocalDirectExecutor(
            root, extra_paths=extra_paths, gateway_session=gateway_session
        )

    def jail_facts(self) -> JailFacts:
        if self.unavailable_reason:
            return JailFacts(
                strategy="unavailable",
                allow_network=self._allow_network,
                unavailable_reason=self.unavailable_reason,
            )
        strategy = _resolved_strategy(self._manager)
        if strategy is None:
            return JailFacts(strategy="none", allow_network=self._allow_network)
        return _sandbox_jail_facts(
            strategy, self._allow_network, require_appcontainer=self._require_appcontainer
        )

    @property
    def strategy(self) -> IsolationStrategy | None:
        """The confining tier commands run under (``None`` when there is none)."""
        return None if self.unavailable_reason else _resolved_strategy(self._manager)

    def workdir(self) -> str:
        return str(self.root)

    def _uses_windows_toolchain(self) -> bool:
        return (
            not self.unavailable_reason
            and _resolved_strategy(self._manager) == IsolationStrategy.WINDOWS_APPCONTAINER
        )

    @property
    def toolchain(self) -> WindowsToolchain | None:
        """The Windows agent toolchain used inside the AppContainer (else ``None``)."""
        if not self._uses_windows_toolchain():
            return None
        if not self._toolchain_resolved:
            arg = self._toolchain_arg
            if arg == AUTO_TOOLCHAIN:
                self._toolchain = discover_toolchain()
            else:
                self._toolchain = arg if isinstance(arg, WindowsToolchain) else None
            self._toolchain_resolved = True
        return self._toolchain

    def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult:
        if self._uses_windows_toolchain():
            # BusyBox sh from the Locus toolchain (bash/WSL are unreachable there).
            return self.run(["sh", "-c", script], timeout=timeout)
        return self.run(["bash", "-lc", script], timeout=timeout)

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
        decision = self._gate("process_exec", str(self.root), command=command)
        if not decision.allowed:
            return _blocked_result(decision, self.backend, self.unavailable_reason)
        return self._spawn(command, timeout=timeout)

    def _spawn(self, command: list[str], *, timeout: int) -> ExecResult:
        import time as _time

        if self.unavailable_reason:
            # Fail closed even under a permissive authorizer: there is no jail here.
            return ExecResult(
                exit_code=GATEWAY_BLOCKED_EXIT_CODE,
                stdout="",
                stderr=f"[denied] {self.unavailable_reason}",
                duration_seconds=0.0,
                backend=self.backend,
            )
        toolchain: WindowsToolchain | None = None
        if self._uses_windows_toolchain() and needs_toolchain(command):
            toolchain = self.toolchain
            if toolchain is None:
                return ExecResult(
                    exit_code=TOOLCHAIN_MISSING_EXIT_CODE,
                    stdout="",
                    stderr=f"[toolchain missing] {MISSING_TOOLCHAIN_HINT}",
                    duration_seconds=0.0,
                    backend=self.backend,
                )
            # Resolved only after the gateway allowed the logical name.
            command = toolchain.resolve(command)
        elif self._uses_windows_toolchain():
            toolchain = self.toolchain
        executable = command[0] if command else ""
        policy = SandboxPolicy(
            platform=self._platform,
            allow_network=self._allow_network,
            allowed_read_paths=[str(self.root), *self._extra_paths],
            allowed_write_paths=[str(self.root), *self._extra_paths],
            allowed_executables=[executable],
            timeout_seconds=timeout,
            require_appcontainer=self._require_appcontainer,
            toolchain_root=str(toolchain.root) if toolchain is not None else "",
        )
        explicit_env = minimal_agent_env(self.env, base={})
        spec = ExecutionSpec(
            tool_id="coding", command=command, cwd=str(self.root), env=explicit_env
        )
        plan = self._manager.plan(spec, policy)
        if plan.backend.startswith("k8s-"):
            raise NotImplementedError(
                "K8s sandbox execution is the workflow engine's responsibility; "
                "the harness cannot exec a pod spec in-process."
            )
        if plan.strategy == IsolationStrategy.HARDENED_DOCKER:
            # The docker CLI gets DOCKER_* only; the container gets explicit vars (-e).
            run_env = docker_cli_env()
        else:
            # bwrap / seatbelt / the Windows launcher pass their env to the child.
            run_env = minimal_agent_env(
                self.env, path_prepend=toolchain.path_dirs() if toolchain is not None else None
            )
        launcher_cwd = plan.metadata.get("launcher_cwd") or None
        start = _time.time()
        try:
            proc = subprocess.run(
                plan.command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=launcher_cwd,
            )
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


class DockerContainerExecutor(_GatedExecutor):
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
        gateway_session: GatewaySession | None = None,
        inspect_network_mode: Callable[[str], str] | None = None,
    ) -> None:
        self.container_id = container_id
        self.gateway_session = gateway_session
        self._workdir = workdir_path
        self._docker_host = docker_host or os.getenv("DOCKER_HOST") or ""
        self._docker = docker_bin
        # Derives the network fact from the container itself (docker inspect).
        self._inspect = inspect_network_mode or self._docker_network_mode
        self._network_mode: str | None = None

    def workdir(self) -> str:
        return self._workdir

    def allows(self, path: str) -> bool:
        abs_path = self._abs(path)
        wd = self._workdir.rstrip("/") or "/"
        return abs_path == wd or abs_path.startswith(wd + "/")

    def jail_facts(self) -> JailFacts:
        """The container is not ours: its root fs and user are unknown, so only the
        network fact is reported -- derived from the container itself (docker
        inspect), never from the caller. Unknown network counts as enabled."""
        if self._network_mode is None:
            try:
                self._network_mode = str(self._inspect(self.container_id) or "").strip()
            except Exception:  # noqa: BLE001 - an uninspectable container is not jailed
                self._network_mode = ""
        return JailFacts(strategy="docker-exec", allow_network=self._network_mode != "none")

    def _docker_network_mode(self, container_id: str) -> str:
        """Platform inspection of the container we exec into (not an agent action)."""
        proc = subprocess.run(
            [self._docker, "inspect", "--format", "{{.HostConfig.NetworkMode}}", container_id],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._docker_env(),
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _docker_env(self) -> dict[str, str]:
        return docker_cli_env(self._docker_host)

    def _spawn(self, inner: list[str], *, timeout: int) -> ExecResult:
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
        return self.run(["bash", "-lc", script], timeout=timeout)

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
        # Run through bash -lc so PATH/activate scripts behave like a shell.
        if len(command) == 3 and command[0] in ("bash", "sh") and command[1] in ("-lc", "-c"):
            inner = ["bash", "-lc", command[2]]
        else:
            inner = ["bash", "-lc", " ".join(shlex.quote(c) for c in command)]
        decision = self._gate("process_exec", self._workdir, command=inner)
        if not decision.allowed:
            return _blocked_result(decision, self.backend)
        return self._spawn(inner, timeout=timeout)

    def read_file(self, path: str) -> str | None:
        decision = self._gate("file_read", self._abs(path))
        if not decision.allowed:
            raise GatewayBlocked(decision)
        res = self._spawn(["cat", "--", self._abs(path)], timeout=30)
        if res.exit_code != 0:
            return None
        return res.stdout

    def write_file(self, path: str, content: str) -> None:
        # base64-pipe to avoid quoting hazards with arbitrary content.
        import base64

        target = self._abs(path)
        decision = self._gate("file_write", target)
        if not decision.allowed:
            raise GatewayBlocked(decision)
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        script = (
            f'mkdir -p "$(dirname {shlex.quote(target)})" && '
            f"printf %s {shlex.quote(b64)} | base64 -d > {shlex.quote(target)}"
        )
        res = self._spawn(["bash", "-lc", script], timeout=60)
        if res.exit_code != 0:
            raise RuntimeError(f"write_file failed in container: {res.stderr or res.stdout}")

    def exists(self, path: str) -> bool:
        decision = self._gate("file_read", self._abs(path))
        if not decision.allowed:
            return False
        res = self._spawn(["test", "-e", self._abs(path)], timeout=15)
        return res.exit_code == 0

    def _abs(self, path: str) -> str:
        if path.startswith("/"):
            return path
        return f"{self._workdir.rstrip('/')}/{path}"


# ---------------------------------------------------------------------------
# Default selection — sandboxed execution is the default
# ---------------------------------------------------------------------------


def sandbox_opt_out() -> bool:
    """Explicit dev opt-out: ``LOCUS_SANDBOX_AGENTS=0`` selects LocalDirectExecutor,
    whose process execution tool_jail still denies (no unconfined exec)."""
    flag = str(os.getenv("LOCUS_SANDBOX_AGENTS") or "").strip().lower()
    return flag in {"0", "false", "no", "off"}


def default_executor(
    root: str | Path,
    *,
    extra_paths: list[str] | None = None,
    gateway_session: GatewaySession | None = None,
    allow_network: bool = False,
    env: dict[str, str] | None = None,
) -> LocalDirectExecutor | LocalSandboxExecutor:
    """The harness executor for a host workspace: the platform's confining tier.

    Windows → AppContainer + Job Object (require_appcontainer); macOS → seatbelt;
    Linux → bubblewrap; else hardened Docker when available. With no confining
    tier, the executor reports the ``unavailable`` tier, so tool_jail denies every
    exec with an actionable reason (fail closed). ``LOCUS_SANDBOX_AGENTS=0`` is the
    explicit dev opt-out to LocalDirectExecutor -- also denied by tool_jail.
    """
    if sandbox_opt_out():
        return LocalDirectExecutor(
            root, env=env, extra_paths=extra_paths, gateway_session=gateway_session
        )
    selection = select_confining_strategy()
    if selection.strategy is None:
        return LocalSandboxExecutor(
            root,
            allow_network=allow_network,
            extra_paths=extra_paths,
            gateway_session=gateway_session,
            env=env,
            unavailable_reason=selection.reason,
        )
    return LocalSandboxExecutor(
        root,
        manager=SandboxManager(force_strategy=selection.strategy),
        allow_network=allow_network,
        extra_paths=extra_paths,
        gateway_session=gateway_session,
        env=env,
    )
