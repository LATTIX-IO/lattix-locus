"""Frozen backend-sidecar entrypoint for the Tauri desktop bundle.

This is the script PyInstaller/Nuitka packages and Tauri spawns as its
``externalBin`` sidecar. On launch it:
  1. makes the bundled binaries (Node, NATS, …) discoverable via PATH,
  2. runs first-run provisioning (fetch heavy sidecars + pull the model) — a
     no-op once everything is present,
  3. runs the native supervisor in the foreground (backend + frontend + sidecars
     + agents), then blocks until the shell terminates it.
"""

from __future__ import annotations

import json
import os
import sys


def _prepend_bundled_bin_to_path() -> None:
    """Make bundled binaries (Node, NATS, the backend) discoverable by name."""
    # Absolute import: as the PyInstaller entry script this module runs as
    # __main__ (no parent package), so relative imports would fail.
    from locus_tooling.desktop import bundled_bin_dir

    bundled = str(bundled_bin_dir())
    current = os.environ.get("PATH", "")
    if bundled and bundled not in current.split(os.pathsep):
        os.environ["PATH"] = bundled + os.pathsep + current if current else bundled


#: Modules the frozen bundle must contain; ``--self-check`` imports each one so a
#: missing PyInstaller hidden import fails the build instead of the installed app.
_SELF_CHECK_MODULES = (
    "app.main",
    "locus_runtime.gateway",
    "locus_runtime.policy_engine",
    "locus_runtime.grants",
    "locus_runtime.model_client",
    "locus_runtime.harness.verified_loop",
    # Agent runtime (LOCUS-361): the Deep Agents stack and the Locus extensions
    # (importing the runtime module loads deepagents, LangChain/LangGraph 1.x and
    # the SQLite checkpointer) must be bundled.
    "locus_runtime.harness.deep_agents.runtime",
    "deepagents",
    "langgraph.checkpoint.sqlite",
    "locus_tooling.native_secrets",
    "biscuit_auth",
    "keyring",
    "playwright.sync_api",
    "locus_runtime.computer_use.browser",
    "locus_runtime.computer_use.wiring",
    # Update channels (LOCUS-349): readiness/hold, version handshake, loop resume.
    "locus_tooling.desktop_update",
    "locus_runtime.computer_use.user_browser.driver",
    "locus_runtime.computer_use.user_browser.native_host",
    "tldextract",
    # Observability (LOCUS-375): OTel SDK + OTLP/HTTP exporter must be bundled.
    "locus_runtime.telemetry.setup",
    "opentelemetry.sdk.trace",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
)


def self_check() -> int:
    """Import every critical module and report the keychain backend; 0 = healthy."""
    import importlib
    import json

    report: dict[str, object] = {"modules": {}, "ok": True}
    modules: dict[str, str] = {}
    for name in _SELF_CHECK_MODULES:
        try:
            importlib.import_module(name)
            modules[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - report every failure, then fail
            modules[name] = f"error: {type(exc).__name__}: {exc}"
            report["ok"] = False
    report["modules"] = modules
    # Every agent runtime must build in the bundle (LOCUS-361): deep-agents also
    # checks the bundled package metadata against its audited pins.
    try:
        from locus_runtime.harness.runtimes import RUNTIME_NAMES, create_runtime

        runtimes: dict[str, str] = {}
        for runtime_name in RUNTIME_NAMES:
            try:
                create_runtime(runtime_name)
                runtimes[runtime_name] = "ok"
            except Exception as exc:  # noqa: BLE001 - report, then fail
                runtimes[runtime_name] = f"error: {type(exc).__name__}: {exc}"
                report["ok"] = False
        report["agent_runtimes"] = runtimes
    except Exception as exc:  # noqa: BLE001
        report["agent_runtimes"] = f"error: {type(exc).__name__}: {exc}"
        report["ok"] = False
    try:
        import keyring

        report["keyring_backend"] = type(keyring.get_keyring()).__name__
    except Exception as exc:  # noqa: BLE001
        report["keyring_backend"] = f"error: {type(exc).__name__}"
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def loop_serve(argv: list[str]) -> int:
    """``--loop-serve <repo>``: ``lattix loop serve`` on the installed code.

    The desktop supervisor starts this after a restart when loop autostart is on
    and the kill switch is off (LOCUS-349); the loop itself re-checks the kill
    switch before every tick and step.
    """
    if len(argv) != 1 or not argv[0].strip():
        print("usage: locus-backend --loop-serve <repo-path>", file=sys.stderr)
        return 2
    from locus_runtime.loop_runner import build_runner

    try:
        result = build_runner(argv[0]).serve()
    except KeyboardInterrupt:
        return 0
    print(json.dumps(result.to_dict(), default=str))
    return 0


def _looks_like_native_messaging(argv: list[str]) -> bool:
    args = argv[1:]
    if "--native-messaging-host" in args or any(
        str(a).startswith("chrome-extension://") for a in args
    ):
        return True
    # Firefox passes the add-on ID (pairing.FIREFOX_EXTENSION_ID; kept literal
    # so ordinary launches import nothing; a test pins the two together).
    return "locus-browser@lattix.io" in args


def main() -> int | None:
    # LangSmith tracing is never on (LOCUS-361): scrub its environment switches
    # first, so no child or in-process LangChain import can turn it on.
    from locus_runtime.hosted_tracing import force_langsmith_off

    force_langsmith_off()
    # A browser launched us as the Locus native-messaging host (LOCUS-350): the
    # host manifest points at this binary, and browsers pass the caller's
    # extension origin as an argument. Dispatch before anything prints to stdout;
    # the cheap argv check keeps normal launches from importing the host.
    if _looks_like_native_messaging(sys.argv):
        from locus_runtime.computer_use.user_browser.native_host import (
            is_native_messaging_invocation,
        )
        from locus_runtime.computer_use.user_browser.native_host import main as host_main

        if is_native_messaging_invocation(sys.argv):
            return host_main(sys.argv)
        return 2  # an unpinned extension asked for the host: refuse
    if "--self-check" in sys.argv[1:]:
        return self_check()
    if len(sys.argv) > 1 and sys.argv[1] == "--loop-serve":
        return loop_serve(sys.argv[2:])

    # Out-of-band confirmation secret from the Tauri shell (LOCUS-350): read once
    # from stdin, kept in memory, stdin detached so no child inherits the pipe.
    from locus_tooling.shell_confirmation import receive_from_stdin

    receive_from_stdin()
    from locus_tooling.desktop import run_desktop_supervisor

    _prepend_bundled_bin_to_path()
    # run_desktop_supervisor starts sidecars + frontend, runs first-run
    # provisioning in the background, and serves the backend in-process.
    run_desktop_supervisor()


if __name__ == "__main__":
    sys.exit(main())
