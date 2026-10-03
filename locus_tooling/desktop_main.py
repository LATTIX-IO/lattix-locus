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
    "locus_tooling.native_secrets",
    "biscuit_auth",
    "keyring",
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
    try:
        import keyring

        report["keyring_backend"] = type(keyring.get_keyring()).__name__
    except Exception as exc:  # noqa: BLE001
        report["keyring_backend"] = f"error: {type(exc).__name__}"
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def main() -> int | None:
    if "--self-check" in sys.argv[1:]:
        return self_check()
    from locus_tooling.desktop import run_desktop_supervisor

    _prepend_bundled_bin_to_path()
    # run_desktop_supervisor starts sidecars + frontend, runs first-run
    # provisioning in the background, and serves the backend in-process.
    run_desktop_supervisor()


if __name__ == "__main__":
    sys.exit(main())
