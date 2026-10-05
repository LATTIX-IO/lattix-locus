"""Three-tier hybrid sandbox for Locus agent tool execution.

Provides Codex-grade kernel-level isolation that adapts to deployment context:

  1. **Kernel mode** (laptop/desktop) — bubblewrap + seccomp directly on host.
     No Docker daemon required.  Fastest startup (~1 ms).
  2. **Hardened Docker mode** (docker-compose local-secure) — Docker container
     with custom seccomp profile, read-only rootfs, resource limits, and
     network=none when disabled.
  3. **K8s mode** (hosted) — gVisor ``runsc`` or Kata Containers RuntimeClass
     selected via pod spec; the SandboxManager emits the spec but K8s
     enforces it.

The ``SandboxManager`` auto-detects which strategy is available and always
picks the strongest one.  All strategies share the same ``SandboxPolicy`` and
``ExecutionSpec`` contracts so the rest of the runtime is decoupled from the
isolation backend.
"""

from __future__ import annotations

import os
import platform as platform_module
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SANDBOX_RUNNER_IMAGE = "python:3.12.10-slim-bookworm"

#: Paths that must ALWAYS be mounted read-only even inside writable parents.
ALWAYS_READONLY_SUBPATHS: list[str] = [
    ".git",
    ".locus",
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".config/gcloud",
]

#: Default per-tool resource limits.
DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = "1.0"
DEFAULT_PID_LIMIT = 256
DEFAULT_TOOL_TIMEOUT_SECONDS = 60
SANDBOX_SECURITY_ROOT = Path(__file__).resolve().parent.parent / "docker" / "sandbox"
DEFAULT_SECCOMP_PROFILE_PATH = SANDBOX_SECURITY_ROOT / "seccomp-strict.json"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _restricted_process_allowed() -> bool:
    return _env_flag("LOCUS_ALLOW_RESTRICTED_PROCESS_SANDBOX", False)


def _validated_seccomp_profile_path() -> Path:
    configured = str(os.getenv("LOCUS_SECCOMP_PROFILE") or "").strip()
    candidate = Path(configured) if configured else DEFAULT_SECCOMP_PROFILE_PATH
    allowed_root = SANDBOX_SECURITY_ROOT.resolve(strict=True)
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Seccomp profile does not exist: {candidate}") from exc
    if resolved.suffix.lower() != ".json":
        raise RuntimeError("LOCUS_SECCOMP_PROFILE must point to a JSON seccomp profile")
    if resolved != allowed_root and not resolved.is_relative_to(allowed_root):
        raise RuntimeError("LOCUS_SECCOMP_PROFILE must stay within docker/sandbox")
    if not resolved.is_file():
        raise RuntimeError(f"Seccomp profile does not exist: {resolved}")
    return resolved


#: Host environment variables an agent command may inherit (matched case-insensitively,
#: because Windows environment names are case-insensitive). Everything else -- LOCUS_*
#: settings, API keys, tokens, cloud credentials -- is dropped before a spawn.
AGENT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "USER",
        "USERNAME",
        "LOGNAME",
        "SHELL",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TZ",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "APPDATA",
        # AppContainer process creation needs LOCALAPPDATA (Windows rewrites it to
        # the container's own AC directory); without it CreateProcessW fails (203).
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        "VIRTUAL_ENV",
    }
)

#: Extra host variables the docker CLI itself needs (they never reach the container:
#: only ``-e`` entries do).
DOCKER_CLI_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"}
)

_SECRET_NAME_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "SESSION",
    "COOKIE",
    "PRIVATE",
)


def is_secret_env_name(name: str) -> bool:
    """True for names an agent command must never receive (LOCUS_* and secret-like)."""
    upper = str(name or "").upper()
    if upper.startswith("LOCUS_"):
        return True
    return any(marker in upper for marker in _SECRET_NAME_MARKERS)


def prepend_path(env: dict[str, str], dirs: list[str]) -> dict[str, str]:
    """Return ``env`` with ``dirs`` first on PATH (Windows keys are case-insensitive;
    an existing ``Path``/``PATH`` key is reused). Duplicates are dropped."""
    out = dict(env)
    key = next((k for k in out if k.upper() == "PATH"), "PATH")
    existing = [p for p in str(out.get(key, "")).split(os.pathsep) if p]
    seen: set[str] = set()
    merged: list[str] = []
    for entry in [*dirs, *existing]:
        norm = os.path.normcase(entry)
        if norm in seen:
            continue
        seen.add(norm)
        merged.append(entry)
    out[key] = os.pathsep.join(merged)
    return out


def minimal_agent_env(
    extra: dict[str, str] | None = None,
    *,
    base: dict[str, str] | None = None,
    allow: frozenset[str] = AGENT_ENV_ALLOWLIST,
    path_prepend: list[str] | None = None,
) -> dict[str, str]:
    """The environment an agent command runs with: allowlisted host variables plus
    explicit per-run variables. Secret-like names are dropped even when passed
    explicitly (fail safe): credentials reach tools by reference, never via env.

    ``path_prepend`` puts directories first on PATH (the Windows agent toolchain:
    BusyBox, then the embeddable Python), ahead of explicit and host entries."""
    source = os.environ if base is None else base
    env = {key: value for key, value in source.items() if key.upper() in allow}
    for key, value in (extra or {}).items():
        if is_secret_env_name(key):
            continue
        env[str(key)] = str(value)
    if path_prepend:
        env = prepend_path(env, [str(d) for d in path_prepend])
    return env


def docker_cli_env(docker_host: str = "", *, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the docker CLI process: the agent minimum plus DOCKER_* only."""
    env = minimal_agent_env(base=base, allow=AGENT_ENV_ALLOWLIST | DOCKER_CLI_ENV_ALLOWLIST)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


def sandbox_runner_image() -> str:
    return (
        str(os.getenv("SANDBOX_RUNNER_IMAGE") or DEFAULT_SANDBOX_RUNNER_IMAGE).strip()
        or DEFAULT_SANDBOX_RUNNER_IMAGE
    )


# ---------------------------------------------------------------------------
# Platform & strategy enums
# ---------------------------------------------------------------------------


class HostPlatform(str, Enum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"


class IsolationStrategy(str, Enum):
    """Which isolation backend is in use."""

    KERNEL_BWRAP = "kernel-bwrap"
    KERNEL_SEATBELT = "kernel-seatbelt"
    WINDOWS_APPCONTAINER = "windows-appcontainer"
    HARDENED_DOCKER = "hardened-docker"
    K8S_GVISOR = "k8s-gvisor"
    K8S_KATA = "k8s-kata"
    RESTRICTED_PROCESS = "restricted-process"


# ---------------------------------------------------------------------------
# Data contracts (shared across all strategies)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxCapabilities:
    strict_syscalls: bool
    namespace_isolation: bool = False
    read_only_rootfs: bool = False
    network_namespace: bool = False


@dataclass
class SandboxPolicy:
    """Declarative security policy for a single tool execution."""

    platform: HostPlatform
    allow_network: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_read_paths: list[str] = field(default_factory=list)
    allowed_write_paths: list[str] = field(default_factory=list)
    allowed_executables: list[str] = field(default_factory=list)
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    cpu_limit: str = DEFAULT_CPU_LIMIT
    pid_limit: int = DEFAULT_PID_LIMIT
    timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS
    # Windows: the launcher must fail closed instead of degrading to the
    # resource-only Job-Object tier when AppContainer cannot be established.
    require_appcontainer: bool = True
    # Windows: the Locus-owned agent toolchain directory; the launcher grants the
    # AppContainer SID read+execute on it (once, idempotent). Empty: no toolchain.
    toolchain_root: str = ""

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            strict_syscalls=self.platform == HostPlatform.LINUX,
            namespace_isolation=self.platform == HostPlatform.LINUX,
            read_only_rootfs=True,
            network_namespace=not self.allow_network,
        )


@dataclass
class ExecutionSpec:
    tool_id: str
    command: list[str]
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    input_paths: list[str] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    requested_hosts: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    backend: str
    command: list[str]
    strategy: IsolationStrategy
    network_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Backwards compat alias
    @property
    def docker_command(self) -> list[str]:
        return self.command


@dataclass
class ExecutionPlanResult:
    executed: bool
    plan: ExecutionPlan


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


class _KernelBwrapStrategy:
    """Linux: bubblewrap + seccomp (Codex-equivalent)."""

    def build_command(self, spec: ExecutionSpec, policy: SandboxPolicy) -> list[str]:
        args: list[str] = ["bwrap"]

        # --- Filesystem isolation ---
        args += ["--ro-bind", "/", "/"]  # Read-only root

        # Minimal writable /dev
        args += ["--dev", "/dev"]

        # Writable /tmp (noexec)
        args += ["--tmpfs", "/tmp"]

        # Explicit writable mounts
        for writable in policy.allowed_write_paths:
            resolved = str(Path(writable).expanduser().resolve())
            args += ["--bind", resolved, resolved]

        # Re-protect sensitive subpaths inside writable parents
        for subpath in ALWAYS_READONLY_SUBPATHS:
            for writable in policy.allowed_write_paths:
                candidate = Path(writable).expanduser().resolve() / subpath
                if candidate.exists():
                    args += ["--ro-bind", str(candidate), str(candidate)]

        # --- Namespace isolation ---
        args += ["--unshare-user", "--unshare-pid", "--unshare-ipc"]
        if not policy.allow_network:
            args += ["--unshare-net"]

        # Fresh /proc
        args += ["--proc", "/proc"]

        # New session (prevents signal injection from parent terminal)
        args += ["--new-session"]

        # Die with parent (cleanup on crash)
        args += ["--die-with-parent"]

        # Working directory
        if spec.cwd:
            args += ["--chdir", spec.cwd]

        # --- Execute ---
        args += ["--"]
        args += spec.command

        return args


class _KernelSeatbeltStrategy:
    """macOS: sandbox-exec with generated seatbelt profile."""

    _SEATBELT_BIN = "/usr/bin/sandbox-exec"

    def build_command(self, spec: ExecutionSpec, policy: SandboxPolicy) -> list[str]:
        profile = self._generate_profile(policy)
        args = [self._SEATBELT_BIN, "-p", profile]

        # Parameter injection for readable/writable roots
        readable_roots = list(policy.allowed_read_paths) or [
            "/System",
            "/usr/lib",
            "/private/tmp",
            "/tmp",
        ]
        for idx, path in enumerate(readable_roots):
            args += [f"-DREADABLE_ROOT_{idx}={path}"]
        for idx, path in enumerate(policy.allowed_write_paths):
            args += [f"-DWRITABLE_ROOT_{idx}={path}"]

        args += ["--"]
        args += spec.command
        return args

    def _generate_profile(self, policy: SandboxPolicy) -> str:
        rules = ["(version 1)", "(deny default)"]

        # Process execution
        rules.append("(allow process-exec)")
        rules.append("(allow process-fork)")

        # File read
        readable_roots = list(policy.allowed_read_paths) or [
            "/System",
            "/usr/lib",
            "/private/tmp",
            "/tmp",
        ]
        for idx, _ in enumerate(readable_roots):
            rules.append(f'(allow file-read* (subpath (param "READABLE_ROOT_{idx}")))')

        # File write (only to allowed paths)
        for idx, _ in enumerate(policy.allowed_write_paths):
            rules.append(f'(allow file-write* (subpath (param "WRITABLE_ROOT_{idx}")))')

        # /tmp writable
        rules.append('(allow file-write* (subpath "/private/tmp"))')
        rules.append('(allow file-write* (subpath "/tmp"))')

        # Network
        if policy.allow_network:
            rules.append("(allow network-outbound)")
            rules.append("(allow network-inbound)")
            rules.append("(allow system-socket)")
        else:
            # Allow localhost-only for IPC
            rules.append("(allow system-socket (socket-domain AF_UNIX))")
            rules.append('(allow network-bind (local ip "localhost:*"))')

        # System basics
        rules.append("(allow sysctl-read)")
        rules.append("(allow mach-lookup)")
        rules.append("(allow signal (target self))")
        rules.append("(allow iokit-open)")

        return "\n".join(rules)


class _HardenedDockerStrategy:
    """Docker container with seccomp + read-only rootfs + resource limits."""

    def build_command(self, spec: ExecutionSpec, policy: SandboxPolicy) -> list[str]:
        args: list[str] = [
            "docker",
            "run",
            "--rm",
            # --- Security ---
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--user=1000:1000",
            "--ipc=private",
        ]

        # Seccomp profile
        args.append(f"--security-opt=seccomp={_validated_seccomp_profile_path()}")

        # --- Network ---
        if not policy.allow_network:
            args.append("--network=none")
        else:
            args.append("--network=locus-sandbox-internal")

        # --- Resource limits ---
        args.append(f"--memory={policy.memory_limit}")
        args.append(f"--cpus={policy.cpu_limit}")
        args.append(f"--pids-limit={policy.pid_limit}")

        # --- Filesystem ---
        # Writable /tmp inside read-only container
        args += ["--tmpfs", "/tmp:rw,noexec,nosuid,size=100m"]

        # Explicit writable mounts
        for path in policy.allowed_write_paths:
            resolved = str(Path(path).expanduser().resolve())
            args += ["-v", f"{resolved}:{resolved}:rw"]

        # Explicit read-only mounts
        for path in policy.allowed_read_paths:
            resolved = str(Path(path).expanduser().resolve())
            args += ["-v", f"{resolved}:{resolved}:ro"]

        # Working directory
        if spec.cwd:
            args += ["-w", spec.cwd]

        # Environment
        for key, value in spec.env.items():
            args += ["-e", f"{key}={value}"]

        # --- Image + command ---
        args.append(sandbox_runner_image())
        args.extend(spec.command)

        return args


#: Directory to start the Windows launcher in, so ``-m locus_runtime.win_sandbox``
#: imports this checkout's launcher whatever the caller's working directory is.
WIN_LAUNCHER_CWD = str(Path(__file__).resolve().parent.parent)


class _WindowsAppContainerStrategy:
    """Windows: confine the child via the win_sandbox launcher (AppContainer + Job
    Object; with ``require_appcontainer`` the launcher fails closed instead of
    degrading to the Job-Object-only tier). No Docker required."""

    def build_command(self, spec: ExecutionSpec, policy: SandboxPolicy) -> list[str]:
        import sys as _sys

        args = [
            _sys.executable,
            "-m",
            "locus_runtime.win_sandbox",
            "run",
            "--memory",
            str(policy.memory_limit),
            "--pids",
            str(policy.pid_limit),
            "--cpu",
            str(policy.cpu_limit),
            "--timeout",
            str(policy.timeout_seconds),
        ]
        if policy.allow_network:
            args.append("--allow-network")
        if policy.require_appcontainer:
            args.append("--require-appcontainer")
        for path in policy.allowed_read_paths:
            args += ["--read-path", str(Path(path).expanduser().resolve())]
        for path in policy.allowed_write_paths:
            args += ["--write-path", str(Path(path).expanduser().resolve())]
        if spec.cwd:
            args += ["--cwd", spec.cwd]
        if policy.toolchain_root:
            args += ["--toolchain-root", str(Path(policy.toolchain_root).resolve())]
        args += ["--", *spec.command]
        return args


class _RestrictedProcessStrategy:
    """Fallback: run command with minimal env sanitization (no sandbox)."""

    def build_command(self, spec: ExecutionSpec, policy: SandboxPolicy) -> list[str]:
        if not _restricted_process_allowed():
            raise PermissionError(
                "No supported sandbox backend is available; restricted-process fallback is disabled"
            )
        return spec.command


# ---------------------------------------------------------------------------
# Sandbox Manager (unified API)
# ---------------------------------------------------------------------------


class SandboxManager:
    """Selects the strongest available sandbox strategy for the deployment context."""

    def __init__(self, *, force_strategy: IsolationStrategy | None = None) -> None:
        self._forced = force_strategy
        self._strategy_name: IsolationStrategy | None = None

    @property
    def active_strategy(self) -> IsolationStrategy:
        if self._strategy_name is None:
            self._strategy_name = self._detect()
        return self._strategy_name

    def _detect(self) -> IsolationStrategy:
        if self._forced is not None:
            return self._forced

        profile = os.getenv("LOCUS_RUNTIME_PROFILE", "local-lightweight").lower()

        # K8s mode: if we're inside a pod, defer to RuntimeClass
        if profile == "hosted" or os.getenv("KUBERNETES_SERVICE_HOST"):
            return IsolationStrategy.K8S_GVISOR

        platform = detect_host_platform()

        # Prefer kernel sandbox if bubblewrap / seatbelt available
        if platform == HostPlatform.LINUX and shutil.which("bwrap"):
            return IsolationStrategy.KERNEL_BWRAP

        if platform == HostPlatform.MACOS and Path("/usr/bin/sandbox-exec").is_file():
            return IsolationStrategy.KERNEL_SEATBELT

        # Windows: AppContainer/Job-Object confinement via the win_sandbox
        # launcher (no Docker). Used for the native install, or opt-in elsewhere.
        if platform == HostPlatform.WINDOWS and (
            profile in {"local-native", "native"}
            or _env_flag("LOCUS_FORCE_WINDOWS_APPCONTAINER")
        ):
            return IsolationStrategy.WINDOWS_APPCONTAINER

        # Native desktop install is explicitly Dockerless: never fall back to a
        # docker daemon. Use the kernel sandbox above when present, else degrade
        # to restricted-process (Windows is handled above).
        if profile in {"local-native", "native"}:
            return IsolationStrategy.RESTRICTED_PROCESS

        # Fall back to hardened Docker if available
        if shutil.which("docker"):
            return IsolationStrategy.HARDENED_DOCKER

        # Last resort
        return IsolationStrategy.RESTRICTED_PROCESS

    def plan(self, spec: ExecutionSpec, policy: SandboxPolicy) -> ExecutionPlan:
        """Build an execution plan using the strongest available isolation."""
        strategy = self.active_strategy

        # --- Pre-execution validation (shared across all strategies) ---
        executable = spec.command[0] if spec.command else spec.tool_id
        if not policy.allowed_executables:
            raise PermissionError("No executables are allowlisted for sandbox execution")
        if executable not in policy.allowed_executables:
            raise PermissionError(f"Executable '{executable}' is not allowlisted")
        if policy.allow_network:
            if not policy.allowed_hosts:
                raise PermissionError("Network access requires an explicit host allowlist")
            if not spec.requested_hosts:
                raise PermissionError("Network access requires explicit requested hosts")
            requested = set(spec.requested_hosts)
            allowed = set(policy.allowed_hosts)
            if not requested.issubset(allowed):
                raise PermissionError(f"Requested hosts not allowlisted: {requested - allowed}")
        elif spec.requested_hosts:
            raise PermissionError("Requested hosts require allow_network=true")

        # --- Strategy dispatch ---
        if strategy == IsolationStrategy.KERNEL_BWRAP:
            cmd = _KernelBwrapStrategy().build_command(spec, policy)
            backend = "kernel-bwrap"
        elif strategy == IsolationStrategy.KERNEL_SEATBELT:
            cmd = _KernelSeatbeltStrategy().build_command(spec, policy)
            backend = "kernel-seatbelt"
        elif strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
            cmd = _WindowsAppContainerStrategy().build_command(spec, policy)
            backend = "windows-appcontainer"
        elif strategy == IsolationStrategy.HARDENED_DOCKER:
            cmd = _HardenedDockerStrategy().build_command(spec, policy)
            backend = "hardened-docker"
        elif strategy in (IsolationStrategy.K8S_GVISOR, IsolationStrategy.K8S_KATA):
            # In K8s mode we don't build a shell command; instead we return
            # pod spec metadata that the workflow engine uses to create the
            # Job/Pod with the correct RuntimeClass.
            return ExecutionPlan(
                backend=f"k8s-{strategy.value.split('-')[-1]}",
                command=spec.command,
                strategy=strategy,
                metadata=self._k8s_pod_spec(spec, policy, strategy),
            )
        else:
            cmd = _RestrictedProcessStrategy().build_command(spec, policy)
            backend = "restricted-process"

        network_name = "locus-sandbox-internal" if policy.allow_network else None
        metadata: dict[str, Any] = {}
        if strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
            metadata["launcher_cwd"] = WIN_LAUNCHER_CWD

        return ExecutionPlan(
            backend=backend,
            command=cmd,
            strategy=strategy,
            network_name=network_name,
            metadata=metadata,
        )

    @staticmethod
    def _k8s_pod_spec(
        spec: ExecutionSpec,
        policy: SandboxPolicy,
        strategy: IsolationStrategy,
    ) -> dict[str, Any]:
        """Generate K8s pod spec fragment for the workflow engine."""
        runtime_class = (
            "locus-sandbox-vm" if strategy == IsolationStrategy.K8S_KATA else "locus-sandbox"
        )
        return {
            "runtimeClassName": runtime_class,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "Localhost", "localhostProfile": "locus-strict.json"},
            },
            "container": {
                "image": sandbox_runner_image(),
                "command": spec.command,
                "workingDir": spec.cwd or "/workspace",
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "readOnlyRootFilesystem": True,
                },
                "resources": {
                    "limits": {"memory": policy.memory_limit, "cpu": policy.cpu_limit},
                    "requests": {"memory": policy.memory_limit, "cpu": policy.cpu_limit},
                },
            },
        }


# ---------------------------------------------------------------------------
# Legacy ToolJailService (updated to use SandboxManager)
# ---------------------------------------------------------------------------


class ToolJailService:
    """Backwards-compatible tool jail that delegates to SandboxManager."""

    def __init__(self, *, manager: SandboxManager | None = None) -> None:
        self._manager = manager or SandboxManager()

    @property
    def active_strategy(self) -> IsolationStrategy:
        return self._manager.active_strategy

    async def plan(self, spec: ExecutionSpec, policy: SandboxPolicy) -> ExecutionPlanResult:
        plan = self._manager.plan(spec, policy)
        return ExecutionPlanResult(executed=False, plan=plan)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


#: Strategies that confine a command on this host (what tool_jail accepts as a jail).
CONFINING_STRATEGIES: frozenset[IsolationStrategy] = frozenset(
    {
        IsolationStrategy.KERNEL_BWRAP,
        IsolationStrategy.KERNEL_SEATBELT,
        IsolationStrategy.WINDOWS_APPCONTAINER,
        IsolationStrategy.HARDENED_DOCKER,
    }
)

_SEATBELT_PATH = Path("/usr/bin/sandbox-exec")


def windows_appcontainer_supported() -> bool:
    """The AppContainer APIs the win_sandbox launcher calls exist on this host."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        userenv = ctypes.WinDLL("userenv")
        return all(
            hasattr(userenv, name)
            for name in ("CreateAppContainerProfile", "DeriveAppContainerSidFromAppContainerName")
        )
    except (OSError, AttributeError):
        return False


@dataclass(frozen=True)
class ConfinementSelection:
    """The confining strategy for harness execution on this host, or why there is none."""

    strategy: IsolationStrategy | None
    platform: HostPlatform
    reason: str


def _no_sandbox_hint(platform: HostPlatform, native: bool) -> str:
    docker = "" if native else " or install Docker (hardened-docker tier)"
    if platform == HostPlatform.LINUX:
        return (
            "no confining sandbox available on this host: install bubblewrap "
            f"(apt install bubblewrap / dnf install bubblewrap){docker}"
        )
    if platform == HostPlatform.MACOS:
        return (
            f"no confining sandbox available on this host: /usr/bin/sandbox-exec is missing{docker}"
        )
    return (
        "no confining sandbox available on this host: the Windows AppContainer APIs "
        f"(userenv.dll, Windows 8 / Server 2012 or later) are unavailable{docker}"
    )


def select_confining_strategy(
    *,
    platform: HostPlatform | None = None,
    which: Any = None,
    seatbelt_available: bool | None = None,
    appcontainer_available: bool | None = None,
    profile: str | None = None,
) -> ConfinementSelection:
    """Pick the platform's confining tier for harness execution (fail closed).

    Windows → AppContainer (+ Job Object, require_appcontainer); macOS → seatbelt;
    Linux → bubblewrap; otherwise hardened Docker when the docker CLI exists (never
    under the Dockerless native profile). ``strategy`` is ``None`` when nothing can
    confine a command here; ``reason`` then says what to install. Inputs are
    injectable so selection is testable on any host.
    """
    host = platform or detect_host_platform()
    find = which or shutil.which
    run_profile = str(
        profile if profile is not None else os.getenv("LOCUS_RUNTIME_PROFILE") or ""
    ).lower()
    native = run_profile in {"local-native", "native", "local_native"}
    if host == HostPlatform.WINDOWS:
        available = (
            windows_appcontainer_supported()
            if appcontainer_available is None
            else appcontainer_available
        )
        if available:
            return ConfinementSelection(
                IsolationStrategy.WINDOWS_APPCONTAINER, host, "windows AppContainer + Job Object"
            )
    elif host == HostPlatform.MACOS:
        available = _SEATBELT_PATH.is_file() if seatbelt_available is None else seatbelt_available
        if available:
            return ConfinementSelection(IsolationStrategy.KERNEL_SEATBELT, host, "macOS seatbelt")
    elif find("bwrap"):
        return ConfinementSelection(IsolationStrategy.KERNEL_BWRAP, host, "linux bubblewrap")
    if not native and find("docker"):
        return ConfinementSelection(
            IsolationStrategy.HARDENED_DOCKER, host, "hardened docker container"
        )
    return ConfinementSelection(None, host, _no_sandbox_hint(host, native))


def detect_host_platform(system_name: str | None = None) -> HostPlatform:
    normalized = str(system_name or platform_module.system()).strip().lower()
    if normalized == "darwin":
        return HostPlatform.MACOS
    if normalized == "windows":
        return HostPlatform.WINDOWS
    return HostPlatform.LINUX
