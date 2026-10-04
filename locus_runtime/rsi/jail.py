"""The OS jail for the RSI candidate instance (LOCUS-379).

The candidate instance (:mod:`.candidate`) runs agent-written Locus code. Before
LOCUS-379 it ran as the principal's OS user with only an environment scrub; now
the candidate's Python process itself is confined, per platform:

=============  ==================================================================
tier           how
=============  ==================================================================
appcontainer   Windows: its own AppContainer profile (``com.lattix.locus.rsi-candidate``)
               with **no capabilities** (no network, loopback included), a Job Object
               (memory/process caps, kill-on-close, UI restrictions), and ACL grants to
               the profile SID on Locus-created directories only.
seatbelt       macOS: ``sandbox-exec`` with a generated deny-by-default profile (no
               ``network*`` rule at all, no ``mach-lookup``: no keychain).
bwrap          Linux: bubblewrap with every namespace unshared (network included), a
               tmpfs ``/tmp``, read-only binds of the system directories, the
               interpreter and the code copy, and read-write binds of the home and the
               workspace only. The user's home is never mounted.
=============  ==================================================================

What the candidate can read: the system files every jailed program needs, its
interpreter (on Windows a Locus-owned read-only copy, see :func:`ensure_runtime`),
a read-only copy of the candidate checkout's code and the trusted entry script.
What it can write: its temp home and the sample's workspace. Everything else it
needs crosses the stdio bridge (:mod:`.bridge`).

The agent's own commands (``exec`` over the bridge) run in the **tool jail**: the
same tier with a separate profile (``com.lattix.locus.rsi-tools`` on Windows), the
workspace as the only writable root and no network -- not in the candidate's jail,
so a command cannot read the candidate's home or rewrite its audit log.

Pure helpers (availability, bwrap argv, seatbelt profile, runtime key) are
unit-tested on every OS; the launchers run on their platform only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal

from locus_runtime.sandbox import (
    HostPlatform,
    detect_host_platform,
    windows_appcontainer_supported,
)

Isolation = Literal["appcontainer", "seatbelt", "bwrap", "none"]
JAILED_TIERS: tuple[str, ...] = ("appcontainer", "seatbelt", "bwrap")
ISOLATION_NONE = "none"
#: The explicit, loud opt-out: run the candidate without an OS jail (never promotable).
UNJAILED_ENV = "LOCUS_RSI_CANDIDATE_UNJAILED"
SEATBELT_BIN = "/usr/bin/sandbox-exec"
#: Bumped when the Windows runtime copy's layout changes (part of the cache key).
RUNTIME_LAYOUT = "1"
RUNTIME_MARKER = ".locus-rsi-runtime.json"
#: stdlib directories never copied into the Windows runtime (tests, GUI, installers).
_STDLIB_SKIP = frozenset(
    {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3"}
)
#: site-packages entries never copied: editable-install hooks pointing at the
#: evaluator's own checkout, and coverage's start-up hook.
_SITE_SKIP_PREFIXES = ("__editable__", "_editable_impl_", "a1_coverage")
_CODE_DIRS = ("locus_runtime", "locus_tooling", "policies")
_CODE_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"
)
#: Linux system paths bound read-only (``--ro-bind-try``; symlinked ones are recreated).
LINUX_SYSTEM_PATHS: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/lib32",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
    "/etc/mime.types",
)
#: macOS system paths readable inside the seatbelt profile (never ``/Users``).
MACOS_SYSTEM_PATHS: tuple[str, ...] = (
    "/System",
    "/usr/lib",
    "/usr/share",
    "/usr/bin",
    "/bin",
    "/private/var/db/dyld",
    "/private/var/db/timezone",
    "/private/etc/localtime",
    "/Library/Developer/CommandLineTools",
)
#: Device files the seatbelt profile allows (read/write data only).
MACOS_DEVICES: tuple[str, ...] = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
#: Bounded output a tool command returns over the bridge.
MAX_TOOL_OUTPUT = 1024 * 1024


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class JailAvailability:
    """The candidate jail tier on this host, or why there is none."""

    tier: str | None
    platform: str
    reason: str

    @property
    def available(self) -> bool:
        return self.tier is not None


def jail_availability(
    *,
    platform: HostPlatform | None = None,
    which: Callable[[str], str | None] | None = None,
    seatbelt_available: bool | None = None,
    appcontainer_available: bool | None = None,
) -> JailAvailability:
    """Pick the candidate jail tier (inputs injectable; pure apart from probing).

    Hardened Docker is a valid *tool* jail, but not a candidate jail here: the
    candidate needs the host interpreter and stdio bridge, so no tier means the
    scorecard cannot run isolated on this host."""
    host = platform or detect_host_platform()
    find = which or shutil.which
    if host == HostPlatform.WINDOWS:
        ok = (
            windows_appcontainer_supported()
            if appcontainer_available is None
            else bool(appcontainer_available)
        )
        if ok:
            return JailAvailability("appcontainer", host.value, "Windows AppContainer + Job Object")
        return JailAvailability(
            None, host.value, "the Windows AppContainer APIs (userenv.dll) are unavailable"
        )
    if host == HostPlatform.MACOS:
        ok = (
            Path(SEATBELT_BIN).is_file() if seatbelt_available is None else bool(seatbelt_available)
        )
        if ok:
            return JailAvailability("seatbelt", host.value, "macOS seatbelt (sandbox-exec)")
        return JailAvailability(None, host.value, f"{SEATBELT_BIN} is missing")
    if find("bwrap"):
        return JailAvailability("bwrap", host.value, "Linux bubblewrap")
    return JailAvailability(
        None,
        host.value,
        "bubblewrap is not installed (apt install bubblewrap / dnf install bubblewrap)",
    )


def unjailed_requested(env: Mapping[str, str] | None = None) -> bool:
    """``LOCUS_RSI_CANDIDATE_UNJAILED=1``: the explicit opt-out (exactly ``1``)."""
    source = os.environ if env is None else env
    return str(source.get(UNJAILED_ENV) or "").strip() == "1"


# --------------------------------------------------------------------------- #
# Layout and the POSIX launch commands (pure)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class JailLayout:
    """What a jailed process may touch besides the platform's system files."""

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()


def _dedupe(paths: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        text = str(path)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def bwrap_argv(
    layout: JailLayout,
    command: Sequence[str],
    *,
    cwd: str = "",
    system_paths: Sequence[str] = LINUX_SYSTEM_PATHS,
    is_symlink: Callable[[str], bool] = os.path.islink,
    readlink: Callable[[str], str] = os.readlink,
) -> list[str]:
    """bubblewrap argv: every namespace unshared (no network), system paths and
    ``layout.read`` read-only, ``layout.write`` read-write, tmpfs ``/tmp``, no
    user home. Mount order matters: the tmpfs comes before binds below it."""
    args = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for path in system_paths:
        if is_symlink(path):
            args += ["--symlink", readlink(path), path]
        else:
            args += ["--ro-bind-try", path, path]
    for path in _dedupe(layout.read):
        args += ["--ro-bind", path, path]
    for path in _dedupe(layout.write):
        args += ["--bind", path, path]
    if cwd:
        args += ["--chdir", cwd]
    return [*args, "--", *command]


def _sb_string(path: str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(
    layout: JailLayout,
    *,
    system_paths: Sequence[str] = MACOS_SYSTEM_PATHS,
    exec_paths: Sequence[str] = (),
) -> str:
    """A deny-by-default seatbelt profile (pure).

    Allowed: reading the system paths and ``layout.read``; reading and writing
    ``layout.write``; the null/random devices; executing only under
    ``exec_paths`` (the interpreter, or the system paths for the tool jail);
    signals to itself; sysctl reads; path metadata (``stat``, needed to resolve
    paths; it does not allow listing a directory or reading a file). No
    ``network*`` rule (all sockets denied), no ``mach-lookup`` (no keychain, no
    other system service), no IPC."""
    read = _dedupe([*system_paths, *layout.read])
    write = _dedupe(layout.write)
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
    ]
    if read:
        rules.append(
            "(allow file-read* " + " ".join(f"(subpath {_sb_string(p)})" for p in read) + ")"
        )
    if write:
        subpaths = " ".join(f"(subpath {_sb_string(p)})" for p in write)
        rules.append(f"(allow file-read* file-write* {subpaths})")
    devices = " ".join(f"(literal {_sb_string(p)})" for p in MACOS_DEVICES)
    rules.append(f"(allow file-read-data file-write-data {devices})")
    executable = _dedupe(exec_paths)
    if executable:
        rules.append(
            "(allow process-exec "
            + " ".join(f"(subpath {_sb_string(p)})" for p in executable)
            + ")"
        )
    return "\n".join(rules) + "\n"


def seatbelt_argv(
    layout: JailLayout, command: Sequence[str], *, exec_paths: Sequence[str] = ()
) -> list[str]:
    """``sandbox-exec`` argv. Seatbelt matches real paths (``/var`` is
    ``/private/var`` on macOS), so every root is resolved first."""
    real = JailLayout(
        read=tuple(os.path.realpath(p) for p in layout.read),
        write=tuple(os.path.realpath(p) for p in layout.write),
    )
    executable = [os.path.realpath(p) for p in exec_paths]
    return [SEATBELT_BIN, "-p", seatbelt_profile(real, exec_paths=executable), *command]


# --------------------------------------------------------------------------- #
# Launch
# --------------------------------------------------------------------------- #
class JailedProcess:
    """A running jailed (or, for ``none``, plain) child with binary stdio pipes."""

    def __init__(
        self,
        *,
        stdin: IO[bytes],
        stdout: IO[bytes],
        stderr: IO[bytes],
        wait: Callable[[float | None], int | None],
        kill: Callable[[], None],
        close: Callable[[], None] = lambda: None,
    ) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._wait = wait
        self._kill = kill
        self._close = close

    def wait(self, timeout: float | None = None) -> int | None:
        return self._wait(timeout)

    def kill(self) -> None:
        self._kill()

    def close(self) -> None:
        for stream in (self.stdin, self.stdout, self.stderr):
            try:
                stream.close()
            except OSError:
                pass
        self._close()


def _popen(argv: Sequence[str], *, env: Mapping[str, str], cwd: str) -> JailedProcess:
    proc = subprocess.Popen(  # noqa: S603 - argv list, no shell; the jail is in argv
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        cwd=cwd or None,
        start_new_session=os.name != "nt",
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    def wait(timeout: float | None) -> int | None:
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    return JailedProcess(
        stdin=proc.stdin, stdout=proc.stdout, stderr=proc.stderr, wait=wait, kill=proc.kill
    )


def _spawn_appcontainer(
    command: Sequence[str],
    *,
    profile: str,
    env: Mapping[str, str],
    cwd: str,
    memory_bytes: int,
    active_processes: int,
) -> JailedProcess:
    from locus_runtime.rsi import win_appcontainer as wac

    if sys.platform != "win32":
        raise wac.AppContainerError("AppContainer confinement is Windows-only")
    import _winapi

    pipes = [wac.inheritable_pipe() for _ in range(3)]
    child_ends = (pipes[0][0], pipes[1][1], pipes[2][1])
    parent_ends = (pipes[0][1], pipes[1][0], pipes[2][0])
    try:
        for handle in child_ends:
            os.set_handle_inheritable(handle, True)
        child = wac.spawn(
            command,
            profile=profile,
            env=env,
            cwd=cwd,
            stdin=child_ends[0],
            stdout=child_ends[1],
            stderr=child_ends[2],
            memory_bytes=memory_bytes,
            active_processes=active_processes,
        )
    except BaseException:
        for handle in (*child_ends, *parent_ends):
            _winapi.CloseHandle(handle)
        raise
    for handle in child_ends:
        _winapi.CloseHandle(handle)
    return JailedProcess(
        stdin=wac.handle_to_file(parent_ends[0], "w"),
        stdout=wac.handle_to_file(parent_ends[1], "r"),
        stderr=wac.handle_to_file(parent_ends[2], "r"),
        wait=child.wait,
        kill=child.kill,
        close=child.close,
    )


def launch(
    tier: str,
    command: Sequence[str],
    *,
    layout: JailLayout,
    env: Mapping[str, str],
    cwd: str,
    profile: str = "",
    exec_paths: Sequence[str] = (),
    memory_bytes: int = 0,
    active_processes: int = 0,
) -> JailedProcess:
    """Start ``command`` in ``tier`` (``none`` = a plain child, the unjailed opt-out).

    On Windows the caller must have granted ``layout`` to the profile's SID
    (:func:`grant_layout`); the AppContainer then enforces it."""
    if tier == "appcontainer":
        from locus_runtime.rsi.win_appcontainer import CANDIDATE_PROFILE

        return _spawn_appcontainer(
            command,
            profile=profile or CANDIDATE_PROFILE,
            env=env,
            cwd=cwd,
            memory_bytes=memory_bytes,
            active_processes=active_processes,
        )
    if tier == "seatbelt":
        return _popen(seatbelt_argv(layout, command, exec_paths=exec_paths), env=env, cwd=cwd)
    if tier == "bwrap":
        return _popen(bwrap_argv(layout, command, cwd=cwd), env=env, cwd=cwd)
    if tier == ISOLATION_NONE:
        return _popen(command, env=env, cwd=cwd)
    raise ValueError(f"unknown jail tier {tier!r}")


def grant_layout(tier: str, profile: str, layout: JailLayout) -> None:
    """Windows: grant ``layout`` to the profile's AppContainer SID (fail closed).
    A no-op for the POSIX tiers, whose layout is in the launch command."""
    if tier != "appcontainer":
        return
    from locus_runtime.rsi import win_appcontainer as wac

    sid = wac.profile_sid(profile)
    wac.grant_paths(sid, read=layout.read, write=layout.write)


class _Drain(threading.Thread):
    """Read a stream to EOF, keeping at most ``limit`` bytes (the tail)."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True, name="rsi-drain")
        self._stream = stream
        self._limit = limit
        self.data = bytearray()
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(65536)
                if not chunk:
                    return
                self.data += chunk
                if len(self.data) > self._limit:
                    del self.data[: len(self.data) - self._limit]
                    self.truncated = True
        except (OSError, ValueError):
            return

    def text(self) -> str:
        prefix = "[output truncated]\n" if self.truncated else ""
        return prefix + bytes(self.data).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ToolResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


def run_tool(
    tier: str,
    command: Sequence[str],
    *,
    layout: JailLayout,
    env: Mapping[str, str],
    cwd: str,
    timeout: float,
    profile: str = "",
    exec_paths: Sequence[str] = (),
    memory_bytes: int = 512 * 1024**2,
    active_processes: int = 64,
) -> ToolResult:
    """Run one agent command in the tool jail and collect its bounded output."""
    started = time.monotonic()
    proc = launch(
        tier,
        command,
        layout=layout,
        env=env,
        cwd=cwd,
        profile=profile,
        exec_paths=exec_paths,
        memory_bytes=memory_bytes,
        active_processes=active_processes,
    )
    try:
        proc.stdin.close()  # the agent's commands get no stdin
        out, err = _Drain(proc.stdout, MAX_TOOL_OUTPUT), _Drain(proc.stderr, MAX_TOOL_OUTPUT)
        out.start()
        err.start()
        code = proc.wait(timeout)
        timed_out = code is None
        if timed_out:
            proc.kill()
            code = proc.wait(10.0)
        out.join(10.0)
        err.join(10.0)
        return ToolResult(
            exit_code=124 if timed_out else int(code if code is not None else 1),
            stdout=out.text(),
            stderr=err.text(),
            duration_seconds=round(time.monotonic() - started, 3),
            timed_out=timed_out,
        )
    finally:
        proc.close()


# --------------------------------------------------------------------------- #
# The candidate's code and interpreter
# --------------------------------------------------------------------------- #
def copy_code(checkout: Path, dest: Path) -> Path:
    """Copy the importable code of a checkout (``locus_runtime``, ``locus_tooling``
    and the policy bundle) to ``dest``: never ``.git`` (remote URLs, credentials),
    never caches. Returns ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in _CODE_DIRS:
        source = Path(checkout) / name
        if source.is_dir():
            shutil.copytree(source, dest / name, ignore=_CODE_IGNORE, dirs_exist_ok=True)
    return dest


@dataclass(frozen=True)
class InterpreterInfo:
    executable: str
    version: str
    prefix: str
    base_prefix: str
    site_packages: tuple[str, ...]

    def read_roots(self) -> list[str]:
        """Directories a POSIX jail binds read-only so this interpreter runs."""
        roots = [self.prefix, self.base_prefix, *self.site_packages]
        exe_dir = str(Path(self.executable).resolve().parent)
        roots.append(exe_dir)
        return _dedupe(r for r in roots if r)


_INFO_SCRIPT = (
    "import json, site, sys; print(json.dumps({'executable': sys.executable, "
    "'version': sys.version, 'prefix': sys.prefix, 'base_prefix': sys.base_prefix, "
    "'site_packages': site.getsitepackages()}))"
)


def _package_dirs(paths: Sequence[str], prefix: str) -> list[str]:
    """The environment's own package directories (``site.getsitepackages()`` also
    lists the prefix itself on Windows, which must never be copied whole)."""
    return [
        str(p)
        for p in paths
        if str(p).startswith(prefix)
        and Path(str(p)).name.lower() in {"site-packages", "dist-packages"}
    ]


def interpreter_info(python: str) -> InterpreterInfo:
    """Describe ``python`` (this process when it is this interpreter). The
    interpreter is evaluator configuration, not candidate code; it is asked with
    ``-I`` (no environment, no user site, no script directory)."""
    if Path(python).resolve() == Path(sys.executable).resolve():
        import site

        data: dict[str, Any] = {
            "executable": sys.executable,
            "version": sys.version,
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "site_packages": site.getsitepackages(),
        }
    else:
        done = subprocess.run(
            [python, "-I", "-c", _INFO_SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if done.returncode != 0:
            raise RuntimeError(f"the candidate interpreter did not start: {done.stderr[-200:]}")
        data = json.loads(done.stdout)
    return InterpreterInfo(
        executable=str(data["executable"]),
        version=str(data["version"]),
        prefix=str(data["prefix"]),
        base_prefix=str(data["base_prefix"]),
        site_packages=tuple(
            _package_dirs([str(p) for p in data.get("site_packages") or ()], str(data["prefix"]))
        ),
    )


def runtime_key(info: InterpreterInfo, *, site_packages: bool) -> str:
    """Cache key of the Windows runtime copy: interpreter + installed packages. Pure
    apart from listing the site-packages directories."""
    listing: list[tuple[str, str, int]] = []
    if site_packages:
        for root in info.site_packages:
            base = Path(root)
            if not base.is_dir():
                continue
            for entry in sorted(base.iterdir()):
                try:
                    listing.append((root, entry.name, entry.stat().st_mtime_ns))
                except OSError:
                    continue
    blob = json.dumps(
        {
            "layout": RUNTIME_LAYOUT,
            "version": info.version,
            "prefix": info.prefix,
            "base_prefix": info.base_prefix,
            "site": listing,
            "include_site": site_packages,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _force_rmtree(path: Path) -> None:
    def force(func: Callable[..., Any], target: str, _exc: BaseException) -> None:
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=force)


def _skip_site(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n.startswith(_SITE_SKIP_PREFIXES)]


def build_runtime_tree(info: InterpreterInfo, dest: Path, *, site_packages: bool) -> None:
    """Copy a self-contained Windows interpreter into ``dest``: the base install's
    executables and DLLs, ``DLLs/``, the stdlib (without tests and GUI) and,
    optionally, the environment's site-packages (without editable-install hooks).
    ``python.exe`` finds its home by the ``Lib/os.py`` landmark next to it."""
    base = Path(info.base_prefix)
    for entry in base.iterdir():
        if entry.is_file() and entry.suffix.lower() in {".exe", ".dll"}:
            shutil.copy2(entry, dest / entry.name)
    if (base / "DLLs").is_dir():
        shutil.copytree(base / "DLLs", dest / "DLLs", dirs_exist_ok=True)

    def skip_stdlib(directory: str, names: list[str]) -> list[str]:
        if Path(directory) == base / "Lib":
            return [n for n in names if n in _STDLIB_SKIP]
        return []

    shutil.copytree(base / "Lib", dest / "Lib", ignore=skip_stdlib, dirs_exist_ok=True)
    target = dest / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    if site_packages:
        for root in info.site_packages:
            if Path(root).is_dir():
                shutil.copytree(root, target, ignore=_skip_site, dirs_exist_ok=True)


def ensure_runtime(
    info: InterpreterInfo,
    cache_root: Path,
    *,
    grant: Callable[[Path], None],
    site_packages: bool = True,
    keep: int = 2,
) -> Path:
    """The Locus-owned, read-only Windows runtime for ``info``; built on first use.

    The AppContainer cannot read the user's Python install (``AppData``) and Locus
    does not change ACLs on directories it does not own (D-23), so the interpreter
    is copied once into ``cache_root/<key>/`` (keyed by interpreter and installed
    packages). ``grant(dir)`` gives the candidate profile read+execute on the empty
    build directory **before** the copy, so every copied file inherits it. Older
    copies beyond ``keep`` are removed. Returns the copy's ``python.exe``."""
    key = runtime_key(info, site_packages=site_packages)
    cache_root.mkdir(parents=True, exist_ok=True)
    final = cache_root / key
    if not (final / RUNTIME_MARKER).is_file():
        build = Path(tempfile.mkdtemp(prefix=".build-", dir=cache_root))
        try:
            grant(build)
            build_runtime_tree(info, build, site_packages=site_packages)
            (build / RUNTIME_MARKER).write_text(
                json.dumps(
                    {"version": info.version, "source": info.prefix, "key": key},
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            try:
                os.replace(build, final)
            except OSError:
                if not (final / RUNTIME_MARKER).is_file():
                    raise
        finally:
            if build.exists():
                _force_rmtree(build)
    _prune_runtimes(cache_root, keep=max(1, keep), current=key)
    python = final / "python.exe"
    if not python.is_file():
        raise RuntimeError(f"the candidate runtime copy has no python.exe: {final}")
    return python


def _prune_runtimes(cache_root: Path, *, keep: int, current: str) -> None:
    built = [
        p
        for p in cache_root.iterdir()
        if p.is_dir() and p.name != current and (p / RUNTIME_MARKER).is_file()
    ]
    built.sort(key=lambda p: (p / RUNTIME_MARKER).stat().st_mtime, reverse=True)
    for stale in built[keep - 1 :]:
        _force_rmtree(stale)
