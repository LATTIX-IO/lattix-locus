"""The Locus-managed Windows agent toolchain (LOCUS-333).

Inside the Windows AppContainer only binaries readable by ALL APPLICATION PACKAGES
run (``cmd``, ``git``); the user's Python, Git-bash and WSL ``bash`` are not
reachable. Locus therefore fetches a small toolchain -- BusyBox-w64 (``sh`` and
the usual POSIX utilities) and the CPython embeddable package -- into a
Locus-owned directory, ``<app_home>/toolchain`` (see
``locus_tooling.native_binaries.provision_toolchain``), and:

* grants **read + execute on that directory only**, to the Locus AppContainer SID
  only (never ALL APPLICATION PACKAGES; no ACL change on any directory Locus does
  not own). The grant is idempotent: a stamp file named after the SID records it.
* runs ``run_shell`` as ``busybox.exe sh -c ...`` and maps ``python``/``python3``
  to the toolchain interpreter (:meth:`WindowsToolchain.resolve`). The gateway and
  tool_jail still see -- and allowlist -- the logical names (``sh``, ``python``);
  an absolute path to a toolchain binary is not on the allowlist and is denied.
* puts the toolchain directories first on the sandbox ``PATH``.

Pure helpers are unit-tested on every OS; the grant itself runs only on Windows.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from locus_tooling.native_binaries import (
    BUSYBOX_EXE,
    BUSYBOX_SUBDIR,
    TOOLCHAIN_COMPONENTS,
    TOOLCHAIN_DIRNAME,
    TOOLCHAIN_MARKER,
    current_platform,
    python_embed_subdir,
    resolve_toolchain_spec,
    toolchain_component_installed,
    toolchain_dir,
)

#: Logical executable names the toolchain serves inside the sandbox.
SHELL_NAMES = frozenset({"sh", "bash"})
PYTHON_NAMES = frozenset({"python", "python3"})
TOOLCHAIN_NAMES = SHELL_NAMES | PYTHON_NAMES

#: A per-application AppContainer SID: S-1-15-2 followed by seven sub-authorities.
#: Excludes the well-known group SIDs ALL APPLICATION PACKAGES (S-1-15-2-1) and ALL
#: RESTRICTED APPLICATION PACKAGES (S-1-15-2-2), which must never be granted here.
_APPCONTAINER_SID = re.compile(r"^S-1-15-2(-\d{1,10}){7}$")

GRANT_STAMP_PREFIX = ".locus-grant-"

SHELL_SCRIPT_FLAGS = frozenset({"-c", "-lc"})


def wrap_shell_script(script: str) -> str:
    """Wrap a ``sh -c`` script so its last command is not exec'd by the shell.

    ash runs the final simple command of ``-c`` via ``exec``. BusyBox-w32 emulates
    exec, and that emulation silently fails (exit 0, the program never runs) when
    the shell is the AppContainer's root process -- its parent, the launcher, lives
    outside the container. Grouping the script and exiting with its status keeps
    the shell in charge; the exit status is preserved. Pure."""
    return "{\n" + script + "\n}; exit $?"


MISSING_TOOLCHAIN_HINT = (
    "the Windows agent toolchain (BusyBox sh + embeddable Python) is not installed; "
    "run `lattix native-fetch-toolchain` (the desktop app fetches it on first run)"
)


def toolchain_app_home() -> Path:
    """The Locus app home: ``LOCUS_APP_HOME`` when set, else the per-user default."""
    explicit = str(os.getenv("LOCUS_APP_HOME") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from locus_tooling.common import default_app_home

    return default_app_home()


@dataclass(frozen=True)
class WindowsToolchain:
    """Paths of one toolchain install (``<app_home>/toolchain``)."""

    root: Path
    python_version_dir: str = python_embed_subdir()

    @property
    def busybox_dir(self) -> Path:
        return self.root / BUSYBOX_SUBDIR

    @property
    def busybox_exe(self) -> Path:
        return self.busybox_dir / BUSYBOX_EXE

    @property
    def python_dir(self) -> Path:
        return self.root / self.python_version_dir

    @property
    def python_exe(self) -> Path:
        return self.python_dir / "python.exe"

    def path_dirs(self) -> list[str]:
        """Directories to put first on the sandbox PATH (shell utilities first)."""
        return [str(self.busybox_dir), str(self.python_dir)]

    def is_installed(self, *, arch: str | None = None) -> bool:
        """Every component is present with an install stamp matching its pin."""
        if not (self.root / TOOLCHAIN_MARKER).is_file():
            return False
        arch = arch or current_platform()[1]
        try:
            specs = [resolve_toolchain_spec(n, "windows", arch) for n in TOOLCHAIN_COMPONENTS]
        except Exception:  # noqa: BLE001 - an unsupported arch has no toolchain
            return False
        return (
            all(toolchain_component_installed(self.root, spec) for spec in specs)
            and self.busybox_exe.is_file()
            and self.python_exe.is_file()
        )

    def resolve(self, command: list[str]) -> list[str]:
        """Map a logical command to the toolchain binaries (other commands unchanged).

        ``sh``/``bash`` -> ``busybox.exe sh|bash ...``; ``python``/``python3`` ->
        the toolchain interpreter. Only the bare logical name is mapped: a path is
        left alone (and is not on the gateway's executable allowlist)."""
        if not command:
            return list(command)
        head, rest = str(command[0]), [str(part) for part in command[1:]]
        if head in SHELL_NAMES:
            if len(rest) >= 2 and rest[0] in SHELL_SCRIPT_FLAGS:
                rest = [rest[0], wrap_shell_script(rest[1]), *rest[2:]]
            return [str(self.busybox_exe), head, *rest]
        if head in PYTHON_NAMES:
            return [str(self.python_exe), *rest]
        return [head, *rest]


#: The app home whose (read-only) toolchain to use when it differs from
#: ``LOCUS_APP_HOME``: an RSI candidate instance (LOCUS-351) runs with a separate,
#: empty app home but shares the installed BusyBox / embedded Python.
TOOLCHAIN_HOME_ENV = "LOCUS_TOOLCHAIN_HOME"


def toolchain_for(app_home: Path | None = None) -> WindowsToolchain:
    if app_home is None:
        shared = str(os.getenv(TOOLCHAIN_HOME_ENV) or "").strip()
        if shared:
            app_home = Path(shared).expanduser()
    return WindowsToolchain(root=toolchain_dir(app_home or toolchain_app_home()))


def discover_toolchain(app_home: Path | None = None) -> WindowsToolchain | None:
    """The installed toolchain under the app home, or ``None`` when it is missing."""
    try:
        toolchain = toolchain_for(app_home)
    except Exception:  # noqa: BLE001 - no resolvable app home: no toolchain
        return None
    return toolchain if toolchain.is_installed() else None


def needs_toolchain(command: list[str]) -> bool:
    return bool(command) and str(command[0]) in TOOLCHAIN_NAMES


# --------------------------------------------------------------------------- #
# ACL grant (Locus-owned directory, Locus AppContainer SID only)
# --------------------------------------------------------------------------- #
def is_appcontainer_sid(sid: str) -> bool:
    return bool(_APPCONTAINER_SID.match(str(sid or "")))


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def is_locus_owned_toolchain(root: Path) -> bool:
    """``root`` is a real directory named ``toolchain`` that carries the marker Locus
    writes when it creates it -- the only kind of directory whose ACL Locus changes."""
    root = Path(root)
    return (
        root.name == TOOLCHAIN_DIRNAME
        and root.is_dir()
        and not _is_reparse_point(root)
        and (root / TOOLCHAIN_MARKER).is_file()
    )


def toolchain_grant_command(sid: str, root: Path) -> list[str]:
    """icacls argv granting ``sid`` read+execute on ``root`` (inherited by files and
    subdirectories, applied to the existing tree). Pure."""
    if not is_appcontainer_sid(sid):
        raise ValueError(f"refusing to grant a non-AppContainer SID: {sid!r}")
    return ["icacls", str(root), "/grant", f"*{sid}:(OI)(CI)RX", "/T", "/C", "/Q"]


def grant_stamp(root: Path, sid: str) -> Path:
    return Path(root) / f"{GRANT_STAMP_PREFIX}{sid}"


RunFn = Callable[..., Any]


def ensure_toolchain_grant(root: Path, sid: str, *, run: RunFn = subprocess.run) -> bool:
    """Grant ``sid`` read+execute on the Locus toolchain ``root``, once.

    Returns ``True`` when icacls ran now, ``False`` when the stamp shows the grant
    is already in place (new files inherit the (OI)(CI) ACE, so a re-fetch keeps
    it). Raises ``PermissionError`` for a directory Locus does not own and
    ``OSError`` when icacls fails (fail closed: no stamp is written)."""
    root = Path(root)
    if not is_locus_owned_toolchain(root):
        raise PermissionError(f"not a Locus-owned toolchain directory: {root}")
    argv = toolchain_grant_command(sid, root)
    stamp = grant_stamp(root, sid)
    if stamp.is_file():
        return False
    proc = run(argv, check=False, capture_output=True, text=True)
    if getattr(proc, "returncode", 1) != 0:
        detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        raise OSError(f"icacls grant on {root} failed: {detail[-300:]}")
    stamp.write_text("read+execute granted to this AppContainer SID\n", encoding="utf-8")
    return True


def grant_toolchain_access(root: Path, *, run: RunFn = subprocess.run) -> bool:
    """Install-time grant: derive the Locus AppContainer SID (creating the profile if
    needed) and grant it read+execute on the toolchain. Windows only."""
    from locus_runtime import win_sandbox

    if not win_sandbox._is_windows():  # noqa: SLF001 - same package
        raise RuntimeError("the agent toolchain grant is Windows-only")
    _sid, sid_string = win_sandbox._derive_appcontainer_sid(  # noqa: SLF001
        win_sandbox.APPCONTAINER_NAME
    )
    return ensure_toolchain_grant(root, sid_string, run=run)
