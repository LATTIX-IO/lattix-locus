"""Run wiring for computer use (LOCUS-346).

The run loops (``SweAgent`` for backend code / team nodes, the Linear loop
runner) build their toolset through :func:`build_run_toolset`: a run whose
envelope lists computer-use tools gets a
:class:`~locus_runtime.computer_use.toolset.ComputerUseToolset` bound to the
run's gateway session and the process controller; every other run gets the
plain :class:`~locus_runtime.harness.tools.CodingToolset` it had before.

Computer-use tools are only offered when a controller is *installed* (the
backend does that at startup when the gateway is enforcing): without one the
run keeps the coding tools and the omission is logged, never silently
widened. :func:`release_run_toolset` closes the agent browser at the end of
the run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from locus_runtime.computer_use.controller import controller_installed, get_controller
from locus_runtime.computer_use.operations import BROWSER_TOOLS, DESKTOP_TOOLS
from locus_runtime.harness.tools import CodingToolset

logger = logging.getLogger(__name__)


def requested_computer_use_tools(tools: Iterable[str] | None) -> frozenset[str]:
    """The computer-use tool names among ``tools`` (an envelope's ``capabilities.tools``)."""
    names = {str(tool) for tool in tools or ()}
    return frozenset(names & (BROWSER_TOOLS | DESKTOP_TOOLS))


def _default_desktop_backend() -> Any:
    from locus_runtime.computer_use import platform_desktop_backend

    return platform_desktop_backend()


def build_run_toolset(
    *,
    tools: Iterable[str] | None,
    workspace: Any,
    session: Any = None,
    app_home: Path | None = None,
    browser_factory: Callable[..., Any] | None = None,
    desktop_backend_factory: Callable[[], Any] | None = None,
    **coding_kwargs: Any,
) -> CodingToolset:
    """The toolset for one run: computer-use tools when the envelope lists them.

    ``session`` is the run's :class:`~locus_runtime.gateway.GatewaySession`
    (the one whose capabilities came from the envelope); the browser and the
    desktop tool authorize every action on it. ``coding_kwargs`` go to the
    coding toolset unchanged.
    """
    wanted = requested_computer_use_tools(tools)
    if not wanted:
        return CodingToolset(workspace=workspace, **coding_kwargs)
    if not controller_installed():
        logger.warning(
            "computer_use.not_installed: run lists computer-use tools but no controller "
            "is installed; offering coding tools only",
            extra={"tools": sorted(wanted)},
        )
        return CodingToolset(workspace=workspace, **coding_kwargs)

    from locus_runtime.computer_use.desktop import DesktopTool, DesktopUnavailable
    from locus_runtime.computer_use.toolset import ComputerUseToolset

    controller = get_controller()
    browser = None
    if wanted & BROWSER_TOOLS:
        if browser_factory is None:
            from locus_runtime.computer_use.browser import AgentBrowser

            browser_factory = AgentBrowser
        # Lazy: Chromium only launches on the first browser call.
        browser = browser_factory(session, controller=controller, app_home=app_home)
    desktop = None
    if wanted & DESKTOP_TOOLS:
        try:
            backend = (desktop_backend_factory or _default_desktop_backend)()
            desktop = DesktopTool(session, backend, controller=controller)
        except DesktopUnavailable as exc:
            logger.warning("computer_use.desktop_unavailable: %s", exc)
    return ComputerUseToolset(
        workspace=workspace, browser=browser, desktop=desktop, **coding_kwargs
    )


def release_run_toolset(toolset: Any) -> None:
    """End-of-run cleanup: close the agent browser and drop its panic listener."""
    browser = getattr(toolset, "browser", None)
    detach = getattr(browser, "detach", None)
    if callable(detach):
        try:
            detach()
        except Exception:  # noqa: BLE001 - cleanup never fails the run
            logger.exception("computer_use.browser_release_error")


__all__ = ["build_run_toolset", "release_run_toolset", "requested_computer_use_tools"]
