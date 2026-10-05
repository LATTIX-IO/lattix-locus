"""Dynamic egress test for the D-29 provenance inspection.

Imports the inspected package (and runs an optional exercise script) inside the
Locus sandbox with network egress denied, and records every connection attempt.
The jail is the platform's confining tier from
:func:`locus_runtime.sandbox.select_confining_strategy` -- bubblewrap with
``--unshare-net`` on Linux, seatbelt without ``network-outbound`` on macOS,
``--network=none`` for hardened Docker, and on Windows an AppContainer with no
network capability (``require_appcontainer``, run with the Locus-managed
toolchain interpreter). The jail denies egress; :mod:`.egress_harness` (a
``sys.addaudithook`` hook in the child) makes each Python-level attempt visible
and denies it a second time.

With no confining tier the test is ``not-run`` (fail closed: the inspected code is
untrusted and never runs on the host). ``allow_unconfined=True`` exists only for
the unit tests, which run a fixture package Locus wrote itself; the result is
labelled so it can never pass an inspection.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from locus_runtime.sandbox import (
    ConfinementSelection,
    ExecutionSpec,
    IsolationStrategy,
    SandboxManager,
    SandboxPolicy,
    detect_host_platform,
    docker_cli_env,
    minimal_agent_env,
    select_confining_strategy,
)

HARNESS_PATH = Path(__file__).resolve().with_name("egress_harness.py")
UNCONFINED_LABEL = "unconfined (audit hook only; test fixtures)"

LIMITATIONS = (
    "Python audit hooks see socket, DNS, urllib/http.client and process events; native code "
    "calling the OS socket API directly raises no audit event. The jail blocks such calls "
    "but they are not logged, so native binaries are also scanned statically for "
    "networking imports.",
    "Only the import and the stated exercise were run; code paths they do not reach were "
    "not observed.",
)


@dataclass
class EgressResult:
    status: str  # pass | fail | error | not-run
    isolation: str
    interpreter: str = ""
    exit_code: int | None = None
    completed: bool = False
    attempts: list[dict[str, str]] = field(default_factory=list)
    other_events: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_sha256: str = ""
    detail: str = ""
    #: Positive control: a loopback connection to a host listener from inside the
    #: jail. ``blocked`` proves the jail denied egress; ``connected`` means it did not.
    jail_probe: str = "not-run"

    def as_record(self, *, exercise: str, artifact: str = "") -> dict[str, Any]:
        record: dict[str, Any] = {
            "status": self.status,
            "isolation": self.isolation,
            "network": "denied",
            "interpreter": self.interpreter,
            "exercise": exercise,
            "date": _dt.date.today().isoformat(),
            "exit_code": self.exit_code,
            "completed": self.completed,
            "jail_probe": self.jail_probe,
            "attempts": self.attempts,
            "other_events": self.other_events,
            "limitations": [*LIMITATIONS, *([self.detail] if self.detail else [])],
        }
        if artifact:
            record["artifact"] = artifact
        if self.log_sha256:
            record["log_sha256"] = self.log_sha256
        return record


@dataclass
class ParsedLog:
    attempts: list[dict[str, str]] = field(default_factory=list)
    other_events: list[dict[str, str]] = field(default_factory=list)
    completed: bool = False
    interpreter: str = ""
    errors: list[str] = field(default_factory=list)
    probe_connected: bool | None = None


def parse_log(text: str) -> ParsedLog:
    """What the harness recorded (see :mod:`.egress_harness` for the line format)."""
    parsed = ParsedLog()
    attempts = parsed.attempts
    others = parsed.other_events
    errors = parsed.errors
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            errors.append(f"unparseable log line: {line[:120]}")
            continue
        if not isinstance(item, dict):
            continue
        event = str(item.get("event", ""))
        if event == "harness.start":
            parsed.interpreter = f"CPython {item.get('python', '?')} ({item.get('platform', '?')})"
        elif event == "harness.done":
            parsed.completed = int(item.get("status", 1)) == 0
        elif event == "harness.probe":
            parsed.probe_connected = bool(item.get("connected"))
        elif event == "harness.error":
            errors.append(str(item.get("error", "")))
        elif event.startswith("harness."):
            continue
        else:
            entry = {
                "event": event,
                "target": str(item.get("target", "")),
                "outcome": str(item.get("outcome", "recorded")),
            }
            (attempts if item.get("kind") in {"network", "process"} else others).append(entry)
    return parsed


def classify(attempts: Sequence[dict[str, str]], completed: bool, errors: Sequence[str]) -> str:
    if attempts:
        return "fail"
    if not completed or errors:
        return "error"
    return "pass"


def _copy_inputs(work: Path, package_paths: Sequence[Path], exercise: str) -> tuple[list[str], str]:
    roots: list[str] = []
    for index, source in enumerate(package_paths):
        target = work / "pkgs" / str(index)
        shutil.copytree(source, target)
        roots.append(str(target))
    shutil.copy2(HARNESS_PATH, work / "harness.py")
    exercise_path = ""
    if exercise.strip():
        exercise_path = str(work / "exercise.py")
        Path(exercise_path).write_text(exercise, encoding="utf-8")
    (work / "out").mkdir()
    return roots, exercise_path


def run_egress_test(
    package_paths: Sequence[str | Path],
    modules: Sequence[str],
    *,
    exercise: str = "",
    timeout: int = 120,
    settle: float = 2.0,
    selection: ConfinementSelection | None = None,
    allow_unconfined: bool = False,
    toolchain: Any = None,
) -> EgressResult:
    """Run the harness in the jail and classify what it saw.

    ``package_paths`` are directories to put on ``sys.path`` (an unpacked wheel);
    they are copied into a fresh work directory, which is the only path the jail
    can read or write. ``selection``/``toolchain`` are injectable for tests.
    """
    choice = selection if selection is not None else select_confining_strategy()
    if choice.strategy is None and not allow_unconfined:
        return EgressResult(status="not-run", isolation="none", detail=choice.reason)
    work = Path(tempfile.mkdtemp(prefix="locus-provenance-"))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        listener.setblocking(False)
        probe = f"127.0.0.1:{listener.getsockname()[1]}"
        roots, exercise_path = _copy_inputs(work, [Path(p) for p in package_paths], exercise)
        log = work / "out" / "egress.jsonl"
        args = [str(work / "harness.py"), "--log", str(log), "--settle", str(settle)]
        args += ["--probe", probe]
        for root in roots:
            args += ["--path", root]
        args += ["--import", ",".join(modules)]
        if exercise_path:
            args += ["--exercise", exercise_path]
        command, run_env, launcher_cwd, isolation = _jailed_command(
            choice, args, work, timeout, allow_unconfined=allow_unconfined, toolchain=toolchain
        )
        if not command:
            return EgressResult(status="not-run", isolation=isolation, detail=launcher_cwd)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout + 30,
                env=run_env,
                cwd=launcher_cwd or None,
                check=False,
            )
            exit_code: int | None = proc.returncode
            stderr = (proc.stderr or "")[-2000:]
        except subprocess.TimeoutExpired:
            exit_code, stderr = None, "timed out"
        text = log.read_text(encoding="utf-8") if log.is_file() else ""
        parsed = parse_log(text)
        _scrub_paths(parsed, work)
        errors = parsed.errors
        if not text:
            errors.append(f"no harness log was written (exit {exit_code}): {stderr.strip()[-500:]}")
        accepted = _accepted_any(listener)
        connected = bool(parsed.probe_connected) or accepted
        jail_probe = (
            "not-run"
            if parsed.probe_connected is None and not accepted
            else ("connected" if connected else "blocked")
        )
        status = classify(parsed.attempts, parsed.completed, errors)
        if status == "pass" and jail_probe != "blocked":
            status = "error"
            errors.append(f"jail positive control was '{jail_probe}', so egress denial is unproven")
        result = EgressResult(
            status=status,
            isolation=isolation,
            interpreter=parsed.interpreter,
            exit_code=exit_code,
            completed=parsed.completed,
            attempts=parsed.attempts,
            other_events=parsed.other_events,
            errors=errors,
            log_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
            jail_probe=jail_probe,
        )
        if errors:
            result.detail = "Run errors: " + " | ".join(errors)[:600]
        return result
    finally:
        listener.close()
        shutil.rmtree(work, ignore_errors=True)


def _scrub_paths(parsed: ParsedLog, work: Path) -> None:
    """Replace the temporary work directory in recorded targets (no local paths in records)."""
    variants = {str(work), str(work).replace("\\", "\\\\"), work.as_posix()}
    for entry in [*parsed.attempts, *parsed.other_events]:
        for variant in sorted(variants, key=len, reverse=True):
            entry["target"] = entry["target"].replace(variant, "<workdir>")
        entry["target"] = re.sub(r" object at 0x[0-9A-Fa-f]+>", " object>", entry["target"])


def _accepted_any(listener: socket.socket) -> bool:
    try:
        conn, _ = listener.accept()
    except (BlockingIOError, OSError):
        return False
    conn.close()
    return True


def _jailed_command(
    choice: ConfinementSelection,
    harness_args: list[str],
    work: Path,
    timeout: int,
    *,
    allow_unconfined: bool,
    toolchain: Any,
) -> tuple[list[str], dict[str, str], str, str]:
    """``(argv, env, launcher_cwd, isolation label)``; an empty argv means not runnable
    (``launcher_cwd`` then carries the reason)."""
    strategy = choice.strategy
    if strategy is None:
        if not allow_unconfined:
            return [], {}, choice.reason, "none"
        return [sys.executable, *harness_args], minimal_agent_env({}), "", UNCONFINED_LABEL
    toolchain_root = ""
    path_prepend: list[str] | None = None
    if strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
        from locus_runtime.win_toolchain import MISSING_TOOLCHAIN_HINT, discover_toolchain

        chain = toolchain if toolchain is not None else discover_toolchain()
        if chain is None:
            return [], {}, MISSING_TOOLCHAIN_HINT, strategy.value
        command = chain.resolve(["python", *harness_args])
        toolchain_root = str(chain.root)
        path_prepend = chain.path_dirs()
    elif strategy == IsolationStrategy.HARDENED_DOCKER:
        command = ["python", *harness_args]
    else:
        command = [sys.executable, *harness_args]
    policy = SandboxPolicy(
        platform=detect_host_platform(),
        allow_network=False,
        allowed_read_paths=[str(work)],
        allowed_write_paths=[str(work)],
        allowed_executables=[command[0]],
        timeout_seconds=timeout,
        require_appcontainer=True,
        toolchain_root=toolchain_root,
    )
    spec = ExecutionSpec(tool_id="provenance-egress", command=command, cwd=str(work), env={})
    plan = SandboxManager(force_strategy=strategy).plan(spec, policy)
    if strategy == IsolationStrategy.HARDENED_DOCKER:
        env = docker_cli_env()
    else:
        env = minimal_agent_env({}, path_prepend=path_prepend)
    launcher_cwd = str(plan.metadata.get("launcher_cwd") or "")
    return plan.command, env, launcher_cwd, f"{strategy.value}, network denied, audit hook"
