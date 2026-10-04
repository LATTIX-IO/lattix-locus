"""The one factory that picks browser drivers for a run (D-28; LOCUS-346 / LOCUS-350).

The run envelope's ``capabilities.tools`` decides which ``BrowserDriver``
profiles a run gets: ``browser_*`` tools → the isolated agent browser
(``"agent"``), ``user_browser_*`` tools → the principal's own browser
(``"user"``). A run can have both. Nothing else constructs drivers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from locus_runtime.computer_use.browser_contract import BrowserDriver
from locus_runtime.computer_use.controller import ComputerUseController
from locus_runtime.computer_use.operations import browser_profiles_for

DriverFactory = Callable[..., Any]


def _agent_factory() -> DriverFactory:
    from locus_runtime.computer_use.browser import AgentBrowser

    return AgentBrowser


def _user_factory() -> DriverFactory:
    from locus_runtime.computer_use.user_browser.driver import UserBrowserDriver

    return UserBrowserDriver


def build_browser_drivers(
    tools: Iterable[str] | None,
    *,
    session: Any,
    controller: ComputerUseController,
    app_home: Path | None = None,
    agent_factory: DriverFactory | None = None,
    user_factory: DriverFactory | None = None,
) -> dict[str, BrowserDriver]:
    """``{profile: driver}`` for the browser tools the envelope lists.

    Both drivers are bound to the run's gateway session and the shared
    controller. The agent browser is lazy (Chromium starts on first use); the
    user driver only talks to an already-paired extension.
    """
    profiles = browser_profiles_for(tools)
    drivers: dict[str, BrowserDriver] = {}
    if "agent" in profiles:
        factory = agent_factory or _agent_factory()
        drivers["agent"] = factory(session, controller=controller, app_home=app_home)
    if "user" in profiles:
        factory = user_factory or _user_factory()
        drivers["user"] = factory(session, controller=controller, app_home=app_home)
    return drivers


__all__ = ["build_browser_drivers"]
