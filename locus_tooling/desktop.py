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
from collections.abc import MutableMapping
from pathlib import Path

from .common import default_app_home, source_repo_root
from .native_launcher import (
    HealthCheck,
    NativeConfig,
    NativePlan,
    NativeSupervisor,
    ServiceSpec,
    build_native_plan,
)
from .update_contract import LoopResumeDecision, UpdateChannel

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


def bundled_opa_binary() -> Path | None:
    """The OPA policy engine the desktop bundle ships beside this sidecar.

    Tauri installs ``externalBin`` binaries next to the app executable with the
    target-triple suffix stripped, so the frozen sidecar finds ``locus-opa(.exe)``
    in its own directory on every platform (``Contents/MacOS`` on macOS). None
    outside a frozen bundle (a checkout uses ``.tools/opa``, the bin dir or PATH).
    """
    if not is_frozen():
        return None
    from .opa_release import bundled_binary_name

    return bundled_root() / bundled_binary_name()


def configure_bundled_opa(environ: MutableMapping[str, str] | None = None) -> Path | None:
    """Point ``LOCUS_OPA_BIN`` at the bundled OPA; call before importing the backend.

    The backend's gateway starts its policy engine at import and denies every
    action without one (fail closed), so the bundled binary must be found first.
    An explicit ``LOCUS_OPA_BIN`` naming an existing file wins. Returns the path
    now configured, or None when there is none (the gateway then denies, and the
    UI says the policy engine is missing).
    """
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    explicit = str(env.get("LOCUS_OPA_BIN") or "").strip()
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    bundled = bundled_opa_binary()
    if bundled is not None and bundled.is_file():
        env["LOCUS_OPA_BIN"] = str(bundled)
        return bundled
    return None


def desktop_app_home() -> Path:
    explicit = str(os.getenv("LOCUS_APP_HOME") or "").strip()
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
        # The folder picker and loop autostart confine paths to this root. The
        # server default (/projects) doesn't exist on a desktop, so default to
        # the user's home; LOCUS_PROJECTS_ROOT overrides it.
        "projects_root": os.getenv("LOCUS_PROJECTS_ROOT") or str(Path.home()),
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


def resume_loop_after_start(
    *,
    log=print,
    channel: UpdateChannel | None = None,
    supervisor_factory=NativeSupervisor,
) -> LoopResumeDecision:
    """Release an update's hold on the loop, then restart ``lattix loop serve``
    when loop autostart is on and the kill switch is off (LOCUS-349).

    Runs on every desktop start, so after an update restart the loop resumes on
    the new code. The loop runs as a supervised child (``--loop-serve``), so
    quit, ``/system/shutdown`` and the next update stop it with everything else;
    a run interrupted that way resumes from its checkpoint on the next start.
    """
    from .desktop_update import default_update_channel, loop_serve_argv, read_loop_autostart

    port = channel or default_update_channel()
    decision = port.resume_after_restart()
    if decision.released_update_hold:
        log("[loop] released the update hold on the loop")
    log(f"[loop] {'starting' if decision.start else 'not starting'}: {decision.reason}")
    if not decision.start:
        return decision
    home = getattr(port, "home", None)
    autostart = read_loop_autostart(home) if isinstance(home, Path) else None
    if autostart is None or not autostart.repo_path:
        return LoopResumeDecision(
            start=False,
            reason="loop autostart has no repository",
            released_update_hold=decision.released_update_hold,
        )
    spec = ServiceSpec(
        name="loop",
        argv=loop_serve_argv(autostart.repo_path, frozen=is_frozen(), executable=sys.executable),
        cwd=autostart.repo_path,
        health=HealthCheck(kind="none"),
        required=False,
    )
    supervisor = supervisor_factory(NativePlan([spec], {}, []), log=log)
    supervisor.start_all()
    _LIVE_SUPERVISORS.append(supervisor)
    return decision


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
    # reliably. The world graph (Postgres-backed) stays off on the desktop.
    sqlite_state = Path(cfg.app_home) / "data" / "state" / "locus-state.db"
    sqlite_state.parent.mkdir(parents=True, exist_ok=True)
    os.environ["LOCUS_SQLITE_STATE_PATH"] = str(sqlite_state)
    os.environ.pop("POSTGRES_DSN", None)
    os.environ["LOCUS_MEMORY_GRAPH_PROJECTION_ENABLED"] = "false"
    # Long-term memory is ON by default (LOCUS-387): the embedded SQLite store
    # (FTS5 + sqlite-vec) under the app home, with no server to start. First run
    # creates it (owner-only) and its Personal collection before the backend is
    # imported; embeddings come from the local engine once its model is pulled.
    from locus_runtime.memory.bootstrap import default_store_path

    from .desktop_firstrun import ensure_memory_store

    memory_db = default_store_path(Path(cfg.app_home))
    os.environ["LOCUS_MEMORY_ENABLE_LONG_TERM"] = "true"
    os.environ["LOCUS_MEMORY_STORE"] = "sqlite"
    os.environ["LOCUS_MEMORY_SQLITE_PATH"] = str(memory_db)
    ensure_memory_store(Path(cfg.app_home), progress=log, path=memory_db)
    # The agent browser (computer use) launches the Chromium first-run installs
    # under <app_home>/playwright, never a system or user browser.
    from .desktop_firstrun import playwright_browsers_dir

    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(playwright_browsers_dir(Path(cfg.app_home)))
    )

    # FAST PATH: start only the frontend synchronously so the window appears in
    # seconds. Everything heavy (DB init, Ollama serve + the multi-GB model pull,
    # first-run binary fetch) runs in the background so it never blocks the UI.
    _install_shutdown_hooks()
    fast = NativePlan([s for s in plan.services if s.name == "frontend"], plan.env, [])
    fast_supervisor = NativeSupervisor(fast, log=log)
    fast_supervisor.start_all()
    _LIVE_SUPERVISORS.append(fast_supervisor)

    from .desktop_firstrun import (
        ensure_agent_toolchain,
        ensure_playwright_chromium,
        ensure_sidecars,
    )

    deferred_supervisors: list = []

    def _bring_up_infra() -> None:
        # Fetch any missing sidecar binaries, then start DB/model services + pull
        # the model (re-plan so newly-fetched binaries are picked up).
        ensure_sidecars(writable_bin_dir(), model=None, progress=log)
        ensure_agent_toolchain(desktop_app_home(), progress=log)
        ensure_playwright_chromium(desktop_app_home(), progress=log)
        plan2 = build_native_plan(desktop_config(**overrides))
        deferred = NativePlan([s for s in plan2.services if s.name != "frontend"], plan2.env, [])
        sup = NativeSupervisor(deferred, log=log)
        deferred_supervisors.append(sup)
        _LIVE_SUPERVISORS.append(sup)
        sup.start_all()
        log("[firstrun] background services ready")

    threading.Thread(target=lambda: _safe(_bring_up_infra, log=log), daemon=True).start()
    # Never let the loop block the backend: a failure here is logged, not raised.
    try:
        resume_loop_after_start(log=log)
    except Exception as exc:  # noqa: BLE001
        log(f"[loop] could not resume the loop: {type(exc).__name__}: {exc}")

    log("[firstrun] starting backend…")
    try:
        import uvicorn
        from app.main import app as fastapi_app

        uvicorn.run(fastapi_app, host=cfg.bind_host, port=cfg.backend_port, log_level="warning")
    finally:
        fast_supervisor.stop_all()
        for sup in deferred_supervisors:
            sup.stop_all()
