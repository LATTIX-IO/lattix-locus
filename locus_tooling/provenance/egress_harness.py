"""Child-side harness for the D-29 dynamic egress test (stdlib only; any CPython 3.8+).

Run *inside* the Locus sandbox with network egress denied:

    python egress_harness.py --log <file> --path <dir> [--path <dir>...]
        --import <module>[,<module>...] [--exercise <file.py>] [--settle <seconds>]
        [--probe <host:port>]

Before any inspected code runs, it installs a ``sys.addaudithook`` hook that
records every network-, process- and native-load-related audit event to ``--log``
(JSON lines, flushed per event, so a crash or kill keeps what was seen) and
**denies** network and process events by raising ``PermissionError`` -- the
OS-level jail denies them too; the hook is what makes the attempt visible. It then
imports the modules and runs the optional exercise script. The last line written
is ``{"event": "harness.done", ...}``; its absence means the run did not finish.

Native code that calls the OS socket API directly does not raise Python audit
events: the jail still blocks it, but it is not logged here. The inspection
records that limitation and pairs this test with the static native-symbol scan.
"""

from __future__ import annotations

import json
import runpy
import sys
import time
from typing import Any, TextIO

NETWORK_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
        "socket.sendto",
        "socket.sendmsg",
        "socket.bind",
        "urllib.Request",
        "http.client.connect",
        "ftplib.connect",
        "smtplib.connect",
        "poplib.connect",
        "imaplib.open",
        "nntplib.connect",
        "telnetlib.Telnet.open",
        "webbrowser.open",
    }
)
PROCESS_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.spawn",
        "os.posix_spawn",
        "os.startfile",
        "os.fork",
        "pty.spawn",
    }
)
RECORDED_EVENTS = frozenset(
    {
        "socket.__new__",
        "ctypes.dlopen",
        "sqlite3.enable_load_extension",
        "sqlite3.load_extension",
    }
)


class _State:
    log: TextIO | None = None
    active: bool = False


_STATE = _State()


def _target(event: str, args: tuple[Any, ...]) -> str:
    try:
        if event in {"socket.connect", "socket.sendto", "socket.bind"} and len(args) >= 2:
            return repr(args[1])[:200]
        if event == "socket.sendmsg" and len(args) >= 2:
            return repr(args[1])[:200]
        if event == "socket.getaddrinfo" and args:
            return repr(args[:2])[:200]
        if event == "http.client.connect" and len(args) >= 3:
            return f"{args[1]}:{args[2]}"
        if event == "urllib.Request" and args:
            return str(args[0])[:200]
        if event == "socket.__new__" and len(args) >= 3:
            return f"family={args[1]} type={args[2]}"
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn"}:
            return repr(args[:2])[:200]
        return repr(args)[:200]
    except Exception:  # noqa: BLE001 - never let logging break the hook
        return "<unrepresentable>"


def _write(record: dict[str, Any]) -> None:
    handle = _STATE.log
    if handle is None:
        return
    handle.write(json.dumps(record) + "\n")
    handle.flush()


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if not _STATE.active:
        return
    if event in NETWORK_EVENTS:
        kind, outcome = "network", "denied"
    elif event in PROCESS_EVENTS:
        kind, outcome = "process", "denied"
    elif event in RECORDED_EVENTS:
        kind, outcome = "recorded", "recorded"
    else:
        return
    _STATE.active = False  # the hook's own I/O must not re-enter it
    try:
        _write({"event": event, "kind": kind, "target": _target(event, args), "outcome": outcome})
    finally:
        _STATE.active = True
    if outcome == "denied":
        raise PermissionError(f"locus provenance egress test: {event} denied")


def _probe(address: str) -> dict[str, Any]:
    """Positive control, run before the hook: try a TCP connection the jail must
    refuse (the parent listens on the host's loopback)."""
    import socket

    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return {"event": "harness.probe", "connected": True}
    except OSError as exc:
        return {"event": "harness.probe", "connected": False, "error": type(exc).__name__}


def main(argv: list[str]) -> int:
    log_path = ""
    paths: list[str] = []
    modules: list[str] = []
    exercise = ""
    settle = 2.0
    probe = ""
    items = list(argv)
    while items:
        flag = items.pop(0)
        value = items.pop(0) if items else ""
        if flag == "--log":
            log_path = value
        elif flag == "--path":
            paths.append(value)
        elif flag == "--import":
            modules += [m.strip() for m in value.split(",") if m.strip()]
        elif flag == "--exercise":
            exercise = value
        elif flag == "--probe":
            probe = value
        elif flag == "--settle":
            settle = max(0.0, float(value or 0))
        else:
            sys.stderr.write(f"unknown argument {flag}\n")
            return 2
    if not log_path:
        sys.stderr.write("--log is required\n")
        return 2
    _STATE.log = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - kept open for the hook
    _write(
        {
            "event": "harness.start",
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "time": time.time(),
        }
    )
    if probe:
        _write(_probe(probe))
    for entry in reversed(paths):
        sys.path.insert(0, entry)
    sys.addaudithook(_hook)
    _STATE.active = True
    status = 0
    try:
        for module in modules:
            __import__(module)
            _write({"event": "harness.imported", "module": module})
        if exercise:
            runpy.run_path(exercise, run_name="__provenance_exercise__")
            _write({"event": "harness.exercised", "script": exercise})
        # Background threads (a delayed "phone home") get time to try.
        time.sleep(settle)
    except BaseException as exc:  # noqa: BLE001 - recorded, then reported by exit code
        _STATE.active = False
        _write({"event": "harness.error", "error": f"{type(exc).__name__}: {exc}"[:500]})
        status = 3
    _STATE.active = False
    _write({"event": "harness.done", "status": status})
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
