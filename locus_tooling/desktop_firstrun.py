"""First-run sidecar provisioning for the desktop install.

On first launch the lean installer has only the small bundled binaries (Node,
NATS, the backend). This fetches the heavy sidecars (Postgres+pgvector, Neo4j +
JRE, Ollama) into the writable app-home bin dir and pulls the model, streaming
progress lines the Tauri splash drains. Subsequent launches find everything
present and skip the fetch.

IO (provision / model pull) is injectable so the flow is unit-tested offline.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import native_binaries as nb

ProgressFn = Callable[[str], None]
ProvisionFn = Callable[..., "nb.ProvisionReport"]
ModelPullFn = Callable[[str, str], int]
WhichFn = Callable[[list[str], "Path | None"], "str | None"]


def _default_progress(message: str) -> None:
    # Structured, prefixed line the Tauri shell can parse for the splash.
    print(f"[firstrun] {message}", flush=True)


def _default_model_pull(ollama_bin: str, model: str) -> int:
    return subprocess.run([ollama_bin, "pull", model], check=False).returncode


def _configured_opa_present() -> bool:
    configured = str(os.getenv("LOCUS_OPA_BIN") or "").strip()
    return bool(configured) and Path(configured).is_file()


def ensure_sidecars(
    bin_dir: Path,
    *,
    targets: list[str] | None = None,
    model: str | None = "gpt-oss:20b",
    progress: ProgressFn | None = None,
    provision: ProvisionFn | None = None,
    model_pull: ModelPullFn | None = None,
    which: WhichFn | None = None,
) -> "nb.ProvisionReport":
    """Provision missing sidecars into ``bin_dir`` then pull ``model`` if Ollama
    is available. Returns the provision report; never raises on a single failure
    (the supervisor degrades around whatever is still missing)."""
    progress = progress or _default_progress
    provision = provision or nb.provision
    model_pull = model_pull or _default_model_pull
    which = which or nb._which
    if targets is None:
        targets = list(nb.DEFAULT_TARGETS)
        # The desktop bundle ships OPA (LOCUS_OPA_BIN, set by desktop_main before
        # the backend starts); fetch it only when no usable binary is configured.
        if _configured_opa_present():
            targets.remove("opa")
    else:
        targets = list(targets)
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    progress(f"checking sidecars: {', '.join(targets)}")
    report = provision(targets, bin_dir)
    for name in report.installed:
        progress(f"installed {name}")
    for name in report.skipped:
        progress(f"present {name}")
    for name, detail in report.manual.items():
        progress(f"manual {name}: {detail}")
    for name, err in report.failed.items():
        progress(f"FAILED {name}: {err}")
    for warning in report.warnings:
        progress(warning)

    if model:
        ollama = which(["ollama"], bin_dir)
        if ollama:
            progress(f"pulling model {model} (first run — large download)")
            rc = model_pull(ollama, model)
            progress(f"model pull exit={rc}")
        else:
            progress("ollama not available; skipping model pull")
    progress("first-run provisioning complete")
    return report


ToolchainFn = Callable[..., "nb.ToolchainReport"]


def ensure_agent_toolchain(
    app_home: Path,
    *,
    progress: ProgressFn | None = None,
    provision_toolchain: ToolchainFn | None = None,
    os_name: str | None = None,
) -> "nb.ToolchainReport | None":
    """Windows only: fetch the agent toolchain (BusyBox sh + embeddable Python) that
    commands inside the AppContainer run with, and grant the container access to it.
    Never raises; a failure is reported and agent shell commands then fail with an
    actionable message until a later run fetches it."""
    progress = progress or _default_progress
    if (os_name or nb.current_platform()[0]) != "windows":
        return None
    provision_toolchain = provision_toolchain or nb.provision_toolchain
    progress("checking agent toolchain (busybox sh, python)")
    try:
        report = provision_toolchain(Path(app_home))
    except Exception as exc:  # noqa: BLE001 - first run must not crash the app
        progress(f"FAILED agent toolchain: {exc}")
        return None
    for name in report.installed:
        progress(f"installed {name}")
    for name in report.present:
        progress(f"present {name}")
    for name, err in report.failed.items():
        progress(f"FAILED {name}: {err}")
    return report


# --------------------------------------------------------------------------- #
# Playwright Chromium for the agent browser (LOCUS-346)
# --------------------------------------------------------------------------- #
#: The only browser the agent browser launches (computer use, LOCUS-341).
PLAYWRIGHT_BROWSER = "chromium"
#: ``<app_home>/playwright``: Playwright's browsers dir (``PLAYWRIGHT_BROWSERS_PATH``).
PLAYWRIGHT_DIRNAME = "playwright"

#: () -> (node executable, driver cli.js, driver env, playwright version)
PlaywrightDriverFn = Callable[[], "tuple[str, str, dict[str, str], str]"]
#: (argv, env) -> exit code
RunFn = Callable[[list[str], dict[str, str]], int]


def playwright_browsers_dir(app_home: Path) -> Path:
    return Path(app_home) / PLAYWRIGHT_DIRNAME


def _bundled_playwright_driver() -> tuple[str, str, dict[str, str], str]:
    """The Node driver shipped inside the ``playwright`` package (bundled by the
    PyInstaller spec), exactly what ``python -m playwright`` runs."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    from playwright._repo_version import version

    node, cli = compute_driver_executable()
    return str(node), str(cli), dict(get_driver_env()), str(version)


def playwright_install_command(
    app_home: Path, *, driver: PlaywrightDriverFn | None = None
) -> tuple[list[str], dict[str, str], str]:
    """``(argv, env, version)`` that installs the pinned Chromium into ``<app_home>/playwright``.

    Equivalent to ``PLAYWRIGHT_BROWSERS_PATH=<app_home>/playwright python -m
    playwright install chromium``, without needing a Python interpreter (the
    frozen backend has none). The driver resolves the Chromium build pinned to
    this ``playwright`` package version; nothing else is requested.
    """
    node, cli, env, version = (driver or _bundled_playwright_driver)()
    env = dict(env)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_browsers_dir(app_home))
    # Never redirect the download to a host from the environment.
    for name in ("PLAYWRIGHT_DOWNLOAD_HOST", "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST"):
        env.pop(name, None)
    return [node, cli, "install", PLAYWRIGHT_BROWSER], env, version


def _default_run(argv: list[str], env: dict[str, str]) -> int:
    return subprocess.run(argv, env=env, check=False).returncode


def _ensure_executable(path: str) -> None:
    # PyInstaller may unpack the bundled node driver without its exec bit (POSIX).
    if os.name == "nt":
        return
    try:
        mode = os.stat(path).st_mode
        if not mode & stat.S_IXUSR:
            os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def ensure_playwright_chromium(
    app_home: Path,
    *,
    progress: ProgressFn | None = None,
    driver: PlaywrightDriverFn | None = None,
    run: RunFn | None = None,
) -> bool:
    """First run: install Playwright's Chromium into ``<app_home>/playwright``.

    A marker per Playwright version makes later launches a no-op (a version
    bump re-runs the install, which Playwright itself keeps incremental).
    Never raises: without Chromium the browser tools report themselves
    unavailable and everything else keeps working.
    """
    progress = progress or _default_progress
    browsers = playwright_browsers_dir(app_home)
    try:
        argv, env, version = playwright_install_command(app_home, driver=driver)
    except Exception as exc:  # noqa: BLE001 - no driver bundled: browser tools stay off
        progress(f"FAILED playwright chromium: driver unavailable ({type(exc).__name__})")
        return False
    marker = browsers / f".locus-{PLAYWRIGHT_BROWSER}-{version}"
    if marker.exists():
        progress(f"present playwright {PLAYWRIGHT_BROWSER} ({version})")
        return True
    browsers.mkdir(parents=True, exist_ok=True)
    _ensure_executable(argv[0])
    progress(f"installing playwright {PLAYWRIGHT_BROWSER} ({version}) into {browsers}")
    try:
        rc = (run or _default_run)(argv, env)
    except Exception as exc:  # noqa: BLE001 - first run must not crash the app
        progress(f"FAILED playwright chromium: {exc}")
        return False
    if rc != 0:
        progress(f"FAILED playwright chromium: exit={rc}")
        return False
    marker.write_text(version, encoding="utf-8")
    progress(f"installed playwright {PLAYWRIGHT_BROWSER} ({version})")
    return True


def main(argv: list[str] | None = None) -> int:
    from .desktop import desktop_app_home, writable_bin_dir

    args = list(argv if argv is not None else sys.argv[1:])
    model = args[0] if args else "gpt-oss:20b"
    ensure_sidecars(writable_bin_dir(), model=model)
    ensure_agent_toolchain(desktop_app_home())
    ensure_playwright_chromium(desktop_app_home())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
