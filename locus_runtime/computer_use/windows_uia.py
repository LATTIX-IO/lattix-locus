"""Windows desktop control through UI Automation (LOCUS-341).

Calls ``UIAutomationCore`` COM directly with ``comtypes`` (MIT). P28: the
popular wrappers ``uiautomation`` and ``pywinauto`` are not used (maintainer
provenance). The tree comes from the Control view walker, depth- and
size-capped; actions use UIA patterns first:

* click → ``InvokePattern.Invoke``, else ``TogglePattern.Toggle``, else
  ``SelectionItemPattern.Select``, else (fallback) ``SetFocus`` + ``SendInput``
  mouse click at the element's *current* centre;
* type → ``ValuePattern.SetValue`` (replaces the value), else ``SetFocus`` +
  ``Ctrl+A`` + ``SendInput`` Unicode characters;
* key → ``SendInput`` virtual keys.

Synthetic input is sent only while the target window's process owns the
foreground window, checked before every character / key, so typing can never
land in another app (e.g. after the user switches windows). Password fields
report ``IsPassword`` and their value is never read.

Verified on Windows 11 against Notepad (tests/policy/test_computer_use_desktop_opa.py).
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
import threading
import time
from typing import Any

from locus_runtime.computer_use.controller import CancelToken
from locus_runtime.computer_use.desktop import (
    DesktopUnavailable,
    DesktopWindow,
    Node,
    NodeFacts,
    chunks,
    parse_chord,
)

# UIA constants (UIAutomationClient.h).
TREE_SCOPE_CHILDREN = 2
UIA_INVOKE_PATTERN = 10000
UIA_SELECTION_ITEM_PATTERN = 10010
UIA_TOGGLE_PATTERN = 10015
UIA_VALUE_PATTERN = 10002

CONTROL_TYPES: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
    50039: "SemanticZoom",
    50040: "AppBar",
}

# Virtual-key codes for chord names (winuser.h).
_VK: dict[str, int] = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    "meta": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "space": 0x20,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
_EXTENDED_KEYS = frozenset({0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B})

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_KEY_INTERVAL = 0.004


def _windll() -> Any:
    """``ctypes.windll`` (Windows only; typed loosely so this module type-checks anywhere)."""
    return ctypes.windll  # type: ignore[attr-defined,unused-ignore]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def process_image_name(pid: int) -> str:
    """Lower-case executable name of ``pid`` (empty when it cannot be read)."""
    if sys.platform != "win32" or pid <= 0:
        return ""
    kernel32 = _windll().kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value).lower()
    finally:
        kernel32.CloseHandle(handle)


class UiaBackend:
    """:class:`~locus_runtime.computer_use.desktop.DesktopBackend` over Windows UIA."""

    platform = "windows"

    def __init__(self) -> None:
        self._local = threading.local()

    # -- setup -----------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "not Windows"
        try:
            self._automation()
        except DesktopUnavailable as exc:
            return False, str(exc)
        user32 = _windll().user32
        if not user32.GetForegroundWindow() and not user32.GetDesktopWindow():
            return False, "no interactive desktop session"
        return True, ""

    def _automation(self) -> tuple[Any, Any]:
        cached: tuple[Any, Any] | None = getattr(self._local, "uia", None)
        if cached is not None:
            return cached
        if sys.platform != "win32":
            raise DesktopUnavailable("UI Automation is Windows-only")
        try:
            comtypes = importlib.import_module("comtypes")
            importlib.import_module("comtypes.client")

            try:
                comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
            except OSError:
                pass  # already initialised on this thread (possibly MTA): fine for UIA
            comtypes.client.GetModule("UIAutomationCore.dll")
            uia_module = importlib.import_module("comtypes.gen.UIAutomationClient")

            automation = comtypes.client.CreateObject(
                uia_module.CUIAutomation._reg_clsid_, interface=uia_module.IUIAutomation
            )
        except ImportError as exc:
            raise DesktopUnavailable("comtypes is not installed") from exc
        except Exception as exc:  # noqa: BLE001 - COM / session failure
            raise DesktopUnavailable(f"UI Automation unavailable: {exc}") from exc
        self._local.uia = (automation, uia_module)
        return automation, uia_module

    # -- perception ------------------------------------------------------------
    def describe(self, native: Any) -> NodeFacts:
        rect = native.CurrentBoundingRectangle
        is_password = bool(native.CurrentIsPassword)
        value = ""
        if not is_password:
            value = self._value_of(native)
        return NodeFacts(
            name=str(native.CurrentName or ""),
            role=CONTROL_TYPES.get(int(native.CurrentControlType), "Unknown"),
            automation_id=str(native.CurrentAutomationId or ""),
            class_name=str(native.CurrentClassName or ""),
            rect=(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
            enabled=bool(native.CurrentIsEnabled),
            is_password=is_password,
            value=value[:200],
            pid=int(native.CurrentProcessId),
        )

    def _value_of(self, native: Any) -> str:
        _, uia = self._automation()
        try:
            pattern = native.GetCurrentPattern(UIA_VALUE_PATTERN)
            if not pattern:
                return ""
            return str(pattern.QueryInterface(uia.IUIAutomationValuePattern).CurrentValue or "")
        except Exception:  # noqa: BLE001 - no value
            return ""

    def find_window(self, *, app: str = "", title: str = "") -> DesktopWindow | None:
        automation, _ = self._automation()
        root = automation.GetRootElement()
        found = root.FindAll(TREE_SCOPE_CHILDREN, automation.CreateTrueCondition())
        want_app = app.strip().lower()
        want_title = title.strip().lower()
        if not want_app and not want_title:
            return None
        for index in range(found.Length):
            element = found.GetElement(index)
            try:
                name = str(element.CurrentName or "")
                pid = int(element.CurrentProcessId)
            except Exception:  # noqa: BLE001 - window closed meanwhile
                continue
            exe = process_image_name(pid)
            if want_app and exe != want_app:
                continue
            if want_title and want_title not in name.lower():
                continue
            return DesktopWindow(app=exe, title=name, pid=pid, native=element)
        return None

    def walk(
        self, window: DesktopWindow, *, max_depth: int, max_nodes: int, token: CancelToken
    ) -> list[Node]:
        automation, _ = self._automation()
        walker = automation.ControlViewWalker
        nodes: list[Node] = []
        stack: list[tuple[Any, int]] = [(window.native, 0)]
        while stack and len(nodes) < max_nodes:
            token.check()
            element, depth = stack.pop()
            try:
                facts = self.describe(element)
            except Exception:  # noqa: BLE001 - element vanished mid-walk
                continue
            nodes.append(Node(_with_depth(facts, depth), element))
            if depth >= max_depth:
                continue
            children: list[Any] = []
            try:
                child = walker.GetFirstChildElement(element)
                while child and len(children) < max_nodes:
                    children.append(child)
                    child = walker.GetNextSiblingElement(child)
            except Exception:  # noqa: BLE001 - subtree unavailable
                children = []
            stack.extend((c, depth + 1) for c in reversed(children))
        return nodes

    def focused(self, window: DesktopWindow) -> Node | None:
        automation, _ = self._automation()
        try:
            element = automation.GetFocusedElement()
            facts = self.describe(element)
        except Exception:  # noqa: BLE001
            return None
        if facts.pid != window.pid:
            return None
        return Node(facts, element)

    # -- semantic actions ------------------------------------------------------
    def invoke(self, native: Any) -> bool:
        _, uia = self._automation()
        for pattern_id, interface, method in (
            (UIA_INVOKE_PATTERN, uia.IUIAutomationInvokePattern, "Invoke"),
            (UIA_TOGGLE_PATTERN, uia.IUIAutomationTogglePattern, "Toggle"),
            (UIA_SELECTION_ITEM_PATTERN, uia.IUIAutomationSelectionItemPattern, "Select"),
        ):
            try:
                pattern = native.GetCurrentPattern(pattern_id)
            except Exception:  # noqa: BLE001
                continue
            if pattern:
                getattr(pattern.QueryInterface(interface), method)()
                return True
        return False

    def set_value(self, native: Any, text: str) -> bool:
        _, uia = self._automation()
        try:
            pattern = native.GetCurrentPattern(UIA_VALUE_PATTERN)
        except Exception:  # noqa: BLE001
            return False
        if not pattern:
            return False
        value = pattern.QueryInterface(uia.IUIAutomationValuePattern)
        if value.CurrentIsReadOnly:
            return False
        value.SetValue(text)
        return True

    # -- synthetic input (fallback) ---------------------------------------------
    def _foreground_pid(self) -> int:
        user32 = _windll().user32
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _ensure_foreground(self, window: DesktopWindow, native: Any) -> None:
        try:
            native.SetFocus()
        except Exception:  # noqa: BLE001 - focus refused; checked below
            pass
        deadline = time.monotonic() + 0.5
        while self._foreground_pid() != window.pid:
            if time.monotonic() > deadline:
                raise DesktopUnavailable(
                    "the target window is not in the foreground; synthetic input refused"
                )
            time.sleep(0.02)

    def _send(self, inputs: list[_INPUT]) -> None:
        array = (_INPUT * len(inputs))(*inputs)
        sent = _windll().user32.SendInput(
            len(inputs), array, ctypes.sizeof(_INPUT)
        )
        if sent != len(inputs):
            raise DesktopUnavailable("SendInput was blocked (UIPI or secure desktop)")

    @staticmethod
    def _key(vk: int, up: bool) -> _INPUT:
        flags = (_KEYEVENTF_KEYUP if up else 0) | (
            _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_KEYS else 0
        )
        return _INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, flags, 0, 0)))

    @staticmethod
    def _unicode(char: str, up: bool) -> _INPUT:
        flags = _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if up else 0)
        return _INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(0, ord(char), flags, 0, 0)))

    def _vk_for(self, name: str) -> int:
        if name in _VK:
            return _VK[name]
        if len(name) == 1:
            scan = _windll().user32.VkKeyScanW(ord(name))
            if scan != -1:
                return int(scan) & 0xFF
        raise ValueError(f"unknown key {name!r}")

    def synth_click(self, window: DesktopWindow, native: Any, token: CancelToken) -> None:
        self._ensure_foreground(window, native)
        facts = self.describe(native)  # current position, never a stale frame
        left, top, right, bottom = facts.rect
        if right <= left or bottom <= top:
            raise DesktopUnavailable("element has no on-screen area")
        x, y = (left + right) // 2, (top + bottom) // 2
        automation, uia = self._automation()
        hit = automation.ElementFromPoint(uia.tagPOINT(x, y))
        if int(hit.CurrentProcessId) != window.pid:
            raise DesktopUnavailable("another window covers the element; click refused")
        token.check()
        _windll().user32.SetCursorPos(x, y)
        down = _INPUT(
            _INPUT_MOUSE, _INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN, 0, 0))
        )
        up = _INPUT(_INPUT_MOUSE, _INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP, 0, 0)))
        self._send([down, up])

    def synth_text(self, window: DesktopWindow, native: Any, text: str, token: CancelToken) -> None:
        self._ensure_foreground(window, native)
        self.synth_keys(window, "ctrl+a", token)
        if not text:
            token.check()
            self._send([self._key(0x2E, False), self._key(0x2E, True)])  # Delete
            return
        for piece in chunks(text):
            for char in piece:
                token.check()
                if self._foreground_pid() != window.pid:
                    raise DesktopUnavailable("focus left the target window; typing stopped")
                self._send([self._unicode(char, False), self._unicode(char, True)])
            time.sleep(_KEY_INTERVAL)

    def synth_keys(self, window: DesktopWindow, chord: str, token: CancelToken) -> None:
        names = parse_chord(chord)
        if not names:
            raise ValueError("empty key chord")
        codes = [self._vk_for(name) for name in names]
        token.check()
        if self._foreground_pid() != window.pid:
            self._ensure_foreground(window, window.native)
        token.check()
        self._send(
            [self._key(code, False) for code in codes]
            + [self._key(code, True) for code in reversed(codes)]
        )


def _with_depth(facts: NodeFacts, depth: int) -> NodeFacts:
    from dataclasses import replace

    return replace(facts, depth=depth)
