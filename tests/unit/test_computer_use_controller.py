"""Computer-use modes, panic latency and the desktop tool over a fake backend (LOCUS-341)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.computer_use import (
    ComputerUseCancelled,
    ComputerUseController,
    DesktopTool,
    UiResult,
)
from locus_runtime.computer_use.controller import CancelToken
from locus_runtime.computer_use.desktop import DesktopWindow, Node, NodeFacts
from locus_runtime.gateway import Capabilities, Gateway, GatewayAuditRecord
from tests.gateway_support import FakeEngine

PANIC_BUDGET_MS = 100.0


@dataclass
class FakeBackend:
    """DesktopBackend double: a window with a few nodes; records what it was asked."""

    app: str = "notepad.exe"
    invoke_ok: bool = True
    value_ok: bool = True
    char_delay: float = 0.0
    calls: list[tuple[str, Any]] = field(default_factory=list)
    platform: str = "fake"

    def __post_init__(self) -> None:
        self.facts = {
            "n-doc": NodeFacts(name="Text editor", role="Document", pid=42),
            "n-save": NodeFacts(name="Save", role="Button", pid=42),
            "n-send": NodeFacts(name="Send", role="Button", pid=42),
            "n-pass": NodeFacts(name="Password", role="Edit", is_password=True, pid=42),
            "n-alien": NodeFacts(name="Other", role="Button", pid=7),
        }
        self.window = DesktopWindow(app=self.app, title="Doc - Fake", pid=42, native="win")

    def available(self) -> tuple[bool, str]:
        return True, ""

    def find_window(self, *, app: str = "", title: str = "") -> DesktopWindow | None:
        return self.window if app.lower() in {"", self.app} else None

    def walk(
        self, window: DesktopWindow, *, max_depth: int, max_nodes: int, token: CancelToken
    ) -> list[Node]:
        token.check()
        return [Node(facts, native) for native, facts in self.facts.items()][:max_nodes]

    def describe(self, native: Any) -> NodeFacts:
        return self.facts[native]

    def focused(self, window: DesktopWindow) -> Node | None:
        return Node(self.facts["n-pass"], "n-pass") if self.focus_password else None

    focus_password: bool = False

    def invoke(self, native: Any) -> bool:
        self.calls.append(("invoke", native))
        return self.invoke_ok

    def set_value(self, native: Any, text: str) -> bool:
        self.calls.append(("set_value", (native, text)))
        return self.value_ok

    def synth_click(self, window: DesktopWindow, native: Any, token: CancelToken) -> None:
        token.check()
        self.calls.append(("synth_click", native))

    def synth_text(self, window: DesktopWindow, native: Any, text: str, token: CancelToken) -> None:
        typed = []
        for char in text:
            token.check()  # before every primitive
            token.wait(self.char_delay)
            typed.append(char)
        self.calls.append(("synth_text", "".join(typed)))

    def synth_keys(self, window: DesktopWindow, chord: str, token: CancelToken) -> None:
        token.check()
        self.calls.append(("synth_keys", chord))


def _session(apps: tuple[str, ...] = ("notepad.exe",)) -> tuple[gw.GatewaySession, list]:
    audit: list[GatewayAuditRecord] = []
    gateway = Gateway(FakeEngine(), audit.append)
    caps = Capabilities(allowed_tools=frozenset(gw.COMPUTER_USE_KINDS), allowed_apps=apps)
    session = gateway.open_session(run_id="run-d", principal="p", engine="e", capabilities=caps)
    return session, audit


def _tool(backend: FakeBackend, mode: str = "takeover", **kwargs: Any) -> DesktopTool:
    session, _ = _session()
    controller = ComputerUseController(mode)  # type: ignore[arg-type]
    tool = DesktopTool(session, backend, controller=controller, **kwargs)
    assert tool.observe(app=backend.app).ok
    return tool


def _ref(tool: DesktopTool, name: str) -> str:
    return next(ref for ref, facts in tool.refs().items() if facts.name == name)


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def test_observe_mode_reads_but_never_acts() -> None:
    backend = FakeBackend()
    tool = _tool(backend, mode="observe")
    result = tool.click(_ref(tool, "Save"))
    assert result.outcome == "blocked_by_mode"
    assert backend.calls == []


def test_assist_mode_proposes_without_driving_or_asking_the_gateway() -> None:
    backend = FakeBackend()
    tool = _tool(backend, mode="assist")
    result = tool.click(_ref(tool, "Send"))
    assert result.outcome == "proposed" and result.data["risk"] == "R3"
    assert result.decision is None and backend.calls == []


def test_takeover_mode_invokes_semantically() -> None:
    backend = FakeBackend()
    tool = _tool(backend)
    assert tool.click(_ref(tool, "Save")).ok
    assert backend.calls == [("invoke", "n-save")]


def test_synthetic_click_is_only_a_fallback() -> None:
    backend = FakeBackend(invoke_ok=False)
    tool = _tool(backend)
    assert tool.click(_ref(tool, "Save")).ok
    assert [name for name, _ in backend.calls] == ["invoke", "synth_click"]
    strict = _tool(FakeBackend(invoke_ok=False), allow_synthetic_input=False)
    assert strict.click(_ref(strict, "Save")).outcome == "error"


def test_send_button_asks_and_password_typing_is_denied() -> None:
    backend = FakeBackend()
    tool = _tool(backend)
    assert tool.click(_ref(tool, "Send")).outcome == "ask"
    denied = tool.type(_ref(tool, "Password"), "hunter2")
    assert denied.outcome == "denied" and denied.decision is not None
    assert denied.decision.risk == gw.RiskClass.R4
    assert not any(name == "set_value" for name, _ in backend.calls)


def test_keys_into_a_focused_password_field_are_denied() -> None:
    backend = FakeBackend()
    backend.focus_password = True
    tool = _tool(backend)
    assert tool.key("a").outcome == "denied"
    backend.focus_password = False
    assert tool.key("ctrl+s").ok
    assert tool.key("win+r").outcome == "ask"


def test_element_from_another_process_is_refused() -> None:
    backend = FakeBackend()
    tool = _tool(backend)
    assert tool.click(_ref(tool, "Other")).outcome == "error"
    assert backend.calls == []


def test_observe_output_is_wrapped_as_untrusted_and_capped() -> None:
    session, _ = _session()
    tool = DesktopTool(session, FakeBackend(), controller=ComputerUseController(), max_nodes=2)
    result = tool.observe(app="notepad.exe")
    assert "<<untrusted-content" in result.text and "<<end-untrusted-content" in result.text
    assert result.data["count"] == 2
    assert "hunter2" not in result.text


# --------------------------------------------------------------------------- #
# Panic
# --------------------------------------------------------------------------- #
def _run_in_thread(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def target() -> None:
        box["result"] = fn()
        box["done_at"] = time.perf_counter()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def test_panic_stops_in_flight_typing_within_100ms_and_latches() -> None:
    latencies: list[float] = []
    for _ in range(5):
        backend = FakeBackend(value_ok=False, char_delay=0.005)
        tool = _tool(backend)
        controller = tool._controller  # noqa: SLF001 - the tool's controller
        ref = _ref(tool, "Text editor")
        thread, box = _run_in_thread(lambda tool=tool, ref=ref: tool.type(ref, "x" * 2000))
        time.sleep(0.1)  # typing is in flight (~10 s of keystrokes queued)
        assert controller.status()["inflight_actions"] == 1
        panicked_at = time.perf_counter()
        report = controller.panic("test")
        thread.join(2.0)
        assert not thread.is_alive()
        latency_ms = (box["done_at"] - panicked_at) * 1000
        latencies.append(latency_ms)
        result: UiResult = box["result"]
        assert result.outcome == "cancelled"
        assert report.cancelled_actions == 1 and report.latency_ms < PANIC_BUDGET_MS
        # New actions are rejected immediately until a human resets.
        start = time.perf_counter()
        assert tool.click(_ref(tool, "Save")).outcome == "cancelled"
        assert (time.perf_counter() - start) * 1000 < PANIC_BUDGET_MS
        assert not any(name == "invoke" for name, _ in backend.calls)
    assert max(latencies) <= PANIC_BUDGET_MS, latencies


def test_panic_is_idempotent_and_reset_drops_to_observe() -> None:
    controller = ComputerUseController("takeover")
    first = controller.panic("hotkey")
    second = controller.panic("api")
    assert not first.already_latched and second.already_latched
    assert second.source == "hotkey"
    with pytest.raises(ComputerUseCancelled):
        controller.begin("ui_click")
    controller.reset("alice")
    assert not controller.panicked and controller.mode == "observe"
    with controller.action("ui_observe") as token:
        token.check()


def test_panic_listeners_run_and_cannot_block_the_panic() -> None:
    controller = ComputerUseController("takeover")
    seen: list[str] = []

    def boom() -> None:
        raise RuntimeError("listener failed")

    controller.on_panic(boom)
    controller.on_panic(lambda: seen.append("closed"))
    report = controller.panic()
    assert seen == ["closed"] and report.latency_ms < PANIC_BUDGET_MS


def test_cancel_token_wait_wakes_on_cancel() -> None:
    token = CancelToken("ui_type")
    threading.Timer(0.02, token.cancel).start()
    start = time.perf_counter()
    with pytest.raises(ComputerUseCancelled):
        token.wait(5.0)
    assert time.perf_counter() - start < 1.0


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        ComputerUseController("god-mode")  # type: ignore[arg-type]
