"""LOCUS-349 (D-26) update coordination, tested against the port (D-28).

The decision functions are pure; the contract suite runs every
:class:`UpdateChannel` implementation through the same behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from locus_tooling import build_info
from locus_tooling.desktop_update import (
    AUTOSTART_FILE,
    LocalUpdateChannel,
    loop_serve_argv,
    read_loop_autostart,
    write_loop_autostart,
)
from locus_tooling.update_contract import (
    PORT_VERSION,
    UPDATE_LOCK_OWNER_PREFIX,
    HandshakeResult,
    LoopAutostart,
    LoopUpdateState,
    UpdateChannel,
    UpdateReadiness,
    decide_loop_resume,
    decide_readiness,
    version_handshake,
)


# --------------------------------------------------------------------------- #
# Pure decisions
# --------------------------------------------------------------------------- #
def test_port_version_is_pinned() -> None:
    assert PORT_VERSION == "1.0"
    assert (
        UpdateReadiness(ready=True, active_runs=0, loop=LoopUpdateState(enabled=True)).port_version
        == "1.0"
    )


@pytest.mark.parametrize(
    ("app", "backend", "result"),
    [
        ("0.1.0-dev.42", "0.1.0-dev.42", HandshakeResult.MATCH),
        ("v0.1.0-dev.42", "0.1.0-dev.42", HandshakeResult.MATCH),
        ("0.1.0-dev.43", "0.1.0-dev.42", HandshakeResult.MISMATCH),
        ("0.1.0", "0.1.0-dev.42", HandshakeResult.MISMATCH),
        ("0.1.0-dev.42", "", HandshakeResult.UNSTAMPED),
        ("", "0.1.0-dev.42", HandshakeResult.UNKNOWN_APP),
    ],
)
def test_version_handshake(app: str, backend: str, result: HandshakeResult) -> None:
    handshake = version_handshake(app, backend)
    assert handshake.result is result
    assert handshake.ok is (result is HandshakeResult.MATCH)
    if result is HandshakeResult.MISMATCH:
        assert "does not match" in handshake.detail


def test_readiness_requires_no_runs_and_a_held_loop() -> None:
    held = LoopUpdateState(enabled=True, paused_for_update=True)
    free = LoopUpdateState(enabled=True)
    busy = LoopUpdateState(enabled=True, lock_owner="tick-abcd")

    assert decide_readiness(active_runs=0, loop=held).ready is True
    waiting = decide_readiness(active_runs=2, loop=held)
    assert waiting.ready is False and "2 agent run(s)" in waiting.reason
    assert "busy (tick-abcd)" in decide_readiness(active_runs=0, loop=busy).reason
    assert decide_readiness(active_runs=0, loop=free).ready is False
    # Negative counts never make it ready by accident.
    assert decide_readiness(active_runs=-3, loop=held).active_runs == 0


def test_readiness_model_rejects_unknown_fields_and_negative_runs() -> None:
    with pytest.raises(ValidationError):
        UpdateReadiness(ready=True, active_runs=-1, loop=LoopUpdateState(enabled=True))
    with pytest.raises(ValidationError):
        LoopAutostart.model_validate({"enabled": True, "repo_path": "x", "url": "https://evil"})


@pytest.mark.parametrize(
    ("autostart", "kill", "repo_ok", "start"),
    [
        (LoopAutostart(enabled=True, repo_path="/r"), "", True, True),
        (LoopAutostart(enabled=False, repo_path="/r"), "", True, False),
        (LoopAutostart(enabled=True, repo_path="/r"), "kill-switch file exists", True, False),
        (LoopAutostart(enabled=True, repo_path="/r"), "", False, False),
        (LoopAutostart(enabled=True, repo_path=""), "", True, False),
    ],
)
def test_loop_resume_decision(
    autostart: LoopAutostart, kill: str, repo_ok: bool, start: bool
) -> None:
    decision = decide_loop_resume(autostart, kill_switch=kill, repo_has_workflow=repo_ok)
    assert decision.start is start
    if kill:
        assert "kill switch" in decision.reason


# --------------------------------------------------------------------------- #
# Contract suite (every implementation of the port)
# --------------------------------------------------------------------------- #
def _write_lock(home: Path, owner: str, acquired_at: float) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "loop.lock").write_text(
        json.dumps({"owner": owner, "pid": 1, "acquired_at": acquired_at}), encoding="utf-8"
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text("---\npolling: {}\n---\n", encoding="utf-8")
    return repo


class _Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _no_env_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCUS_LOOP_DISABLED", raising=False)


@pytest.fixture(params=["local"])
def make_channel(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    clock = _Clock()

    def factory(build_version: str = "0.1.0-dev.7") -> tuple[UpdateChannel, Path, _Clock]:
        home = tmp_path / "loop"
        if request.param == "local":
            return (
                LocalUpdateChannel(
                    home, build_version=build_version, lock_ttl_seconds=600, clock=clock
                ),
                home,
                clock,
            )
        raise AssertionError(request.param)

    return factory


def test_implementation_satisfies_the_protocol(make_channel: Any) -> None:
    channel, _home, _clock = make_channel()
    assert isinstance(channel, UpdateChannel)


def test_prepare_holds_the_loop_lock_and_is_idempotent(make_channel: Any) -> None:
    channel, home, clock = make_channel()
    first = channel.prepare(0)
    assert first.ready is True and first.loop.paused_for_update is True
    owner = json.loads((home / "loop.lock").read_text(encoding="utf-8"))["owner"]
    assert owner.startswith(UPDATE_LOCK_OWNER_PREFIX)

    clock.now += 300
    second = channel.prepare(0)  # refreshes the same hold
    lock = json.loads((home / "loop.lock").read_text(encoding="utf-8"))
    assert second.ready is True and lock["owner"] == owner
    assert lock["acquired_at"] == clock.now
    assert channel.readiness(0).loop.paused_for_update is True


def test_prepare_never_takes_a_running_loop_lock(make_channel: Any) -> None:
    channel, home, clock = make_channel()
    _write_lock(home, "tick-1234", clock.now)
    result = channel.prepare(0)
    assert result.ready is False
    assert result.loop.paused_for_update is False
    assert result.loop.lock_owner == "tick-1234"
    # The loop's lock is untouched.
    assert json.loads((home / "loop.lock").read_text(encoding="utf-8"))["owner"] == "tick-1234"
    assert channel.release() is False


def test_prepare_takes_a_stale_loop_lock(make_channel: Any) -> None:
    channel, home, clock = make_channel()
    _write_lock(home, "tick-dead", clock.now - 601)
    assert channel.prepare(0).ready is True


def test_agent_runs_keep_the_hold_but_block_the_install(make_channel: Any) -> None:
    channel, _home, _clock = make_channel()
    result = channel.prepare(3)
    assert result.ready is False and result.active_runs == 3
    assert result.loop.paused_for_update is True  # no new loop run meanwhile


def test_readiness_is_read_only(make_channel: Any) -> None:
    channel, home, _clock = make_channel()
    result = channel.readiness(0)
    assert result.ready is False
    assert not (home / "loop.lock").exists()


def test_release_only_removes_an_update_hold(make_channel: Any) -> None:
    channel, home, _clock = make_channel()
    channel.prepare(0)
    assert channel.release() is True
    assert not (home / "loop.lock").exists()
    assert channel.release() is False


def test_handshake_uses_the_stamped_build(make_channel: Any) -> None:
    channel, _home, _clock = make_channel("0.1.0-dev.7")
    assert channel.handshake("0.1.0-dev.7").result is HandshakeResult.MATCH
    assert channel.handshake("0.1.0-dev.8").result is HandshakeResult.MISMATCH
    unstamped, _home, _clock = make_channel("")
    assert unstamped.handshake("0.1.0-dev.8").result is HandshakeResult.UNSTAMPED


def test_resume_releases_the_hold_and_starts_the_loop(make_channel: Any, tmp_path: Path) -> None:
    channel, home, _clock = make_channel()
    write_loop_autostart(home, enabled=True, repo_path=str(_repo(tmp_path)))
    channel.prepare(0)
    decision = channel.resume_after_restart()
    assert decision.start is True and decision.released_update_hold is True
    assert not (home / "loop.lock").exists()


def test_resume_honours_the_kill_switch(make_channel: Any, tmp_path: Path) -> None:
    channel, home, _clock = make_channel()
    write_loop_autostart(home, enabled=True, repo_path=str(_repo(tmp_path)))
    (home / "DISABLED").write_text("off\n", encoding="utf-8")
    decision = channel.resume_after_restart()
    assert decision.start is False and "kill switch" in decision.reason
    # The update never touches the principal's kill switch.
    assert (home / "DISABLED").exists()


def test_resume_honours_the_env_kill_switch(
    make_channel: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel, home, _clock = make_channel()
    write_loop_autostart(home, enabled=True, repo_path=str(_repo(tmp_path)))
    monkeypatch.setenv("LOCUS_LOOP_DISABLED", "1")
    assert channel.resume_after_restart().start is False


def test_resume_without_autostart(make_channel: Any) -> None:
    channel, _home, _clock = make_channel()
    decision = channel.resume_after_restart()
    assert decision.start is False and "autostart is off" in decision.reason


# --------------------------------------------------------------------------- #
# Autostart flag, argv, supervisor wiring
# --------------------------------------------------------------------------- #
def test_autostart_requires_a_workflow_repo(tmp_path: Path) -> None:
    home = tmp_path / "loop"
    with pytest.raises(ValueError):
        write_loop_autostart(home, enabled=True, repo_path=str(tmp_path / "missing"))
    flag = write_loop_autostart(home, enabled=True, repo_path=str(_repo(tmp_path)))
    assert flag.enabled and Path(flag.repo_path).is_absolute()
    assert read_loop_autostart(home) == flag
    assert write_loop_autostart(home, enabled=False).enabled is False


def test_autostart_tolerates_a_corrupt_file(tmp_path: Path) -> None:
    home = tmp_path / "loop"
    home.mkdir()
    (home / AUTOSTART_FILE).write_text("{not json", encoding="utf-8")
    assert read_loop_autostart(home) == LoopAutostart()
    (home / AUTOSTART_FILE).write_text(json.dumps({"enabled": "yes"}), encoding="utf-8")
    assert read_loop_autostart(home).enabled is False  # only a real true enables it


def test_loop_serve_argv() -> None:
    assert loop_serve_argv("/r", frozen=True, executable="/app/locus-backend") == [
        "/app/locus-backend",
        "--loop-serve",
        "/r",
    ]
    assert loop_serve_argv("/r", frozen=False, executable="python") == [
        "python",
        "-m",
        "locus_tooling.desktop_main",
        "--loop-serve",
        "/r",
    ]


class _FakeSupervisor:
    instances: list[_FakeSupervisor] = []

    def __init__(self, plan: Any, *, log: Any = None) -> None:
        self.plan = plan
        self.started = False
        _FakeSupervisor.instances.append(self)

    def start_all(self) -> dict[str, str]:
        self.started = True
        return {}

    def stop_all(self) -> None:
        self.started = False


def test_supervisor_restarts_the_loop_after_an_update(tmp_path: Path) -> None:
    from locus_tooling import desktop

    home = tmp_path / "loop"
    repo = _repo(tmp_path)
    write_loop_autostart(home, enabled=True, repo_path=str(repo))
    channel = LocalUpdateChannel(home, build_version="0.1.0-dev.7")
    channel.prepare(0)  # the update held the loop before the restart
    _FakeSupervisor.instances.clear()
    logs: list[str] = []
    try:
        decision = desktop.resume_loop_after_start(
            log=logs.append, channel=channel, supervisor_factory=_FakeSupervisor
        )
        assert decision.start is True
        (supervisor,) = _FakeSupervisor.instances
        assert supervisor.started
        (spec,) = supervisor.plan.services
        assert spec.name == "loop" and spec.required is False
        assert spec.argv[-2:] == ["--loop-serve", str(repo.resolve())]
        assert any("released the update hold" in line for line in logs)
        assert supervisor in desktop._LIVE_SUPERVISORS  # noqa: SLF001 - stopped on shutdown
    finally:
        for sup in _FakeSupervisor.instances:
            if sup in desktop._LIVE_SUPERVISORS:  # noqa: SLF001
                desktop._LIVE_SUPERVISORS.remove(sup)  # noqa: SLF001


def test_supervisor_does_not_start_the_loop_when_killed(tmp_path: Path) -> None:
    from locus_tooling import desktop

    home = tmp_path / "loop"
    write_loop_autostart(home, enabled=True, repo_path=str(_repo(tmp_path)))
    (home / "DISABLED").write_text("off\n", encoding="utf-8")
    _FakeSupervisor.instances.clear()
    decision = desktop.resume_loop_after_start(
        log=lambda _m: None,
        channel=LocalUpdateChannel(home, build_version=""),
        supervisor_factory=_FakeSupervisor,
    )
    assert decision.start is False
    assert _FakeSupervisor.instances == []


# --------------------------------------------------------------------------- #
# Build stamp
# --------------------------------------------------------------------------- #
def test_build_stamp_round_trip(tmp_path: Path) -> None:
    path = build_info.stamp_build_version("0.1.0-dev.42", tmp_path / "_stamp.py")
    namespace: dict[str, Any] = {}
    exec(path.read_text(encoding="utf-8"), namespace)  # noqa: S102 - our own generated file
    assert namespace["BUILD_VERSION"] == "0.1.0-dev.42"


@pytest.mark.parametrize("bad", ["", "v0.1.0", "0.1", "0.1.0-dev.42'; import os", "0.1.0\nX = 1"])
def test_build_stamp_rejects_non_semver(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        build_info.stamp_build_version(bad, tmp_path / "_stamp.py")
    assert build_info.main([bad]) == 2


def test_unstamped_source_checkout_reports_empty() -> None:
    if build_info.STAMP_FILE.exists():  # pragma: no cover - only in a stamped CI build tree
        pytest.skip("this tree carries a CI build stamp")
    assert build_info.backend_build_version() == ""
