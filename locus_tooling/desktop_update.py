"""File-based :class:`~locus_tooling.update_contract.UpdateChannel` (LOCUS-349, D-26).

The update holds the self-improvement loop through the loop's own single-run lock
(``<LOCUS_LOOP_HOME>/loop.lock``, :class:`locus_runtime.loop_runner.state.RunLock`)
under an owner starting with ``desktop-update``:

* a loop run in progress holds that lock, so the update cannot take it and waits
  -- a run is never killed for an update;
* while the update holds it, ``lattix loop serve`` ticks return ``busy`` and no
  new run starts;
* the hold has the loop lock's TTL, is refreshed on every ``prepare`` and is
  released by the supervisor on the next start (after the update, or after an
  aborted one).

The kill switch (``DISABLED`` file / ``LOCUS_LOOP_DISABLED``) is never touched:
it stays the principal's switch, and the resume step honours it.

Loop autostart is the persisted flag ``<LOCUS_LOOP_HOME>/desktop-autostart.json``
(``lattix loop autostart --repo <path>`` / ``--off``).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from locus_runtime.loop_runner.state import (
    Ledger,
    LoopBusy,
    RunLock,
    default_loop_home,
    kill_switch_reason,
    write_json_atomic,
)

from .build_info import backend_build_version
from .update_contract import (
    UPDATE_LOCK_OWNER_PREFIX,
    LoopAutostart,
    LoopResumeDecision,
    LoopUpdateState,
    UpdateReadiness,
    VersionHandshake,
    decide_loop_resume,
    decide_readiness,
    version_handshake,
)

LOCK_FILE = "loop.lock"
AUTOSTART_FILE = "desktop-autostart.json"
DEFAULT_LOCK_TTL_SECONDS = 7200.0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _lock_ttl_from_env() -> float:
    try:
        value = float(str(os.getenv("LOCUS_LOOP_LOCK_TTL_SECONDS") or "").strip() or 0)
    except ValueError:
        value = 0.0
    return value if value > 0 else DEFAULT_LOCK_TTL_SECONDS


# --------------------------------------------------------------------------- #
# Autostart flag
# --------------------------------------------------------------------------- #
def read_loop_autostart(home: Path) -> LoopAutostart:
    data = _read_json(home / AUTOSTART_FILE)
    try:
        return LoopAutostart(
            enabled=bool(data.get("enabled") is True),
            repo_path=str(data.get("repo_path") or ""),
        )
    except ValueError:
        return LoopAutostart()


def write_loop_autostart(home: Path, *, enabled: bool, repo_path: str = "") -> LoopAutostart:
    """Persist the flag. Enabling needs an existing repository with ``WORKFLOW.md``."""
    repo = str(repo_path or "").strip()
    if enabled:
        resolved = Path(repo).expanduser().resolve() if repo else None
        if resolved is None or not (resolved / "WORKFLOW.md").is_file():
            raise ValueError("--repo must be a checkout that contains WORKFLOW.md")
        repo = str(resolved)
    flag = LoopAutostart(enabled=enabled, repo_path=repo)
    write_json_atomic(home / AUTOSTART_FILE, flag.model_dump())
    return flag


def loop_serve_argv(repo_path: str, *, frozen: bool, executable: str) -> list[str]:
    """argv the supervisor uses for ``lattix loop serve`` on the installed code.

    The frozen sidecar has no ``lattix`` script, so both forms go through the
    ``--loop-serve`` mode of :mod:`locus_tooling.desktop_main`.
    """
    if frozen:
        return [executable, "--loop-serve", repo_path]
    return [executable, "-m", "locus_tooling.desktop_main", "--loop-serve", repo_path]


# --------------------------------------------------------------------------- #
# The port implementation
# --------------------------------------------------------------------------- #
class LocalUpdateChannel:
    """:class:`~locus_tooling.update_contract.UpdateChannel` over the loop home files."""

    def __init__(
        self,
        home: Path,
        *,
        build_version: str | None = None,
        lock_ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.home = Path(home)
        self.build_version = backend_build_version() if build_version is None else build_version
        self.lock_ttl_seconds = float(lock_ttl_seconds)
        self.clock = clock

    @property
    def lock_path(self) -> Path:
        return self.home / LOCK_FILE

    # -- state -------------------------------------------------------------
    def _lock_owner(self) -> str:
        holder = _read_json(self.lock_path)
        if not holder:
            return ""
        try:
            acquired = float(holder.get("acquired_at") or 0)
        except (TypeError, ValueError):
            acquired = 0.0
        if float(self.clock()) - acquired >= self.lock_ttl_seconds:
            return ""  # stale, same TTL rule as RunLock: the holder crashed or overran
        return str(holder.get("owner") or "unknown")

    def _loop_state(self, *, held: bool, owner: str) -> LoopUpdateState:
        reason = kill_switch_reason(self.home)
        active = Ledger.load(self.home).active or {}
        pending = str(active.get("run_id") or "") or None
        return LoopUpdateState(
            enabled=not reason,
            kill_switch=reason,
            paused_for_update=held,
            lock_owner=None if held or not owner else owner,
            pending_run_id=pending,
            autostart=read_loop_autostart(self.home).enabled,
        )

    # -- port --------------------------------------------------------------
    def readiness(self, active_runs: int) -> UpdateReadiness:
        owner = self._lock_owner()
        held = owner.startswith(UPDATE_LOCK_OWNER_PREFIX)
        return decide_readiness(
            active_runs=active_runs,
            loop=self._loop_state(held=held, owner=owner),
            build_version=self.build_version,
        )

    def prepare(self, active_runs: int) -> UpdateReadiness:
        owner = self._lock_owner()
        held = False
        if owner.startswith(UPDATE_LOCK_OWNER_PREFIX):
            # Our own hold: refresh it so it outlives a long wait for agent runs.
            write_json_atomic(
                self.lock_path,
                {"owner": owner, "pid": os.getpid(), "acquired_at": self.clock()},
            )
            held = True
        else:
            lock = RunLock(self.lock_path, self.lock_ttl_seconds, clock=self.clock)
            new_owner = f"{UPDATE_LOCK_OWNER_PREFIX}-{secrets.token_hex(4)}"
            try:
                lock.acquire(owner=new_owner)
            except LoopBusy:
                owner = self._lock_owner() or owner or "unknown"
            else:
                held, owner = True, new_owner
        return decide_readiness(
            active_runs=active_runs,
            loop=self._loop_state(held=held, owner=owner),
            build_version=self.build_version,
        )

    def release(self) -> bool:
        holder = _read_json(self.lock_path)
        if not str(holder.get("owner") or "").startswith(UPDATE_LOCK_OWNER_PREFIX):
            return False
        with suppress(FileNotFoundError):
            self.lock_path.unlink()
        return True

    def handshake(self, app_version: str) -> VersionHandshake:
        return version_handshake(app_version, self.build_version)

    def resume_after_restart(self) -> LoopResumeDecision:
        released = self.release()
        autostart = read_loop_autostart(self.home)
        repo_ok = (
            bool(autostart.repo_path) and (Path(autostart.repo_path) / "WORKFLOW.md").is_file()
        )
        return decide_loop_resume(
            autostart,
            kill_switch=kill_switch_reason(self.home),
            repo_has_workflow=repo_ok,
            released_update_hold=released,
        )


def default_update_channel() -> LocalUpdateChannel:
    return LocalUpdateChannel(default_loop_home(), lock_ttl_seconds=_lock_ttl_from_env())
