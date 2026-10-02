"""Windows isolation launcher for xFrontier agent tool execution (Track C).

Windows has no ``bwrap``/``sandbox-exec`` CLI, so the sandbox strategy invokes
this module as a subprocess (``python -m frontier_runtime.win_sandbox run -- …``)
and it sets up OS-level confinement via Win32 before launching the real command.

Tiers (strongest first):
  1. **Windows Sandbox** (Hyper-V, opt-in) — full disposable VM via a generated
     ``.wsb`` config. Highest isolation; heaviest. ``FRONTIER_WIN_SANDBOX_VM=1``.
  2. **AppContainer + Job Object** (DEFAULT) — AppContainer low-privilege,
     capability-gated execution (default-deny filesystem; the bound worktree is
     ACL-granted to the container SID) + a Job Object for memory/process caps +
     kill-on-close. On par with bwrap (Linux) / seatbelt (macOS).
  3. **Job Object** (automatic fallback) — memory limit + active-process cap +
     kill-on-job-close so a runaway/forkbomb child is bounded and dies with the
     launcher. Used when AppContainer setup fails. Force with
     ``FRONTIER_WIN_SANDBOX_TIER=job``.

The pure helpers (limit/capability/WSB computation) are unit-tested cross-platform;
the ctypes launch paths run only on Windows.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

# Well-known AppContainer capability names → granted only when needed.
_NETWORK_CAPABILITIES = ("internetClient", "privateNetworkClientServer")


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested everywhere)
# --------------------------------------------------------------------------- #
def parse_memory_limit(value: str) -> int:
    """Parse a docker-style memory string ('512m', '2g', '1048576') → bytes."""
    text = str(value or "").strip().lower()
    if not text:
        return 0
    units = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    if text[-1] in units:
        number, factor = text[:-1], units[text[-1]]
    else:
        number, factor = text, 1
    try:
        return int(float(number) * factor)
    except ValueError:
        return 0


@dataclass
class JobLimits:
    memory_bytes: int = 0
    active_process_limit: int = 0
    kill_on_close: bool = True

    def limit_flags(self) -> int:
        # JOB_OBJECT_LIMIT_* bit flags.
        flags = 0
        if self.kill_on_close:
            flags |= 0x2000  # KILL_ON_JOB_CLOSE
        if self.active_process_limit > 0:
            flags |= 0x0008  # ACTIVE_PROCESS
        if self.memory_bytes > 0:
            flags |= 0x0100  # PROCESS_MEMORY
        return flags


def compute_job_limits(*, memory: str, pids: int, kill_on_close: bool = True) -> JobLimits:
    return JobLimits(
        memory_bytes=parse_memory_limit(memory),
        active_process_limit=max(0, int(pids or 0)),
        kill_on_close=kill_on_close,
    )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def select_confinement_tier(*, forced: str | None, require_appcontainer: bool) -> tuple[str, bool]:
    """Decide the Windows confinement tier and whether a weaker fallback is allowed.

    Returns ``(tier, allow_fallback)`` where ``tier`` is ``"appcontainer"`` or
    ``"job"``. ``allow_fallback`` is ``False`` when the operator requires
    AppContainer: a failed AppContainer setup must then **fail closed** rather than
    silently drop to the resource-only Job-Object tier — which bounds memory and
    process count but does NOT confine the filesystem or network. For hostile
    agent code that silent downgrade is a security regression, so the native /
    secure deployment can demand the strong tier via
    ``FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER=1``.

    An explicit ``FRONTIER_WIN_SANDBOX_TIER=job`` is the operator deliberately
    choosing the baseline tier, so ``require_appcontainer`` does not apply there.
    Pure/testable (no execution, no Win32)."""
    tier = str(forced or "appcontainer").strip().lower()
    if tier not in {"appcontainer", "job"}:
        tier = "appcontainer"
    if tier == "job":
        return "job", True
    return "appcontainer", not require_appcontainer


def capability_sids(*, allow_network: bool) -> list[str]:
    """AppContainer capability names to grant. Empty (no caps) when network is
    denied — the most locked-down AppContainer."""
    return list(_NETWORK_CAPABILITIES) if allow_network else []


def build_wsb_config(
    *,
    command: list[str],
    read_paths: list[str],
    write_paths: list[str],
    allow_network: bool,
    cwd: str = "",
) -> str:
    """Generate a Windows Sandbox ``.wsb`` XML config (Hyper-V disposable VM)."""
    folders: list[str] = []
    for path in write_paths:
        folders.append(
            f"    <MappedFolder><HostFolder>{escape(path)}</HostFolder>"
            f"<ReadOnly>false</ReadOnly></MappedFolder>"
        )
    for path in read_paths:
        folders.append(
            f"    <MappedFolder><HostFolder>{escape(path)}</HostFolder>"
            f"<ReadOnly>true</ReadOnly></MappedFolder>"
        )
    mapped = "\n".join(folders)
    networking = "Default" if allow_network else "Disable"
    cmd = " ".join(command)
    logon = f"<LogonCommand><Command>{escape(cmd)}</Command></LogonCommand>" if command else ""
    return (
        "<Configuration>\n"
        f"  <Networking>{networking}</Networking>\n"
        "  <MappedFolders>\n"
        f"{mapped}\n"
        "  </MappedFolders>\n"
        f"  {logon}\n"
        "</Configuration>\n"
    )


@dataclass
class ConfinementResult:
    exit_code: int
    tier: str  # "windows-sandbox" | "appcontainer-job" | "job-object" | "plain"
    detail: str = ""


# --------------------------------------------------------------------------- #
# Windows launch paths (ctypes; run only on Windows)
# --------------------------------------------------------------------------- #
def _is_windows() -> bool:
    return sys.platform == "win32"


def _run_with_job_object(
    command: list[str], limits: JobLimits, *, cwd: str = "", timeout: int = 0
) -> int:
    """Run ``command`` inside a Job Object with memory/process caps and
    kill-on-job-close. Robust, well-supported confinement.

    The child is assigned to the job immediately after spawn; closing the job
    handle (on launcher exit) terminates the whole tree.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    h_job = k32.CreateJobObjectW(None, None)
    if not h_job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    try:
        info = _EXT_LIMIT()
        info.BasicLimitInformation.LimitFlags = limits.limit_flags()
        if limits.active_process_limit > 0:
            info.BasicLimitInformation.ActiveProcessLimit = limits.active_process_limit
        if limits.memory_bytes > 0:
            info.ProcessMemoryLimit = limits.memory_bytes
        # 9 == JobObjectExtendedLimitInformation
        if not k32.SetInformationJobObject(h_job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

        proc = subprocess.Popen(command, cwd=cwd or None)
        try:
            handle = int(proc._handle)  # type: ignore[attr-defined]
            if not k32.AssignProcessToJobObject(h_job, wintypes.HANDLE(handle)):
                # Already in a job that disallows nesting? Continue unconfined but warn.
                sys.stderr.write("[win_sandbox] WARN: AssignProcessToJobObject failed\n")
            proc.wait(timeout=timeout or None)
            return int(proc.returncode or 0)
        finally:
            if proc.poll() is None:
                proc.kill()
    finally:
        k32.CloseHandle(h_job)


_APPCONTAINER_NAME = "com.lattix.xfrontier.agent"
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_SUSPENDED = 0x00000004
_SE_GROUP_ENABLED = 0x00000004


def acl_grant_commands(
    sid_string: str, *, write_paths: list[str], read_paths: list[str]
) -> list[list[str]]:
    """icacls argv lists that grant the AppContainer SID access to the bound paths.

    AppContainer processes are denied file access by default, so the workspace
    (write) + any read paths must be explicitly granted to the container SID.
    Pure/testable (no execution)."""
    cmds: list[list[str]] = []
    for path in write_paths:
        cmds.append(["icacls", path, "/grant", f"*{sid_string}:(OI)(CI)M", "/T", "/C", "/Q"])
    for path in read_paths:
        cmds.append(["icacls", path, "/grant", f"*{sid_string}:(OI)(CI)RX", "/T", "/C", "/Q"])
    return cmds


def _derive_appcontainer_sid(name: str) -> tuple[Any, str]:
    import ctypes

    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    sid = ctypes.c_void_p()
    # Idempotent: create the profile if missing, then derive its SID.
    try:
        userenv.CreateAppContainerProfile(
            ctypes.c_wchar_p(name),
            ctypes.c_wchar_p(name),
            ctypes.c_wchar_p(name),
            None,
            0,
            ctypes.byref(sid),
        )
    except Exception:  # noqa: BLE001 - already exists / non-fatal; derive below
        pass
    sid = ctypes.c_void_p()
    hr = userenv.DeriveAppContainerSidFromAppContainerName(
        ctypes.c_wchar_p(name), ctypes.byref(sid)
    )
    if hr != 0 or not sid.value:
        raise OSError(f"DeriveAppContainerSidFromAppContainerName failed (hr={hr})")
    # SID → string form for icacls grants.
    str_ptr = ctypes.c_wchar_p()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(str_ptr)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
    return sid, str(str_ptr.value or "")


def _derive_capability_sids(cap_names: list[str]) -> list[Any]:
    import ctypes
    from ctypes import wintypes

    kb = ctypes.WinDLL("kernelbase", use_last_error=True)
    sids: list[ctypes.c_void_p] = []
    for name in cap_names:
        group_sids = ctypes.POINTER(ctypes.c_void_p)()
        group_count = wintypes.DWORD()
        cap_sids = ctypes.POINTER(ctypes.c_void_p)()
        cap_count = wintypes.DWORD()
        ok = kb.DeriveCapabilitySidsFromName(
            ctypes.c_wchar_p(name),
            ctypes.byref(group_sids),
            ctypes.byref(group_count),
            ctypes.byref(cap_sids),
            ctypes.byref(cap_count),
        )
        if ok and cap_count.value:
            for i in range(cap_count.value):
                sids.append(ctypes.c_void_p(cap_sids[i]))
    return sids


def _run_in_appcontainer(
    command: list[str],
    limits: JobLimits,
    *,
    allow_network: bool,
    read_paths: list[str],
    write_paths: list[str],
    cwd: str = "",
    timeout: int = 0,
) -> int:
    """Launch ``command`` inside an AppContainer (low-privilege, capability-gated)
    bounded by a Job Object. Grants the container SID ACLs on the bound paths so
    the agent can read/write its worktree but nothing else. Raises on any Win32
    failure so callers fall back to the Job-Object tier.

    NOTE: the ctypes path is implemented but must be validated on real Windows in
    CI/e2e; failures degrade safely.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    sid, sid_string = _derive_appcontainer_sid(_APPCONTAINER_NAME)
    # Grant the container access to its bound paths (default-deny otherwise).
    grant_writes = list(write_paths)
    if cwd:
        grant_writes.append(cwd)
    for argv in acl_grant_commands(sid_string, write_paths=grant_writes, read_paths=read_paths):
        subprocess.run(argv, check=False, capture_output=True)

    cap_sids = _derive_capability_sids(capability_sids(allow_network=allow_network))

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    caps_array = (_SID_AND_ATTRIBUTES * len(cap_sids))()
    for i, cap in enumerate(cap_sids):
        caps_array[i].Sid = cap
        caps_array[i].Attributes = _SE_GROUP_ENABLED
    sec = _SECURITY_CAPABILITIES()
    sec.AppContainerSid = sid
    sec.Capabilities = (
        ctypes.cast(caps_array, ctypes.POINTER(_SID_AND_ATTRIBUTES)) if cap_sids else None
    )
    sec.CapabilityCount = len(cap_sids)

    # Build the PROC_THREAD attribute list carrying the security capabilities.
    size = ctypes.c_size_t(0)
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    buf = (ctypes.c_byte * size.value)()
    attr_list = ctypes.cast(buf, ctypes.c_void_p)
    if not k32.InitializeProcThreadAttributeList(attr_list, 1, 0, ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), "InitializeProcThreadAttributeList failed")
    if not k32.UpdateProcThreadAttribute(
        attr_list,
        0,
        ctypes.c_size_t(_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES),
        ctypes.byref(sec),
        ctypes.sizeof(sec),
        None,
        None,
    ):
        raise OSError(ctypes.get_last_error(), "UpdateProcThreadAttribute failed")

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
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
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

    si = _STARTUPINFOEXW()
    si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
    si.lpAttributeList = attr_list
    pi = _PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))

    h_job = _configure_job(k32, limits)
    try:
        ok = k32.CreateProcessW(
            None,
            cmdline,
            None,
            None,
            False,
            _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED,
            None,
            ctypes.c_wchar_p(cwd or None),
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "CreateProcessW (AppContainer) failed")
        try:
            k32.AssignProcessToJobObject(h_job, pi.hProcess)
            k32.ResumeThread(pi.hThread)
            k32.WaitForSingleObject(
                pi.hProcess, wintypes.DWORD(0xFFFFFFFF if not timeout else timeout * 1000)
            )
            code = wintypes.DWORD()
            k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
            return int(code.value)
        finally:
            k32.CloseHandle(pi.hThread)
            k32.CloseHandle(pi.hProcess)
    finally:
        k32.DeleteProcThreadAttributeList(attr_list)
        k32.CloseHandle(h_job)


def _configure_job(k32: Any, limits: JobLimits) -> Any:
    """Create + configure a Job Object (memory/process caps + kill-on-close)."""
    import ctypes
    from ctypes import wintypes

    class _IO(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo", _IO),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    h_job = k32.CreateJobObjectW(None, None)
    if not h_job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = _EXT()
    info.BasicLimitInformation.LimitFlags = limits.limit_flags()
    if limits.active_process_limit > 0:
        info.BasicLimitInformation.ActiveProcessLimit = limits.active_process_limit
    if limits.memory_bytes > 0:
        info.ProcessMemoryLimit = limits.memory_bytes
    if not k32.SetInformationJobObject(h_job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(h_job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return h_job


def run_confined(
    command: list[str],
    *,
    memory: str = "512m",
    pids: int = 256,
    cpu: str = "1.0",  # reserved (Job CPU rate control) — not yet enforced
    timeout: int = 0,
    allow_network: bool = False,
    read_paths: list[str] | None = None,
    write_paths: list[str] | None = None,
    cwd: str = "",
) -> ConfinementResult:
    """Run ``command`` under the strongest available Windows confinement tier.

    Default tier is **AppContainer + Job Object** (deep isolation, on par with
    bwrap on Linux / seatbelt on macOS); it degrades to the Job-Object tier if
    AppContainer setup fails. Force a tier with ``FRONTIER_WIN_SANDBOX_TIER``
    (``appcontainer`` | ``job``). Set
    ``FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER=1`` to **fail closed** instead of
    silently degrading to the resource-only Job-Object tier when AppContainer is
    unavailable — use this for the hostile-code threat model where losing
    filesystem/network confinement is unacceptable.
    """
    if not _is_windows():
        raise RuntimeError("win_sandbox.run_confined is Windows-only")
    limits = compute_job_limits(memory=memory, pids=pids)
    tier, allow_fallback = select_confinement_tier(
        forced=os.getenv("FRONTIER_WIN_SANDBOX_TIER"),
        require_appcontainer=_env_flag("FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER"),
    )

    if tier == "appcontainer":
        try:
            code = _run_in_appcontainer(
                command,
                limits,
                allow_network=allow_network,
                read_paths=read_paths or [],
                write_paths=write_paths or [],
                cwd=cwd,
                timeout=timeout,
            )
            return ConfinementResult(code, "appcontainer-job")
        except Exception as exc:  # noqa: BLE001
            if not allow_fallback:
                # Fail closed: this deployment requires real capability confinement
                # and must NOT silently downgrade to the resource-only Job tier.
                raise RuntimeError(
                    "AppContainer confinement is required "
                    "(FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER=1) but could not be "
                    f"established: {exc}"
                ) from exc
            sys.stderr.write(f"[win_sandbox] appcontainer unavailable, using job-object: {exc}\n")

    code = _run_with_job_object(command, limits, cwd=cwd, timeout=timeout)
    return ConfinementResult(code, "job-object")


# --------------------------------------------------------------------------- #
# CLI entrypoint (invoked by the sandbox strategy)
# --------------------------------------------------------------------------- #
@dataclass
class _Parsed:
    command: list[str] = field(default_factory=list)
    memory: str = "512m"
    pids: int = 256
    cpu: str = "1.0"
    timeout: int = 0
    allow_network: bool = False
    read_paths: list[str] = field(default_factory=list)
    write_paths: list[str] = field(default_factory=list)
    cwd: str = ""


def _parse_args(argv: list[str]) -> _Parsed:
    # The strategy always emits `--` before the real command; split there so the
    # child's own flags can't be mistaken for our options.
    if "--" in argv:
        idx = argv.index("--")
        head, command = argv[:idx], argv[idx + 1 :]
    else:
        head, command = argv, []
    parser = argparse.ArgumentParser(prog="frontier_runtime.win_sandbox")
    parser.add_argument("mode", choices=["run"])
    parser.add_argument("--memory", default="512m")
    parser.add_argument("--pids", type=int, default=256)
    parser.add_argument("--cpu", default="1.0")
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--read-path", action="append", default=[])
    parser.add_argument("--write-path", action="append", default=[])
    parser.add_argument("--cwd", default="")
    ns = parser.parse_args(head)
    return _Parsed(
        command=command,
        memory=ns.memory,
        pids=ns.pids,
        cpu=ns.cpu,
        timeout=ns.timeout,
        allow_network=ns.allow_network,
        read_paths=ns.read_path,
        write_paths=ns.write_path,
        cwd=ns.cwd,
    )


def main(argv: list[str] | None = None) -> int:
    parsed = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    if not parsed.command:
        sys.stderr.write("[win_sandbox] no command to run\n")
        return 2
    result = run_confined(
        parsed.command,
        memory=parsed.memory,
        pids=parsed.pids,
        cpu=parsed.cpu,
        timeout=parsed.timeout,
        allow_network=parsed.allow_network,
        read_paths=parsed.read_paths,
        write_paths=parsed.write_paths,
        cwd=parsed.cwd,
    )
    sys.stderr.write(f"[win_sandbox] tier={result.tier}\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
