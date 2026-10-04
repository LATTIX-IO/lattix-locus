"""Spawn a process in a Locus AppContainer with stdio pipes (LOCUS-379, Windows only).

The agent-tool launcher (:mod:`locus_runtime.win_sandbox`) runs one command and
waits for it. The RSI candidate instance needs more: a long-lived child whose
stdin/stdout carry the bridge protocol (:mod:`locus_runtime.rsi.bridge`), an
explicit environment block, and its own AppContainer profile. This module adds
exactly that, reusing the launcher's profile/SID derivation and Job Object
configuration:

* **AppContainer, no capabilities.** No ``internetClient`` or
  ``privateNetworkClientServer``: the child has no network at all, loopback
  included (AppContainers are not loopback-exempt unless someone runs
  ``CheckNetIsolation LoopbackExempt -a``, which Locus never does).
* **Default-deny files.** The child can open only what is readable by ALL
  APPLICATION PACKAGES (Windows system files) and what the caller granted the
  profile's SID with :func:`grant_paths` (Locus-owned temp directories only).
* **Only three handles inherited.** ``PROC_THREAD_ATTRIBUTE_HANDLE_LIST``
  restricts inheritance to the child's stdin/stdout/stderr pipe ends, so no other
  handle of the trusted parent leaks into the container.
* **Job Object.** Memory and process caps, kill-on-close, and UI restrictions
  (no clipboard, no global atoms, no foreign USER handles, no desktop switching).
  The child is created suspended and resumed only after it is in the job.

Pure helpers (name/SID validation, environment block) are unit-tested on every
OS; :func:`spawn` runs only on Windows.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: AppContainer profile of the RSI candidate process (separate from the agent
#: tool profile, so grants for one never open the other's files).
CANDIDATE_PROFILE = "com.lattix.locus.rsi-candidate"
#: AppContainer profile of the commands the candidate's agent runs (bridged exec).
TOOLS_PROFILE = "com.lattix.locus.rsi-tools"
_PROFILE_NAME = re.compile(r"^com\.lattix\.locus\.[a-z0-9][a-z0-9.-]{0,40}$")

_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF
#: JOB_OBJECT_UILIMIT_*: HANDLES | READCLIPBOARD | WRITECLIPBOARD |
#: SYSTEMPARAMETERS | DISPLAYSETTINGS | GLOBALATOMS | DESKTOP | EXITWINDOWS.
UI_RESTRICTIONS = 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x40 | 0x80
_JOB_OBJECT_BASIC_UI_RESTRICTIONS = 4


class AppContainerError(OSError):
    """The confined child could not be created (fail closed: nothing ran)."""


def validate_profile_name(name: str) -> str:
    """Only Locus-namespaced profiles (``com.lattix.locus.*``). Pure."""
    if not _PROFILE_NAME.match(str(name or "")):
        raise ValueError(f"not a Locus AppContainer profile name: {name!r}")
    return name


def environment_block(env: Mapping[str, str]) -> str:
    """A ``CREATE_UNICODE_ENVIRONMENT`` block: ``NAME=value\\0...\\0`` sorted
    case-insensitively, as CreateProcess expects. Pure.

    Raises ``ValueError`` for a name with ``=`` or NUL, or a value with NUL
    (such an entry would split or truncate the block)."""
    items: list[tuple[str, str]] = []
    for key, value in env.items():
        name, text = str(key), str(value)
        if not name or "=" in name or "\0" in name or "\0" in text:
            raise ValueError(f"invalid environment entry {name!r}")
        items.append((name, text))
    items.sort(key=lambda kv: kv[0].upper())
    return "".join(f"{k}={v}\0" for k, v in items) + "\0"


def grant_commands(
    sid: str, *, read: Sequence[str] = (), write: Sequence[str] = ()
) -> list[list[str]]:
    """icacls argv lists granting an AppContainer ``sid`` read+execute (``read``) or
    modify (``write``) on directories, inherited by everything below. Pure.

    Refuses any SID that is not a per-application AppContainer SID (never ALL
    APPLICATION PACKAGES or a user/group SID)."""
    from locus_runtime.win_toolchain import is_appcontainer_sid

    if not is_appcontainer_sid(sid):
        raise ValueError(f"refusing to grant a non-AppContainer SID: {sid!r}")
    cmds = [["icacls", str(p), "/grant", f"*{sid}:(OI)(CI)M", "/T", "/C", "/Q"] for p in write]
    cmds += [["icacls", str(p), "/grant", f"*{sid}:(OI)(CI)RX", "/T", "/C", "/Q"] for p in read]
    return cmds


RunFn = Callable[..., Any]


def grant_paths(
    sid: str,
    *,
    read: Sequence[str] = (),
    write: Sequence[str] = (),
    run: RunFn = subprocess.run,
) -> None:
    """Apply :func:`grant_commands`; raises ``AppContainerError`` when one fails
    (fail closed: a partly granted layout is not launched)."""
    for argv in grant_commands(sid, read=read, write=write):
        done = run(argv, check=False, capture_output=True, text=True)
        if getattr(done, "returncode", 1) != 0:
            detail = (getattr(done, "stderr", "") or getattr(done, "stdout", "") or "").strip()
            raise AppContainerError(f"icacls grant on {argv[1]} failed: {detail[-200:]}")


def profile_sid(name: str) -> str:
    """The string SID of a Locus AppContainer profile (created when missing)."""
    from locus_runtime import win_sandbox

    _sid, sid_string = win_sandbox._derive_appcontainer_sid(validate_profile_name(name))  # noqa: SLF001
    return sid_string


@dataclass
class ConfinedChild:
    """A running AppContainer child (process handle + its Job Object)."""

    pid: int
    _process: int
    _job: int
    _closed: bool = False

    def poll(self) -> int | None:
        return self.wait(0.0)

    def wait(self, timeout: float | None = None) -> int | None:
        """Exit code, or ``None`` when ``timeout`` elapsed first."""
        import ctypes
        from ctypes import wintypes

        k32 = _kernel32()
        millis = _INFINITE if timeout is None else max(0, int(timeout * 1000))
        if k32.WaitForSingleObject(wintypes.HANDLE(self._process), millis) == _WAIT_TIMEOUT:
            return None
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(wintypes.HANDLE(self._process), ctypes.byref(code))
        return int(code.value)

    def kill(self) -> None:
        """Terminate the whole job (the child and anything it started)."""
        from ctypes import wintypes

        if not self._closed:
            _kernel32().TerminateJobObject(wintypes.HANDLE(self._job), 1)

    def close(self) -> None:
        from ctypes import wintypes

        if self._closed:
            return
        self._closed = True
        k32 = _kernel32()
        k32.CloseHandle(wintypes.HANDLE(self._process))
        # Closing the job handle kills anything still running (KILL_ON_JOB_CLOSE).
        k32.CloseHandle(wintypes.HANDLE(self._job))


def _kernel32() -> Any:
    if sys.platform != "win32":
        raise AppContainerError("kernel32 is Windows-only")
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    k32.ResumeThread.restype = wintypes.DWORD
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    k32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    return k32


def spawn(
    command: Sequence[str],
    *,
    profile: str,
    env: Mapping[str, str],
    cwd: str,
    stdin: int,
    stdout: int,
    stderr: int,
    memory_bytes: int = 0,
    active_processes: int = 0,
) -> ConfinedChild:
    """Start ``command`` suspended in the ``profile`` AppContainer (no capabilities),
    with exactly the three given (inheritable) handles as its stdio, put it in a
    Job Object with the limits and UI restrictions, then resume it.

    Raises :class:`AppContainerError` on any failure; the child never runs
    outside the job. Windows only."""
    if sys.platform != "win32":
        raise AppContainerError("AppContainer confinement is Windows-only")
    import ctypes
    from ctypes import wintypes

    from locus_runtime import win_sandbox

    if not command:
        raise AppContainerError("no command to run")
    sid, _sid_string = win_sandbox._derive_appcontainer_sid(validate_profile_name(profile))  # noqa: SLF001
    k32 = _kernel32()

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.c_void_p),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    sec = _SECURITY_CAPABILITIES()
    sec.AppContainerSid = sid
    sec.Capabilities = None  # no capabilities: no network of any kind
    sec.CapabilityCount = 0
    handles = (wintypes.HANDLE * 3)(stdin, stdout, stderr)

    attr_count = 2
    size = ctypes.c_size_t(0)
    k32.InitializeProcThreadAttributeList(None, attr_count, 0, ctypes.byref(size))
    buf = (ctypes.c_byte * size.value)()
    attrs = ctypes.cast(buf, ctypes.c_void_p)
    if not k32.InitializeProcThreadAttributeList(attrs, attr_count, 0, ctypes.byref(size)):
        raise AppContainerError(ctypes.get_last_error(), "InitializeProcThreadAttributeList")
    job = None
    try:
        if not k32.UpdateProcThreadAttribute(
            attrs,
            0,
            _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.byref(sec),
            ctypes.sizeof(sec),
            None,
            None,
        ):
            raise AppContainerError(ctypes.get_last_error(), "UpdateProcThreadAttribute(caps)")
        if not k32.UpdateProcThreadAttribute(
            attrs,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.byref(handles),
            ctypes.sizeof(handles),
            None,
            None,
        ):
            raise AppContainerError(ctypes.get_last_error(), "UpdateProcThreadAttribute(handles)")
        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = stdin
        si.StartupInfo.hStdOutput = stdout
        si.StartupInfo.hStdError = stderr
        si.lpAttributeList = attrs
        pi = _PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
        block = ctypes.create_unicode_buffer(environment_block(env))
        limits = win_sandbox.JobLimits(
            memory_bytes=max(0, int(memory_bytes)),
            active_process_limit=max(0, int(active_processes)),
            kill_on_close=True,
        )
        job = win_sandbox._configure_job(k32, limits)  # noqa: SLF001
        ui = wintypes.DWORD(UI_RESTRICTIONS)
        if not k32.SetInformationJobObject(
            job, _JOB_OBJECT_BASIC_UI_RESTRICTIONS, ctypes.byref(ui), ctypes.sizeof(ui)
        ):
            raise AppContainerError(ctypes.get_last_error(), "SetInformationJobObject(UI)")
        ok = k32.CreateProcessW(
            None,
            cmdline,
            None,
            None,
            True,  # inherit: limited to the HANDLE_LIST above
            _EXTENDED_STARTUPINFO_PRESENT
            | _CREATE_SUSPENDED
            | _CREATE_UNICODE_ENVIRONMENT
            | _CREATE_NO_WINDOW,
            block,
            ctypes.c_wchar_p(cwd or None),
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            raise AppContainerError(ctypes.get_last_error(), "CreateProcessW (AppContainer)")
        try:
            if not k32.AssignProcessToJobObject(job, pi.hProcess):
                err = ctypes.get_last_error()
                k32.TerminateProcess(pi.hProcess, 1)
                k32.CloseHandle(pi.hProcess)
                raise AppContainerError(err, "AssignProcessToJobObject")
            k32.ResumeThread(pi.hThread)
        finally:
            k32.CloseHandle(pi.hThread)
        child = ConfinedChild(pid=int(pi.dwProcessId), _process=int(pi.hProcess), _job=int(job))
        job = None  # owned by the child object now
        return child
    finally:
        k32.DeleteProcThreadAttributeList(attrs)
        if job is not None:
            k32.CloseHandle(job)


def inheritable_pipe() -> tuple[int, int]:
    """``(read, write)`` OS handles of a new anonymous pipe (not inheritable)."""
    if sys.platform != "win32":
        raise AppContainerError("anonymous pipe handles are Windows-only")
    import _winapi

    read, write = _winapi.CreatePipe(None, 0)
    return int(read), int(write)


def handle_to_file(handle: int, mode: str) -> Any:
    """Wrap a pipe handle the parent keeps in an unbuffered binary file object."""
    if sys.platform != "win32":
        raise AppContainerError("pipe handles are Windows-only")
    import msvcrt

    flags = os.O_RDONLY if "r" in mode else os.O_WRONLY
    fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    return open(fd, mode + "b", buffering=0)  # noqa: SIM115 - the caller owns it
