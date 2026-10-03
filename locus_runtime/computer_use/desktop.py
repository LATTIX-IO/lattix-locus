"""Desktop computer use over a platform accessibility backend (LOCUS-341).

Doc 12 §2-§3 and §6. :class:`DesktopTool` is platform-neutral: it owns the
gateway calls, the controller (mode / cancellation / panic), the element refs
handed to the model and the output caps. A :class:`DesktopBackend` does the
platform work:

* Windows -- :class:`locus_runtime.computer_use.windows_uia.UiaBackend`
  (UI Automation through ``comtypes``; verified on Windows 11).
* macOS -- :class:`locus_runtime.computer_use.macos_ax.AxBackend`
  (Accessibility API through PyObjC; **unverified on a real Mac**).

Every primitive follows the same order: perceive the target live (never a stale
frame) → check the mode → authorize at the gateway with the perceived
:class:`~locus_runtime.gateway.UiFacts` → ``token.check()`` → semantic action
(UIA Invoke / Value patterns, AX press / set value) → synthetic input only as a
fallback, and only after the backend has confirmed the target window is in the
foreground (so input can never land in another app).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from locus_runtime.computer_use.common import (
    UiResult,
    cancelled_result,
    gate,
    mode_refusal,
    wrap_untrusted,
)
from locus_runtime.computer_use.controller import (
    CancelToken,
    ComputerUseCancelled,
    ComputerUseController,
    get_controller,
)
from locus_runtime.gateway import GatewaySession, UiFacts

_MAX_TEXT_CHARS = 10_000


@dataclass(frozen=True)
class DesktopWindow:
    """A top-level window the backend found; ``native`` is the platform object."""

    app: str  # lower-case executable (Windows) or bundle id (macOS)
    title: str
    pid: int
    native: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class NodeFacts:
    """One accessibility node, as the backend read it (values of secret fields omitted)."""

    name: str
    role: str
    automation_id: str = ""
    class_name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    enabled: bool = True
    is_password: bool = False
    value: str = ""
    pid: int = 0
    depth: int = 0


@dataclass(frozen=True)
class Node:
    facts: NodeFacts
    native: Any = field(default=None, compare=False, repr=False)


class DesktopUnavailable(RuntimeError):
    """No accessibility backend can run here (platform, library or session)."""


@runtime_checkable
class DesktopBackend(Protocol):
    platform: str

    def available(self) -> tuple[bool, str]: ...

    def find_window(self, *, app: str = "", title: str = "") -> DesktopWindow | None: ...

    def walk(
        self, window: DesktopWindow, *, max_depth: int, max_nodes: int, token: CancelToken
    ) -> list[Node]: ...

    def describe(self, native: Any) -> NodeFacts: ...

    def focused(self, window: DesktopWindow) -> Node | None: ...

    def invoke(self, native: Any) -> bool: ...

    def set_value(self, native: Any, text: str) -> bool: ...

    def synth_click(self, window: DesktopWindow, native: Any, token: CancelToken) -> None: ...

    def synth_text(
        self, window: DesktopWindow, native: Any, text: str, token: CancelToken
    ) -> None: ...

    def synth_keys(self, window: DesktopWindow, chord: str, token: CancelToken) -> None: ...


def _ui(window: DesktopWindow, control: str, facts: NodeFacts | None, key: str = "") -> UiFacts:
    return UiFacts.create(
        surface="desktop",
        control=control,
        app=window.app,
        role=facts.role if facts else "",
        name=facts.name if facts else "",
        field_id=facts.automation_id if facts else "",
        is_password=bool(facts and facts.is_password),
        key=key,
    )


class DesktopTool:
    """Gated observe / click / type / key on allowlisted desktop apps."""

    def __init__(
        self,
        session: GatewaySession | None,
        backend: DesktopBackend,
        *,
        controller: ComputerUseController | None = None,
        max_depth: int = 12,
        max_nodes: int = 400,
        read_max_chars: int = 16_000,
        allow_synthetic_input: bool = True,
    ) -> None:
        self._session = session
        self._backend = backend
        self._controller = controller or get_controller()
        self._max_depth = max(1, min(int(max_depth), 40))
        self._max_nodes = max(1, min(int(max_nodes), 5_000))
        self._read_max = max(1_000, int(read_max_chars))
        self._allow_synthetic = allow_synthetic_input
        self._window: DesktopWindow | None = None
        self._refs: dict[str, Node] = {}

    @property
    def window(self) -> DesktopWindow | None:
        return self._window

    def refs(self) -> dict[str, NodeFacts]:
        return {ref: node.facts for ref, node in self._refs.items()}

    # -- observe ---------------------------------------------------------------
    def observe(self, *, app: str = "", title: str = "") -> UiResult:
        tool = "desktop_observe"
        ok, why = self._backend.available()
        if not ok:
            return UiResult("error", f"[error] {tool}: desktop control unavailable ({why}).")
        try:
            with self._controller.action("ui_observe") as token:
                window = self._backend.find_window(app=app, title=title)
                if window is None:
                    return UiResult("error", f"[error] {tool}: no window matches app/title.")
                ui = _ui(window, "observe", None)
                decision, blocked = gate(
                    self._session,
                    kind="ui_observe",
                    tool=tool,
                    ui=ui,
                    target=f"{window.app}: {window.title}"[:300],
                )
                if blocked is not None:
                    return blocked
                nodes = self._backend.walk(
                    window, max_depth=self._max_depth, max_nodes=self._max_nodes, token=token
                )
                self._window = window
                self._refs = {f"d{index}": node for index, node in enumerate(nodes, start=1)}
                lines = [self._line(ref, node.facts) for ref, node in self._refs.items()]
                body = "\n".join(lines)
                if len(body) > self._read_max:
                    body = body[: self._read_max] + "\n[... truncated]"
                return UiResult(
                    "done",
                    f"Window '{window.title[:120]}' ({window.app}), {len(nodes)} element(s)\n"
                    + wrap_untrusted(body, source="screen"),
                    decision=decision,
                    data={"app": window.app, "count": len(nodes)},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    @staticmethod
    def _line(ref: str, facts: NodeFacts) -> str:
        indent = "  " * min(facts.depth, 12)
        line = f'{indent}[{ref}] {facts.role} "{facts.name[:120]}"'
        if facts.automation_id:
            line += f" id={facts.automation_id[:60]}"
        left, top, right, bottom = facts.rect
        line += f" rect=({left},{top},{right},{bottom})"
        if facts.is_password:
            line += " password"
        elif facts.value:
            line += f" value={facts.value[:120]!r}"
        if not facts.enabled:
            line += " (disabled)"
        return line

    # -- act -------------------------------------------------------------------
    def _target(self, ref: str) -> tuple[DesktopWindow, Node] | UiResult:
        if self._window is None:
            return UiResult("error", "[error] call desktop_observe first.")
        node = self._refs.get(str(ref or ""))
        if node is None:
            return UiResult("error", f"[error] unknown element ref {ref!r}; observe again.")
        return self._window, node

    def _live(self, window: DesktopWindow, node: Node) -> NodeFacts | UiResult:
        """Re-read the element now: coordinates and identity never come from a stale tree."""
        try:
            facts = self._backend.describe(node.native)
        except Exception:  # noqa: BLE001 - element vanished
            return UiResult("error", "[error] that element is gone; observe again.")
        if facts.pid and window.pid and facts.pid != window.pid:
            return UiResult("error", "[error] that element no longer belongs to the window.")
        return facts

    def click(self, ref: str) -> UiResult:
        tool = "desktop_click"
        try:
            with self._controller.action("ui_click") as token:
                target = self._target(ref)
                if isinstance(target, UiResult):
                    return target
                window, node = target
                facts = self._live(window, node)
                if isinstance(facts, UiResult):
                    return facts
                ui = _ui(window, "click", facts)
                describe = f'click {facts.role} "{facts.name[:80]}" in {window.app}'
                refusal = mode_refusal(self._controller, "ui_click", tool, ui, describe)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="ui_click",
                    tool=tool,
                    ui=ui,
                    target=f"{window.app}: {facts.role} '{facts.name[:80]}'",
                    args={"ref": ref},
                )
                if blocked is not None:
                    return blocked
                token.check()
                how = "invoke"
                if not self._backend.invoke(node.native):
                    if not self._allow_synthetic:
                        return UiResult("error", f"[error] {tool}: element has no invoke action.")
                    token.check()
                    self._backend.synth_click(window, node.native, token)
                    how = "synthetic click"
                return UiResult("done", f"{describe}: done ({how}). Observe again.", decision)
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)
        except DesktopUnavailable as exc:
            return UiResult("error", f"[error] {tool}: {exc}")

    def type(self, ref: str, text: str) -> UiResult:
        """Set the text of an editable element (replaces its value)."""
        tool = "desktop_type"
        text = str(text or "")
        if len(text) > _MAX_TEXT_CHARS:
            return UiResult("error", f"[error] {tool}: text longer than {_MAX_TEXT_CHARS} chars.")
        try:
            with self._controller.action("ui_type") as token:
                target = self._target(ref)
                if isinstance(target, UiResult):
                    return target
                window, node = target
                facts = self._live(window, node)
                if isinstance(facts, UiResult):
                    return facts
                ui = _ui(window, "type", facts)
                describe = f'type into {facts.role} "{facts.name[:80]}" in {window.app}'
                refusal = mode_refusal(self._controller, "ui_type", tool, ui, describe)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="ui_type",
                    tool=tool,
                    ui=ui,
                    target=f"{window.app}: {facts.role} '{facts.name[:80]}'",
                    args={"ref": ref, "text": text},
                )
                if blocked is not None:
                    return blocked
                token.check()
                how = "value pattern"
                if not self._backend.set_value(node.native, text):
                    if not self._allow_synthetic:
                        return UiResult("error", f"[error] {tool}: element has no settable value.")
                    token.check()
                    self._backend.synth_text(window, node.native, text, token)
                    how = "synthetic typing"
                return UiResult("done", f"{describe}: done ({how}, {len(text)} chars).", decision)
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)
        except DesktopUnavailable as exc:
            return UiResult("error", f"[error] {tool}: {exc}")

    def key(self, chord: str, ref: str = "") -> UiResult:
        """Send a key chord (e.g. ``ctrl+s``, ``Enter``) to the observed window."""
        tool = "desktop_key"
        chord = str(chord or "").strip()
        if not chord or len(chord) > 64:
            return UiResult("error", f"[error] {tool}: give a key chord like 'ctrl+s'.")
        try:
            with self._controller.action("ui_key") as token:
                if self._window is None:
                    return UiResult("error", "[error] call desktop_observe first.")
                window = self._window
                facts: NodeFacts | None = None
                if ref:
                    target = self._target(ref)
                    if isinstance(target, UiResult):
                        return target
                    live = self._live(*target)
                    if isinstance(live, UiResult):
                        return live
                    facts = live
                else:
                    # Keys go to the focused element: classify against it (a password
                    # field focused means R4).
                    focused = self._backend.focused(window)
                    facts = focused.facts if focused is not None else None
                ui = _ui(window, "key", facts, key=chord)
                describe = f"key {chord} in {window.app}"
                refusal = mode_refusal(self._controller, "ui_key", tool, ui, describe)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="ui_key",
                    tool=tool,
                    ui=ui,
                    target=f"{window.app}: {chord}",
                    args={"chord": chord, "ref": ref},
                )
                if blocked is not None:
                    return blocked
                if not self._allow_synthetic:
                    return UiResult("error", f"[error] {tool}: synthetic input is disabled.")
                token.check()
                self._backend.synth_keys(window, chord, token)
                return UiResult("done", f"{describe}: done. Observe again.", decision)
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)
        except DesktopUnavailable as exc:
            return UiResult("error", f"[error] {tool}: {exc}")


def parse_chord(chord: str) -> list[str]:
    """``"Ctrl+Shift+S"`` → ``["ctrl", "shift", "s"]`` (lower-case key names)."""
    return [part.strip().lower() for part in str(chord or "").split("+") if part.strip()]


def chunks(text: str, size: int = 8) -> Sequence[str]:
    """Split text for synthetic typing so cancellation is checked between chunks."""
    return [text[i : i + size] for i in range(0, len(text), size)]
