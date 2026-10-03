"""LOCUS-346: runs whose envelope lists computer-use tools get the computer-use toolset."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from locus_runtime.computer_use import controller as cu
from locus_runtime.computer_use.desktop import DesktopUnavailable
from locus_runtime.computer_use.toolset import ComputerUseToolset
from locus_runtime.computer_use.wiring import (
    build_run_toolset,
    release_run_toolset,
    requested_computer_use_tools,
)
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ScriptedChatClient
from locus_runtime.harness.run_envelope import DEFAULT_CODING_TOOLS
from locus_runtime.harness.swe_agent import SweAgent, SweTask
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from tests.unit.test_computer_use_controller import FakeBackend


@pytest.fixture()
def controller_state() -> Iterator[None]:
    previous = cu._DEFAULT, cu._INSTALLED  # noqa: SLF001
    try:
        yield
    finally:
        cu._DEFAULT, cu._INSTALLED = previous  # noqa: SLF001


@pytest.fixture()
def installed_controller(controller_state: None) -> cu.ComputerUseController:
    controller = cu.ComputerUseController()
    cu.install_controller(controller)
    return controller


class FakeBrowser:
    def __init__(self, session: Any, *, controller: Any, app_home: Any) -> None:
        self.session = session
        self.controller = controller
        self.detached = False

    def detach(self) -> None:
        self.detached = True


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(run_id="r1", executor=LocalDirectExecutor(tmp_path))


def test_requested_tools_picks_only_computer_use_names() -> None:
    assert requested_computer_use_tools([*DEFAULT_CODING_TOOLS]) == frozenset()
    assert requested_computer_use_tools(["execute_bash", "browser_read", "desktop_click"]) == {
        "browser_read",
        "desktop_click",
    }


def test_coding_only_envelope_keeps_the_plain_toolset(
    tmp_path: Path, installed_controller: cu.ComputerUseController
) -> None:
    toolset = build_run_toolset(tools=DEFAULT_CODING_TOOLS, workspace=_workspace(tmp_path))
    assert type(toolset) is CodingToolset


def test_computer_use_tools_get_the_toolset_on_the_installed_controller(
    tmp_path: Path, installed_controller: cu.ComputerUseController
) -> None:
    session = object()
    toolset = build_run_toolset(
        tools=[*DEFAULT_CODING_TOOLS, "browser_navigate", "browser_read", "desktop_observe"],
        workspace=_workspace(tmp_path),
        session=session,
        browser_factory=FakeBrowser,
        desktop_backend_factory=FakeBackend,
        edit_format="whole",
    )
    assert isinstance(toolset, ComputerUseToolset)
    assert toolset.edit_format == "whole"
    assert isinstance(toolset.browser, FakeBrowser)
    assert toolset.browser.session is session
    assert toolset.browser.controller is installed_controller
    assert toolset.desktop is not None
    names = {schema["function"]["name"] for schema in toolset.schemas()}
    assert {"browser_navigate", "desktop_observe", "execute_bash"} <= names

    release_run_toolset(toolset)
    assert toolset.browser.detached


def test_browser_only_run_has_no_desktop_and_unavailable_desktop_is_dropped(
    tmp_path: Path, installed_controller: cu.ComputerUseController
) -> None:
    browser_only = build_run_toolset(
        tools=["browser_read"], workspace=_workspace(tmp_path), browser_factory=FakeBrowser
    )
    assert isinstance(browser_only, ComputerUseToolset) and browser_only.desktop is None

    def no_desktop() -> Any:
        raise DesktopUnavailable("no accessibility backend")

    desktop_only = build_run_toolset(
        tools=["desktop_click"], workspace=_workspace(tmp_path), desktop_backend_factory=no_desktop
    )
    assert isinstance(desktop_only, ComputerUseToolset)
    assert desktop_only.browser is None and desktop_only.desktop is None


def test_no_installed_controller_means_no_computer_use_tools(
    tmp_path: Path, controller_state: None
) -> None:
    cu.install_controller(None)
    toolset = build_run_toolset(
        tools=["browser_read"], workspace=_workspace(tmp_path), browser_factory=FakeBrowser
    )
    assert type(toolset) is CodingToolset


def test_swe_agent_builds_the_computer_use_toolset_and_envelope(
    tmp_path: Path, installed_controller: cu.ComputerUseController, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.computer_use import wiring

    built: list[Any] = []
    real = wiring.build_run_toolset

    def spy(**kwargs: Any) -> Any:
        toolset = real(**kwargs, browser_factory=FakeBrowser)
        built.append((kwargs, toolset))
        return toolset

    monkeypatch.setattr(wiring, "build_run_toolset", spy)
    executor = LocalDirectExecutor(tmp_path)
    task = SweTask(
        instance_id="t1", problem_statement="check the page", executor=executor, test_command="true"
    )
    agent = SweAgent(
        client=ScriptedChatClient(responses=[]),
        computer_use_tools=("browser_read",),
        computer_use_apps=("notepad.exe",),
    )
    envelope = agent._envelope_for(task)  # noqa: SLF001
    assert envelope is not None
    assert "browser_read" in envelope.capabilities.tools
    assert envelope.capabilities.apps == ("notepad.exe",)
    caps = envelope.gateway_capabilities()
    assert {"browser_read", "network_egress"} <= caps.allowed_tools

    workspace = Workspace(run_id="t1", executor=executor)
    toolset = agent._build_toolset(task, workspace, agent._resolve_profile(), envelope)  # noqa: SLF001
    assert isinstance(toolset, ComputerUseToolset) and built


def test_swe_agent_without_computer_use_keeps_coding_toolset(tmp_path: Path) -> None:
    executor = LocalDirectExecutor(tmp_path)
    task = SweTask(instance_id="t2", problem_statement="fix", executor=executor)
    agent = SweAgent(client=ScriptedChatClient(responses=[]))
    workspace = Workspace(run_id="t2", executor=executor)
    toolset = agent._build_toolset(task, workspace, agent._resolve_profile(), None)  # noqa: SLF001
    assert type(toolset) is CodingToolset
