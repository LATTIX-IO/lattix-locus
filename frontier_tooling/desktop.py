"""Desktop (Tauri) integration for the native install.

The Tauri shell spawns ONE backend sidecar — the packaged supervisor — which
brings up every native service (Postgres+pgvector, Neo4j world models, NATS,
Ollama, the agents, the backend, and the frontend) via :mod:`native_launcher`,
then blocks. Tauri waits for the backend ``/healthz`` and opens its webview at
the local UI.

This module is the seam between "running from a git checkout" and "running as a
PyInstaller/Nuitka-frozen binary inside a Tauri bundle": it resolves the bundled
binary dir and app-home, builds the desktop :class:`NativeConfig`, and exposes
``run_desktop_supervisor`` (the frozen entrypoint, see ``desktop_main.py``).
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
from pathlib import Path

from .common import default_app_home, source_repo_root
from .native_launcher import NativeConfig, NativePlan, NativeSupervisor, build_native_plan

# Live supervisors so the backend's /system/shutdown (and signal/atexit hooks)
# can tear down every spawned child process (frontend, DB, model, agents).
_LIVE_SUPERVISORS: list[NativeSupervisor] = []
_SHUTDOWN_HOOKS_INSTALLED = False


def shutdown_supervisors() -> None:
    """Stop every running supervisor — kills the whole child-process tree."""
    for supervisor in list(_LIVE_SUPERVISORS):
        try:
            supervisor.stop_all()
        except Exception:  # noqa: BLE001
            pass


def _install_shutdown_hooks() -> None:
    global _SHUTDOWN_HOOKS_INSTALLED  # noqa: PLW0603
    if _SHUTDOWN_HOOKS_INSTALLED:
        return
    _SHUTDOWN_HOOKS_INSTALLED = True
    atexit.register(shutdown_supervisors)

    def _handler(signum, _frame):  # noqa: ANN001
        shutdown_supervisors()
        os._exit(0)

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except Exception:  # noqa: BLE001 - not all signals are settable everywhere
                pass


def is_frozen() -> bool:
    """True when running as a PyInstaller/Nuitka-frozen executable."""
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """Root for Tauri-bundled siblings (the vendored ``bin/`` + ``resources/``).

    Tauri places these next to the INSTALLED executable, so for a frozen sidecar
    we use the exe's directory — NOT PyInstaller's ``_MEIPASS`` temp (that only
    holds the sidecar's own Python payload; ``import app.main`` resolves from the
    PYZ automatically). From a source checkout it's the repo root.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return source_repo_root()


def bundled_bin_dir() -> Path:
    """Where the vendored sidecar binaries live inside the bundle."""
    return bundled_root() / "bin"


def desktop_app_home() -> Path:
    explicit = str(os.getenv("FRONTIER_APP_HOME") or "").strip()
    return Path(explicit) if explicit else default_app_home()


def writable_bin_dir() -> Path:
    """Where first-run-fetched sidecars are written (must be writable, unlike the
    read-only bundle). The launcher searches this dir; bundled binaries are found
    via PATH (see :func:`run_desktop_supervisor`)."""
    return desktop_app_home() / "bin"


def desktop_config(**overrides: object) -> NativeConfig:
    """A :class:`NativeConfig` tuned for the desktop bundle.

    ``bin_dir`` is the writable app-home bin (where first-run fetch lands) and
    ``degrade_when_missing`` keeps the app booting before sidecars arrive. The
    packaged exe IS the backend (served in-process — see
    :func:`run_desktop_supervisor`), so ``manage_backend`` is off and agents run
    in-process via the harness (no ``python -m uvicorn`` subprocesses, which a
    frozen bundle can't spawn). When frozen, the frontend is the staged bundle
    resources dir.
    """
    kwargs: dict[str, object] = {
        "app_home": desktop_app_home(),
        "bin_dir": writable_bin_dir(),
        "degrade_when_missing": True,
        "manage_backend": False,
        "enable_agents": False,
    }
    if is_frozen():
        kwargs["frontend_dir"] = str(bundled_root() / "resources" / "frontend")
    kwargs.update(overrides)
    return NativeConfig(**kwargs)  # type: ignore[arg-type]


def build_desktop_plan(**overrides: object) -> NativePlan:
    return build_native_plan(desktop_config(**overrides))


def _safe(fn, *args, log=print, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log(f"[firstrun] background provisioning error: {exc}")


def run_desktop_supervisor(*, log=print, **overrides: object) -> None:
    """Desktop entrypoint: start the sidecars + frontend, kick off first-run
    provisioning in the background (so the UI appears immediately rather than
    blocking on a multi-GB model download), then **serve the backend in-process**
    with uvicorn (blocks until the shell terminates us)."""
    import os
    import threading

    from .native_launcher import NativePlan

    cfg = desktop_config(**overrides)
    plan = build_native_plan(cfg)
    for warning in plan.warnings:
        log(f"warning: {warning}")
    # The in-process backend reads these at import (POSTGRES_DSN / SQLite path /
    # world-graph flag / bearer token), so set them before importing app.main.
    os.environ.update(plan.env)

    # The backend hard-fails startup if its STATE store can't connect, but
    # Postgres comes up asynchronously in the background and isn't ready yet.
    # Pin state to SQLite (no startup DB dependency) so the app boots fast and
    # reliably; long-term/world-graph memory (Postgres-backed) attaches on a
    # later launch once Postgres is initialized.
    sqlite_state = Path(cfg.app_home) / "data" / "state" / "frontier-state.db"
    sqlite_state.parent.mkdir(parents=True, exist_ok=True)
    os.environ["FRONTIER_SQLITE_STATE_PATH"] = str(sqlite_state)
    os.environ.pop("POSTGRES_DSN", None)
    os.environ["FRONTIER_MEMORY_ENABLE_LONG_TERM"] = "false"
    os.environ["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] = "false"

    # FAST PATH: start only the frontend synchronously so the window appears in
    # seconds. Everything heavy (DB init, Ollama serve + the multi-GB model pull,
    # first-run binary fetch) runs in the background so it never blocks the UI.
    _install_shutdown_hooks()
    fast = NativePlan([s for s in plan.services if s.name == "frontend"], plan.env, [])
    fast_supervisor = NativeSupervisor(fast, log=log)
    fast_supervisor.start_all()
    _LIVE_SUPERVISORS.append(fast_supervisor)

    from .desktop_firstrun import ensure_sidecars

    deferred_supervisors: list = []

    def _bring_up_infra() -> None:
        # Fetch any missing sidecar binaries, then start DB/model services + pull
        # the model (re-plan so newly-fetched binaries are picked up).
        ensure_sidecars(writable_bin_dir(), model=None, progress=log)
        plan2 = build_native_plan(desktop_config(**overrides))
        deferred = NativePlan([s for s in plan2.services if s.name != "frontend"], plan2.env, [])
        sup = NativeSupervisor(deferred, log=log)
        deferred_supervisors.append(sup)
        _LIVE_SUPERVISORS.append(sup)
        sup.start_all()
        log("[firstrun] background services ready")

    threading.Thread(target=lambda: _safe(_bring_up_infra, log=log), daemon=True).start()

    log("[firstrun] starting backend…")
    try:
        import uvicorn
        from app.main import app as fastapi_app

        uvicorn.run(fastapi_app, host=cfg.bind_host, port=cfg.backend_port, log_level="warning")
    finally:
        fast_supervisor.stop_all()
        for sup in deferred_supervisors:
            sup.stop_all()
