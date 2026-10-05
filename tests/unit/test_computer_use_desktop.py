"""macOS AX backend (fake API), the model toolset and envelope wiring (LOCUS-341).

The macOS backend is UNVERIFIED on a real Mac: these tests drive it through a
fake PyObjC surface only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.computer_use import ComputerUseController, DesktopTool
from locus_runtime.computer_use.controller import CancelToken
from locus_runtime.computer_use.desktop import DesktopUnavailable, DesktopWindow, parse_chord
from locus_runtime.computer_use.macos_ax import AxBackend
from locus_runtime.computer_use.operations import computer_use_operations
from locus_runtime.computer_use.toolset import ComputerUseToolset, computer_use_schemas
from locus_runtime.gateway import Capabilities, Gateway
from locus_runtime.harness.run_envelope import (
    AcceptanceCriterion,
    EnvelopeCapabilities,
    RunEnvelope,
)
from tests.gateway_support import FakeEngine
from tests.unit.test_computer_use_controller import FakeBackend


class _Frame:
    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.origin = SimpleNamespace(x=x, y=y)
        self.size = SimpleNamespace(width=w, height=h)


class FakeAx:
    """The subset of PyObjC the AX backend calls, over an in-memory tree."""

    def __init__(self, *, front_pid: int = 77, trusted: bool = True) -> None:
        self.front_pid = front_pid
        self.trusted = trusted
        self.posted: list[Any] = []
        self.pressed: list[str] = []
        self.values: dict[str, str] = {}
        field = {
            "AXRole": "AXTextField",
            "AXSubrole": "AXSecureTextField",
            "AXTitle": "Password",
            "AXValue": "hunter2",
        }
        self.attrs: dict[str, dict[str, Any]] = {
            "app": {"AXWindows": ["win"], "AXFocusedUIElement": "pw"},
            "win": {"AXRole": "AXWindow", "AXTitle": "Notes", "AXChildren": ["btn", "pw", "txt"]},
            "btn": {"AXRole": "AXButton", "AXTitle": "Send", "AXFrame": _Frame(10, 10, 40, 20)},
            "pw": field,
            "txt": {"AXRole": "AXTextArea", "AXDescription": "Body", "AXValue": "hello"},
        }

    def is_trusted(self) -> bool:
        return self.trusted

    def create_application(self, pid: int) -> str:
        return "app"

    def copy_attribute(self, element: str, name: str, _: Any) -> tuple[int, Any]:
        value = self.attrs.get(element, {}).get(name)
        return (0, value) if value is not None else (-25212, None)

    def set_attribute(self, element: str, name: str, value: str) -> int:
        self.values[element] = value
        return 0

    def perform_action(self, element: str, action: str) -> int:
        self.pressed.append(element)
        return 0

    def get_pid(self, element: str, _: Any) -> tuple[int, int]:
        return 0, 77

    def running_apps(self) -> list[Any]:
        return [
            SimpleNamespace(
                bundleIdentifier=lambda: "com.apple.Notes", processIdentifier=lambda: 77
            )
        ]

    def frontmost_pid(self) -> int:
        return self.front_pid

    def key_event(self, code: int, down: bool) -> dict[str, Any]:
        return {"code": code, "down": down}

    def set_flags(self, event: dict[str, Any], flags: int) -> None:
        event["flags"] = flags

    def set_unicode(self, event: dict[str, Any], length: int, char: str) -> None:
        event["char"] = char

    mouse_down, mouse_up = "down", "up"

    def mouse_event(self, kind: str, x: float, y: float) -> tuple[str, float, float]:
        return kind, x, y

    def post(self, event: Any) -> None:
        self.posted.append(event)


def test_ax_backend_reads_tree_and_never_reads_secure_field_values() -> None:
    api = FakeAx()
    backend = AxBackend(api)
    assert backend.available() == (True, "")
    window = backend.find_window(app="com.apple.notes")
    assert window is not None and window.title == "Notes" and window.pid == 77
    nodes = backend.walk(window, max_depth=3, max_nodes=10, token=CancelToken("ui_observe"))
    by_name = {node.facts.name: node.facts for node in nodes}
    assert by_name["Send"].role == "Button" and by_name["Send"].rect == (10, 10, 50, 30)
    assert by_name["Password"].is_password and by_name["Password"].value == ""
    assert by_name["Body"].value == "hello"
    assert len(backend.walk(window, max_depth=0, max_nodes=10, token=CancelToken("x"))) == 1


def test_ax_backend_semantic_actions_and_untrusted_process() -> None:
    api = FakeAx()
    backend = AxBackend(api)
    assert backend.invoke("btn") and api.pressed == ["btn"]
    assert backend.set_value("txt", "new") and api.values["txt"] == "new"
    assert AxBackend(FakeAx(trusted=False)).available()[0] is False


def test_ax_synthetic_input_requires_the_target_frontmost() -> None:
    window = DesktopWindow(app="com.apple.notes", title="Notes", pid=77, native="win")
    api = FakeAx(front_pid=1)
    with pytest.raises(DesktopUnavailable):
        AxBackend(api).synth_keys(window, "cmd+s", CancelToken("ui_key"))
    assert api.posted == []
    api.front_pid = 77
    AxBackend(api).synth_keys(window, "cmd+s", CancelToken("ui_key"))
    assert api.posted[0] == {"code": 1, "down": True, "flags": 1 << 20}


def test_ax_backend_through_the_gated_tool_denies_secure_field_typing() -> None:
    gateway = Gateway(FakeEngine(), lambda _r: None)
    caps = Capabilities(
        allowed_tools=frozenset(gw.COMPUTER_USE_KINDS), allowed_apps=("com.apple.notes",)
    )
    session = gateway.open_session(run_id="r", principal="p", engine="e", capabilities=caps)
    tool = DesktopTool(session, AxBackend(FakeAx()), controller=ComputerUseController("takeover"))
    assert tool.observe(app="com.apple.notes").ok
    refs = {facts.name: ref for ref, facts in tool.refs().items()}
    assert tool.type(refs["Password"], "secret").outcome == "denied"
    assert tool.type(refs["Body"], "hi").ok
    assert tool.click(refs["Send"]).outcome == "ask"


def test_parse_chord() -> None:
    assert parse_chord("Ctrl + Shift+S") == ["ctrl", "shift", "s"]


# --------------------------------------------------------------------------- #
# Toolset and envelope
# --------------------------------------------------------------------------- #
class _Workspace:
    executor = None


def test_toolset_offers_computer_use_tools_and_records_gateway_blocks() -> None:
    gateway = Gateway(FakeEngine(), lambda _r: None)
    caps = Capabilities(
        allowed_tools=frozenset(gw.COMPUTER_USE_KINDS), allowed_apps=("notepad.exe",)
    )
    session = gateway.open_session(run_id="r", principal="p", engine="e", capabilities=caps)
    desktop = DesktopTool(session, FakeBackend(), controller=ComputerUseController("takeover"))
    toolset = ComputerUseToolset(workspace=_Workspace(), desktop=desktop)  # type: ignore[arg-type]
    names = {schema["function"]["name"] for schema in toolset.schemas()}
    assert {"desktop_observe", "desktop_click", "execute_bash", "submit"} <= names
    assert "browser_navigate" not in names  # no browser in this run
    out = toolset.dispatch("desktop_observe", {"app": "notepad.exe"})
    assert "<<untrusted-content" in out
    send_ref = next(ref for ref, facts in desktop.refs().items() if facts.name == "Send")
    asked = toolset.dispatch("desktop_click", {"ref": send_ref})
    assert asked.startswith("[permission required]")
    assert toolset.gateway_blocks[-1]["outcome"] == "ask"
    assert toolset.telemetry.gateway_asked == 1
    assert "browser" in toolset.dispatch("browser_read", {})


def test_schemas_are_well_formed() -> None:
    for schema in computer_use_schemas():
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"


def test_envelope_grants_computer_use_operations_and_apps() -> None:
    envelope = RunEnvelope(
        goal="fill the form",
        done_criteria=(AcceptanceCriterion(id="c1", text="the form is filled"),),
        capabilities=EnvelopeCapabilities(
            tools=("browser_navigate", "browser_act", "desktop_type"),
            egress_hosts=("127.0.0.1",),
            apps=("notepad.exe",),
        ),
    )
    caps = envelope.gateway_capabilities()
    assert {"browser_navigate", "browser_act", "network_egress", "ui_type"} <= caps.allowed_tools
    assert "ui_click" not in caps.allowed_tools
    assert caps.allowed_apps == ("notepad.exe",)
    assert RunEnvelope.from_dict(envelope.to_dict()).capabilities.apps == ("notepad.exe",)
    assert computer_use_operations(["execute_bash"]) == frozenset()
