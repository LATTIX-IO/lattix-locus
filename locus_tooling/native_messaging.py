"""Native-messaging host registration for the Locus browser extension (LOCUS-350).

Writes the host manifest that lets Chrome, Edge and Firefox start the Locus
native host (``io.lattix.locus_browser``) for the **pinned** Locus extension
only (``allowed_origins`` / ``allowed_extensions``), per user, never system-wide:

========  ===========================================================  ======================================
Browser   Windows (manifest under <app_home>, HKCU key points at it)    macOS / Linux (manifest file location)
========  ===========================================================  ======================================
chrome    HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<name>     ~/Library/Application Support/Google/Chrome/NativeMessagingHosts
                                                                       ~/.config/google-chrome/NativeMessagingHosts
edge      HKCU\\Software\\Microsoft\\Edge\\NativeMessagingHosts\\<name>    ~/Library/Application Support/Microsoft Edge/NativeMessagingHosts
                                                                       ~/.config/microsoft-edge/NativeMessagingHosts
firefox   HKCU\\Software\\Mozilla\\NativeMessagingHosts\\<name>            ~/Library/Application Support/Mozilla/NativeMessagingHosts
                                                                       ~/.mozilla/native-messaging-hosts
========  ===========================================================  ======================================

This only runs from the installer / first-run when the principal chooses to
connect a browser. On Windows the registry writer must be passed explicitly
(:class:`WinRegistry` in the installer); nothing here writes the registry by
default, and tests use a fake writer and temporary directories.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from locus_runtime.computer_use.user_browser.pairing import (
    CHROMIUM_ORIGIN,
    FIREFOX_EXTENSION_ID,
    NATIVE_HOST_NAME,
)

Browser = Literal["chrome", "edge", "firefox"]
BROWSERS: tuple[Browser, ...] = ("chrome", "edge", "firefox")
PlatformName = Literal["windows", "darwin", "linux"]

REGISTRY_ROOTS: dict[str, str] = {
    "chrome": "Software\\Google\\Chrome\\NativeMessagingHosts",
    "edge": "Software\\Microsoft\\Edge\\NativeMessagingHosts",
    "firefox": "Software\\Mozilla\\NativeMessagingHosts",
}
_MAC_DIRS = {
    "chrome": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
    "edge": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
    "firefox": "Library/Application Support/Mozilla/NativeMessagingHosts",
}
_LINUX_DIRS = {
    "chrome": ".config/google-chrome/NativeMessagingHosts",
    "edge": ".config/microsoft-edge/NativeMessagingHosts",
    "firefox": ".mozilla/native-messaging-hosts",
}


class RegistryWriter(Protocol):
    """HKCU writes (Windows). The real one is :class:`WinRegistry`."""

    def set_default_value(self, key_path: str, value: str) -> None: ...

    def delete_key(self, key_path: str) -> None: ...


class WinRegistry:  # pragma: no cover - only the installer uses the real registry
    """HKEY_CURRENT_USER writer. Per user; never HKLM."""

    def set_default_value(self, key_path: str, value: str) -> None:
        winreg: Any = importlib.import_module("winreg")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)

    def delete_key(self, key_path: str) -> None:
        winreg: Any = importlib.import_module("winreg")
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class Registration:
    browser: str
    manifest_path: Path
    registry_key: str = ""


def current_platform() -> PlatformName:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def host_manifest(browser: str, host_path: Path) -> dict[str, object]:
    """The manifest for ``browser``, restricted to the pinned Locus extension."""
    if browser not in BROWSERS:
        raise ValueError(f"unknown browser {browser!r}")
    manifest: dict[str, object] = {
        "name": NATIVE_HOST_NAME,
        "description": "Lattix Locus browser relay (talks only to the local Locus backend)",
        "path": str(host_path),
        "type": "stdio",
    }
    if browser == "firefox":
        manifest["allowed_extensions"] = [FIREFOX_EXTENSION_ID]
    else:
        manifest["allowed_origins"] = [CHROMIUM_ORIGIN]
    return manifest


def manifest_path(browser: str, *, platform: PlatformName, home: Path, app_home: Path) -> Path:
    if browser not in BROWSERS:
        raise ValueError(f"unknown browser {browser!r}")
    filename = f"{NATIVE_HOST_NAME}.json"
    if platform == "windows":
        return Path(app_home) / "native-messaging" / browser / filename
    dirs = _MAC_DIRS if platform == "darwin" else _LINUX_DIRS
    return Path(home) / dirs[browser] / filename


def register_host(
    host_path: Path,
    *,
    browsers: Iterable[str] = BROWSERS,
    platform: PlatformName | None = None,
    home: Path | None = None,
    app_home: Path,
    registry: RegistryWriter | None = None,
) -> list[Registration]:
    """Write per-user host manifests (and HKCU keys on Windows) for ``browsers``.

    ``host_path`` must be an absolute path to the host executable (the frozen
    ``locus-backend``). On Windows ``registry`` is required.
    """
    host = Path(host_path)
    if not host.is_absolute():
        raise ValueError("the native host path must be absolute")
    plat = platform or current_platform()
    if plat == "windows" and registry is None:
        raise ValueError("pass a registry writer to register on Windows")
    base_home = Path(home) if home is not None else Path.home()
    done: list[Registration] = []
    for browser in browsers:
        path = manifest_path(browser, platform=plat, home=base_home, app_home=app_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(host_manifest(browser, host), indent=2), encoding="utf-8")
        key = ""
        if plat == "windows":
            assert registry is not None
            key = f"{REGISTRY_ROOTS[browser]}\\{NATIVE_HOST_NAME}"
            registry.set_default_value(key, str(path))
        done.append(Registration(browser, path, key))
    return done


def unregister_host(
    *,
    browsers: Iterable[str] = BROWSERS,
    platform: PlatformName | None = None,
    home: Path | None = None,
    app_home: Path,
    registry: RegistryWriter | None = None,
) -> None:
    plat = platform or current_platform()
    if plat == "windows" and registry is None:
        raise ValueError("pass a registry writer to unregister on Windows")
    base_home = Path(home) if home is not None else Path.home()
    for browser in browsers:
        manifest_path(browser, platform=plat, home=base_home, app_home=app_home).unlink(
            missing_ok=True
        )
        if plat == "windows":
            assert registry is not None
            registry.delete_key(f"{REGISTRY_ROOTS[browser]}\\{NATIVE_HOST_NAME}")


__all__ = [
    "BROWSERS",
    "REGISTRY_ROOTS",
    "Registration",
    "RegistryWriter",
    "WinRegistry",
    "current_platform",
    "host_manifest",
    "manifest_path",
    "register_host",
    "unregister_host",
]
