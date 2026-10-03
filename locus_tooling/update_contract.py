"""Update-coordination port for the desktop update channels (LOCUS-349, D-26).

D-28 (modular ports): the backend endpoints, the desktop supervisor and the tests
depend on this contract, not on the file-based implementation in
:mod:`locus_tooling.desktop_update`.

The port answers three questions for the desktop shell:

* **Readiness** -- may an update be installed now? Only when no agent run is in
  progress and the self-improvement loop is held (its single-run lock is owned by
  the update, so no new loop run can start and none is running).
* **Version handshake** -- is the backend that answered the one this app shipped
  with? (tauri#15134: a Windows NSIS update can keep a stale sidecar.)
* **Loop resume** -- after a restart, should the supervisor start
  ``lattix loop serve`` again? Only when the principal enabled autostart and the
  kill switch is off.

The decision functions here are pure; IO lives behind :class:`UpdateChannel`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

PORT_VERSION = "1.0"

#: Owner prefix of the loop lock while an update holds the loop.
UPDATE_LOCK_OWNER_PREFIX = "desktop-update"


class HandshakeResult(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    #: The backend carries no build stamp (source checkout or local build).
    UNSTAMPED = "unstamped"
    #: The caller did not say which app version it expects.
    UNKNOWN_APP = "unknown_app"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class VersionHandshake(_Model):
    port_version: str = PORT_VERSION
    app_version: str
    backend_version: str
    result: HandshakeResult
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.result is HandshakeResult.MATCH


class LoopUpdateState(_Model):
    #: The kill switch is off (``kill_switch`` is empty).
    enabled: bool
    kill_switch: str = ""
    #: The update owns the loop lock: no loop run is running or can start.
    paused_for_update: bool = False
    #: Owner of the loop lock when someone else holds it (a running loop tick/run).
    lock_owner: str | None = None
    #: A crashed run waiting for resume (resumes after the restart; not running).
    pending_run_id: str | None = None
    autostart: bool = False


class UpdateReadiness(_Model):
    port_version: str = PORT_VERSION
    ready: bool
    active_runs: int = Field(ge=0)
    loop: LoopUpdateState
    build_version: str = ""
    reason: str = ""


class LoopAutostart(_Model):
    """The persisted flag: the desktop supervisor runs ``lattix loop serve``."""

    enabled: bool = False
    repo_path: str = Field(default="", max_length=4096)


class LoopResumeDecision(_Model):
    start: bool
    reason: str
    released_update_hold: bool = False


@runtime_checkable
class UpdateChannel(Protocol):
    """The port. Implementations: :class:`locus_tooling.desktop_update.LocalUpdateChannel`."""

    def readiness(self, active_runs: int) -> UpdateReadiness:
        """Read-only: would an update be allowed now? Holds nothing."""
        ...

    def prepare(self, active_runs: int) -> UpdateReadiness:
        """Hold the loop for the update (idempotent) and report readiness.

        Never stops a running loop run: when one holds the lock, nothing is held
        and ``ready`` is false.
        """
        ...

    def release(self) -> bool:
        """Release an update hold on the loop; True when one was released."""
        ...

    def handshake(self, app_version: str) -> VersionHandshake:
        """Compare the app version with this backend's stamped build version."""
        ...

    def resume_after_restart(self) -> LoopResumeDecision:
        """Release any update hold, then decide whether to start the loop."""
        ...


# --------------------------------------------------------------------------- #
# Pure decisions
# --------------------------------------------------------------------------- #
def _normalize_version(value: str) -> str:
    text = str(value or "").strip()
    return text[1:] if text[:1] in {"v", "V"} else text


def version_handshake(app_version: str, backend_version: str) -> VersionHandshake:
    app = _normalize_version(app_version)
    backend = _normalize_version(backend_version)
    if not app:
        result, detail = HandshakeResult.UNKNOWN_APP, "the app did not report its version"
    elif not backend:
        result, detail = (
            HandshakeResult.UNSTAMPED,
            "the backend has no build stamp (source or local build)",
        )
    elif app == backend:
        result, detail = HandshakeResult.MATCH, ""
    else:
        result, detail = (
            HandshakeResult.MISMATCH,
            f"backend {backend} does not match app {app}; the sidecar was not replaced",
        )
    return VersionHandshake(app_version=app, backend_version=backend, result=result, detail=detail)


def decide_readiness(
    *,
    active_runs: int,
    loop: LoopUpdateState,
    build_version: str = "",
) -> UpdateReadiness:
    runs = max(0, int(active_runs))
    if runs:
        reason = f"{runs} agent run(s) in progress"
    elif not loop.paused_for_update:
        reason = (
            f"the loop is busy ({loop.lock_owner})"
            if loop.lock_owner
            else "the loop is not held for the update"
        )
    else:
        reason = ""
    return UpdateReadiness(
        ready=not reason,
        active_runs=runs,
        loop=loop,
        build_version=build_version,
        reason=reason,
    )


def decide_loop_resume(
    autostart: LoopAutostart,
    *,
    kill_switch: str,
    repo_has_workflow: bool,
    released_update_hold: bool = False,
) -> LoopResumeDecision:
    if not autostart.enabled:
        reason, start = "loop autostart is off", False
    elif kill_switch:
        reason, start = f"kill switch: {kill_switch}", False
    elif not autostart.repo_path or not repo_has_workflow:
        reason, start = "the loop repository has no WORKFLOW.md", False
    else:
        reason, start = "autostart is on and the kill switch is off", True
    return LoopResumeDecision(start=start, reason=reason, released_update_hold=released_update_hold)


__all__ = [
    "PORT_VERSION",
    "UPDATE_LOCK_OWNER_PREFIX",
    "HandshakeResult",
    "LoopAutostart",
    "LoopResumeDecision",
    "LoopUpdateState",
    "UpdateChannel",
    "UpdateReadiness",
    "VersionHandshake",
    "decide_loop_resume",
    "decide_readiness",
    "version_handshake",
]
