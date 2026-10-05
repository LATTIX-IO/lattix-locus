"""macOS desktop control through the Accessibility (AX) API (LOCUS-341).

**Unverified on a real Mac.** This backend was written against Apple's AX API
as exposed by PyObjC (``pyobjc-framework-ApplicationServices``, MIT) and is
exercised only with a fake API in ``tests/unit/test_computer_use_desktop.py``.
It must pass the same scenario suite on macOS before macOS computer use is
claimed (12 §9).

* perception -- ``AXUIElementCreateApplication(pid)`` and ``AXChildren``;
  ``AXSecureTextField`` (subrole) marks password fields, whose value is never read;
* click → ``AXPress``; type → set ``AXValue``; fallbacks are ``CGEvent``
  mouse / Unicode keyboard events, sent only while the target app is frontmost;
* app identity is the bundle identifier (e.g. ``com.apple.textedit``).

The process needs the macOS Accessibility TCC grant; without it
:meth:`AxBackend.available` reports why. Per 12 §4 the permission belongs to a
signed native helper eventually; this adapter is the interface it will sit behind.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from types import SimpleNamespace
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

AX_ERROR_SUCCESS = 0

# Virtual key codes (HIToolbox/Events.h) for chord names.
_MAC_KEYS: dict[str, int] = {
    "enter": 36,
    "return": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "esc": 53,
    "escape": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "home": 115,
    "end": 119,
    "pageup": 116,
    "pagedown": 121,
    **{
        chr(c): code
        for c, code in zip(
            range(ord("a"), ord("z") + 1),
            (
                0,
                11,
                8,
                2,
                14,
                3,
                5,
                4,
                34,
                38,
                40,
                37,
                46,
                45,
                31,
                35,
                12,
                15,
                1,
                17,
                32,
                9,
                13,
                7,
                16,
                6,
            ),
            strict=True,
        )
    },
}
_MAC_MODIFIERS: dict[str, int] = {
    # CGEventFlags masks.
    "shift": 1 << 17,
    "ctrl": 1 << 18,
    "control": 1 << 18,
    "alt": 1 << 19,
    "option": 1 << 19,
    "cmd": 1 << 20,
    "command": 1 << 20,
    "meta": 1 << 20,
}


def load_ax_api() -> Any:
    """The PyObjC entry points this backend uses (raises DesktopUnavailable off macOS)."""
    if sys.platform != "darwin":
        raise DesktopUnavailable("the Accessibility API is macOS-only")
    try:
        import ApplicationServices as appservices  # type: ignore[import-not-found]
        import Quartz  # type: ignore[import-not-found]
        from AppKit import NSWorkspace  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DesktopUnavailable("pyobjc-framework-ApplicationServices is not installed") from exc
    return SimpleNamespace(
        is_trusted=appservices.AXIsProcessTrusted,
        create_application=appservices.AXUIElementCreateApplication,
        copy_attribute=appservices.AXUIElementCopyAttributeValue,
        set_attribute=appservices.AXUIElementSetAttributeValue,
        perform_action=appservices.AXUIElementPerformAction,
        get_pid=appservices.AXUIElementGetPid,
        running_apps=lambda: NSWorkspace.sharedWorkspace().runningApplications(),
        frontmost_pid=lambda: int(
            NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
        ),
        key_event=lambda code, down: Quartz.CGEventCreateKeyboardEvent(None, code, down),
        set_flags=Quartz.CGEventSetFlags,
        set_unicode=Quartz.CGEventKeyboardSetUnicodeString,
        mouse_event=lambda kind, x, y: Quartz.CGEventCreateMouseEvent(
            None, kind, (x, y), Quartz.kCGMouseButtonLeft
        ),
        mouse_down=Quartz.kCGEventLeftMouseDown,
        mouse_up=Quartz.kCGEventLeftMouseUp,
        post=lambda event: Quartz.CGEventPost(Quartz.kCGHIDEventTap, event),
    )


class AxBackend:
    """:class:`~locus_runtime.computer_use.desktop.DesktopBackend` over macOS AX."""

    platform = "macos"

    def __init__(self, api: Any = None) -> None:
        self._api = api

    def _ax(self) -> Any:
        if self._api is None:
            self._api = load_ax_api()
        return self._api

    def available(self) -> tuple[bool, str]:
        try:
            api = self._ax()
        except DesktopUnavailable as exc:
            return False, str(exc)
        if not api.is_trusted():
            return False, "this process lacks the macOS Accessibility permission"
        return True, ""

    def _attr(self, element: Any, name: str) -> Any:
        err, value = self._ax().copy_attribute(element, name, None)
        return value if err == AX_ERROR_SUCCESS else None

    def describe(self, native: Any) -> NodeFacts:
        role = str(self._attr(native, "AXRole") or "")
        subrole = str(self._attr(native, "AXSubrole") or "")
        is_password = subrole == "AXSecureTextField"
        name = str(
            self._attr(native, "AXTitle")
            or self._attr(native, "AXDescription")
            or self._attr(native, "AXLabel")
            or ""
        )
        value = ""
        if not is_password:
            raw = self._attr(native, "AXValue")
            value = str(raw) if isinstance(raw, (str, int, float)) else ""
        frame = self._attr(native, "AXFrame")
        rect = (0, 0, 0, 0)
        try:
            x, y = int(frame.origin.x), int(frame.origin.y)
            rect = (x, y, x + int(frame.size.width), y + int(frame.size.height))
        except AttributeError:
            pass
        enabled = self._attr(native, "AXEnabled")
        err, pid = self._ax().get_pid(native, None)
        return NodeFacts(
            name=name[:300],
            role=role.removeprefix("AX") or "Unknown",
            automation_id=str(self._attr(native, "AXIdentifier") or ""),
            rect=rect,
            enabled=True if enabled is None else bool(enabled),
            is_password=is_password,
            value=value[:200],
            pid=int(pid) if err == AX_ERROR_SUCCESS else 0,
        )

    def find_window(self, *, app: str = "", title: str = "") -> DesktopWindow | None:
        want_app, want_title = app.strip().lower(), title.strip().lower()
        if not want_app and not want_title:
            return None
        for running in self._ax().running_apps():
            bundle = str(running.bundleIdentifier() or "").lower()
            if want_app and bundle != want_app:
                continue
            pid = int(running.processIdentifier())
            app_element = self._ax().create_application(pid)
            for window in self._attr(app_element, "AXWindows") or []:
                name = str(self._attr(window, "AXTitle") or "")
                if want_title and want_title not in name.lower():
                    continue
                return DesktopWindow(app=bundle, title=name, pid=pid, native=window)
        return None

    def walk(
        self, window: DesktopWindow, *, max_depth: int, max_nodes: int, token: CancelToken
    ) -> list[Node]:
        nodes: list[Node] = []
        stack: list[tuple[Any, int]] = [(window.native, 0)]
        while stack and len(nodes) < max_nodes:
            token.check()
            element, depth = stack.pop()
            try:
                facts = self.describe(element)
            except Exception:  # noqa: BLE001 - element vanished
                continue
            nodes.append(Node(replace(facts, depth=depth), element))
            if depth < max_depth:
                children = list(self._attr(element, "AXChildren") or [])[:max_nodes]
                stack.extend((child, depth + 1) for child in reversed(children))
        return nodes

    def focused(self, window: DesktopWindow) -> Node | None:
        app_element = self._ax().create_application(window.pid)
        element = self._attr(app_element, "AXFocusedUIElement")
        if element is None:
            return None
        return Node(self.describe(element), element)

    def invoke(self, native: Any) -> bool:
        return bool(self._ax().perform_action(native, "AXPress") == AX_ERROR_SUCCESS)

    def set_value(self, native: Any, text: str) -> bool:
        return bool(self._ax().set_attribute(native, "AXValue", text) == AX_ERROR_SUCCESS)

    def _require_front(self, window: DesktopWindow) -> None:
        deadline = time.monotonic() + 0.5
        while self._ax().frontmost_pid() != window.pid:
            if time.monotonic() > deadline:
                raise DesktopUnavailable("the target app is not frontmost; synthetic input refused")
            time.sleep(0.02)

    def synth_click(self, window: DesktopWindow, native: Any, token: CancelToken) -> None:
        self._require_front(window)
        left, top, right, bottom = self.describe(native).rect
        if right <= left or bottom <= top:
            raise DesktopUnavailable("element has no on-screen area")
        x, y = (left + right) / 2, (top + bottom) / 2
        api = self._ax()
        token.check()
        api.post(api.mouse_event(api.mouse_down, x, y))
        api.post(api.mouse_event(api.mouse_up, x, y))

    def synth_text(self, window: DesktopWindow, native: Any, text: str, token: CancelToken) -> None:
        self._require_front(window)
        self.synth_keys(window, "cmd+a", token)
        api = self._ax()
        for piece in chunks(text):
            for char in piece:
                token.check()
                if api.frontmost_pid() != window.pid:
                    raise DesktopUnavailable("focus left the target app; typing stopped")
                for down in (True, False):
                    event = api.key_event(0, down)
                    api.set_unicode(event, 1, char)
                    api.post(event)

    def synth_keys(self, window: DesktopWindow, chord: str, token: CancelToken) -> None:
        names = parse_chord(chord)
        flags = 0
        keys: list[int] = []
        for name in names:
            if name in _MAC_MODIFIERS:
                flags |= _MAC_MODIFIERS[name]
            elif name in _MAC_KEYS:
                keys.append(_MAC_KEYS[name])
            else:
                raise ValueError(f"unknown key {name!r}")
        if len(keys) != 1:
            raise ValueError("a chord needs exactly one non-modifier key")
        self._require_front(window)
        token.check()
        api = self._ax()
        for down in (True, False):
            event = api.key_event(keys[0], down)
            if flags:
                api.set_flags(event, flags)
            api.post(event)
