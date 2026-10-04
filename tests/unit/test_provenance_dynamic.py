"""LOCUS-358 / D-29: the dynamic egress test records (and denies) connection attempts.

Fixture packages are written to tmp_path. The "phone home" fixture targets
TEST-NET-3 (203.0.113.0/24, never routed) and loopback; the audit hook denies
both before any packet is sent, so these tests never touch the network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from locus_runtime.sandbox import (
    ConfinementSelection,
    HostPlatform,
    IsolationStrategy,
    select_confining_strategy,
)
from locus_tooling.provenance import dynamic

UNCONFINED = ConfinementSelection(None, HostPlatform.LINUX, "test: no jail")

PHONE_HOME = (
    "import socket\n"
    "import threading\n\n"
    "def _beacon():\n"
    "    try:\n"
    "        socket.create_connection(('203.0.113.7', 443), timeout=1)\n"
    "    except OSError:\n"
    "        pass\n\n"
    "_beacon()\n"
    "try:\n"
    "    sock = socket.socket()\n"
    "    sock.connect(('127.0.0.1', 9))\n"
    "except OSError:\n"
    "    pass\n"
    "threading.Thread(target=_beacon, daemon=True).start()\n"
)


def _package(root: Path, name: str, body: str) -> Path:
    (root / name).mkdir(parents=True)
    (root / name / "__init__.py").write_text(body, encoding="utf-8")
    return root


def test_parse_log_and_classify() -> None:
    lines = [
        {"event": "harness.start", "python": "3.12.1", "platform": "linux"},
        {"event": "harness.probe", "connected": False},
        {
            "event": "socket.getaddrinfo",
            "kind": "network",
            "target": "('x', 1)",
            "outcome": "denied",
        },
        {"event": "ctypes.dlopen", "kind": "recorded", "target": "libfoo", "outcome": "recorded"},
        {"event": "harness.done", "status": 0},
    ]
    parsed = dynamic.parse_log("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")
    assert parsed.interpreter == "CPython 3.12.1 (linux)"
    assert parsed.completed and parsed.probe_connected is False
    assert [a["event"] for a in parsed.attempts] == ["socket.getaddrinfo"]
    assert [e["event"] for e in parsed.other_events] == ["ctypes.dlopen"]
    assert parsed.errors and "unparseable" in parsed.errors[0]
    assert dynamic.classify(parsed.attempts, True, []) == "fail"
    assert dynamic.classify([], True, []) == "pass"
    assert dynamic.classify([], False, []) == "error"


def test_no_confining_sandbox_means_not_run(tmp_path: Path) -> None:
    result = dynamic.run_egress_test(
        [_package(tmp_path, "quiet", "X = 1\n")], ["quiet"], selection=UNCONFINED
    )
    assert result.status == "not-run" and result.isolation == "none"


def test_connection_attempts_are_recorded_and_denied(tmp_path: Path) -> None:
    root = _package(tmp_path, "phonehome", PHONE_HOME)
    result = dynamic.run_egress_test(
        [root], ["phonehome"], selection=UNCONFINED, allow_unconfined=True, settle=0.5
    )
    assert result.status == "fail"
    assert result.isolation == dynamic.UNCONFINED_LABEL
    events = {(a["event"], a["outcome"]) for a in result.attempts}
    assert ("socket.getaddrinfo", "denied") in events
    assert ("socket.connect", "denied") in events
    targets = " ".join(a["target"] for a in result.attempts)
    assert "203.0.113.7" in targets and "127.0.0.1" in targets
    # The delayed thread's attempt is caught too.
    assert sum(1 for a in result.attempts if "203.0.113.7" in a["target"]) >= 2
    record = result.as_record(exercise="import phonehome")
    assert record["status"] == "fail" and record["attempts"]


def test_quiet_package_cannot_pass_without_a_jail(tmp_path: Path) -> None:
    root = _package(tmp_path, "quiet", "VALUE = 1\n")
    result = dynamic.run_egress_test(
        [root], ["quiet"], selection=UNCONFINED, allow_unconfined=True, settle=0.0
    )
    assert result.attempts == []
    assert result.jail_probe == "connected"
    assert result.status == "error"


def test_process_spawn_is_recorded(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        "spawner",
        "import subprocess\ntry:\n    subprocess.run(['echo', 'x'])\nexcept OSError:\n    pass\n",
    )
    result = dynamic.run_egress_test(
        [root], ["spawner"], selection=UNCONFINED, allow_unconfined=True, settle=0.0
    )
    assert result.status == "fail"
    assert any(a["event"] == "subprocess.Popen" for a in result.attempts)


def _local_jail() -> ConfinementSelection | None:
    """The host's AppContainer tier when the agent toolchain is installed; bubblewrap or
    seatbelt only with ``LOCUS_JAIL_TESTS=1`` (CI runners may ship bwrap without
    unprivileged user namespaces). Never Docker in unit tests."""
    selection = select_confining_strategy()
    if selection.strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
        from locus_runtime.win_toolchain import discover_toolchain

        return selection if discover_toolchain() is not None else None
    if selection.strategy in {IsolationStrategy.KERNEL_BWRAP, IsolationStrategy.KERNEL_SEATBELT}:
        return selection if os.getenv("LOCUS_JAIL_TESTS") == "1" else None
    return None


def test_in_the_real_jail_attempts_fail_and_the_positive_control_is_blocked(tmp_path: Path) -> None:
    selection = _local_jail()
    if selection is None:
        pytest.skip("no AppContainer toolchain here (set LOCUS_JAIL_TESTS=1 for bwrap/seatbelt)")
    phone = dynamic.run_egress_test(
        [_package(tmp_path / "a", "phonehome", PHONE_HOME)],
        ["phonehome"],
        selection=selection,
        settle=0.5,
    )
    assert phone.status == "fail", phone
    assert phone.jail_probe == "blocked"
    quiet = dynamic.run_egress_test(
        [_package(tmp_path / "b", "quiet", "VALUE = 1\n")],
        ["quiet"],
        selection=selection,
        exercise="import quiet\nassert quiet.VALUE == 1\n",
        settle=0.0,
    )
    assert quiet.status == "pass", quiet
    assert quiet.jail_probe == "blocked" and quiet.attempts == []
