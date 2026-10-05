"""Computer use v1 (LOCUS-341): agent browser, desktop control, modes and panic.

See ``docs/COMPUTER-USE.md`` and ``docs/product/12-computer-use.md``. Every
action is a gateway action (``ui_*`` / ``browser_*``) classified from what the
tool perceived, inside a :class:`ComputerUseController` action that a panic
cancels.
"""

from __future__ import annotations

import sys

from locus_runtime.computer_use.common import UiResult, wrap_untrusted
from locus_runtime.computer_use.controller import (
    MODES,
    CancelToken,
    ComputerUseCancelled,
    ComputerUseController,
    PanicReport,
    controller_installed,
    get_controller,
    install_controller,
)
from locus_runtime.computer_use.desktop import DesktopBackend, DesktopTool, DesktopUnavailable


def platform_desktop_backend() -> DesktopBackend:
    """The accessibility backend for this OS (UIA on Windows, AX on macOS)."""
    if sys.platform == "win32":
        from locus_runtime.computer_use.windows_uia import UiaBackend

        return UiaBackend()
    if sys.platform == "darwin":
        from locus_runtime.computer_use.macos_ax import AxBackend

        return AxBackend()
    raise DesktopUnavailable(f"no desktop accessibility backend for {sys.platform}")


__all__ = [
    "MODES",
    "CancelToken",
    "ComputerUseCancelled",
    "ComputerUseController",
    "DesktopBackend",
    "DesktopTool",
    "DesktopUnavailable",
    "PanicReport",
    "UiResult",
    "controller_installed",
    "get_controller",
    "install_controller",
    "platform_desktop_backend",
    "wrap_untrusted",
]
