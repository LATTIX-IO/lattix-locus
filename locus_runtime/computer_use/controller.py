"""Computer-use modes, cooperative cancellation and the panic latch (LOCUS-341).

Doc 12 §1 / §5, P5. One :class:`ComputerUseController` per process (see
:func:`get_controller`) is shared by every computer-use tool, so one
:meth:`~ComputerUseController.panic` stops them all:

* **Modes** -- ``observe`` (read the screen / page only), ``assist`` (acting
  calls are returned as proposals for the human to carry out; nothing is
  driven), ``takeover`` (the agent drives input). The default is ``observe``.
* **Cancellation** -- every UI action runs inside :meth:`ComputerUseController.action`,
  which hands out a :class:`CancelToken`. Tools call :meth:`CancelToken.check`
  before *every* primitive (each key, each chunk of text, each poll while
  waiting for an element), so a panic stops in-flight work at the next
  primitive boundary.
* **Panic** -- :meth:`~ComputerUseController.panic` latches: it cancels every
  in-flight token immediately and rejects every new action until a human calls
  :meth:`~ComputerUseController.reset`. It is idempotent and never blocks on
  the tools (listeners only set flags). The backend exposes it as
  ``POST /computer-use/panic``; the desktop app's global hotkey calls that.

What this is not (yet): the OS-level native helper of 12 §4 that preempts
input on physical mouse/keyboard activity and draws the HUD. Cancellation here
is cooperative, inside this process.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

Mode = Literal["observe", "assist", "takeover"]
MODES: tuple[Mode, ...] = ("observe", "assist", "takeover")

#: Kinds that only perceive (allowed in every mode).
OBSERVE_KINDS = frozenset({"ui_observe", "browser_read", "user_browser_read"})
#: Kinds that act on the agent browser, the principal's own browser or the desktop.
ACT_KINDS = frozenset(
    {
        "ui_click",
        "ui_type",
        "ui_key",
        "browser_navigate",
        "browser_act",
        "user_browser_navigate",
        "user_browser_act",
    }
)


class ComputerUseCancelled(RuntimeError):
    """The action was cancelled (panic) or refused because panic is latched."""


class CancelToken:
    """Cooperative cancellation for one in-flight UI action."""

    __slots__ = ("_event", "action_id", "kind", "reason")

    def __init__(self, kind: str) -> None:
        self._event = threading.Event()
        self.action_id = f"cu-{uuid4().hex[:12]}"
        self.kind = kind
        self.reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "panic") -> None:
        self.reason = reason
        self._event.set()

    def check(self) -> None:
        """Raise :class:`ComputerUseCancelled` if cancelled. Call before each primitive."""
        if self._event.is_set():
            raise ComputerUseCancelled(f"computer-use action cancelled ({self.reason or 'panic'})")

    def wait(self, seconds: float) -> None:
        """Sleep up to ``seconds``, waking (and raising) as soon as it is cancelled."""
        if self._event.wait(max(0.0, seconds)):
            self.check()


@dataclass(frozen=True)
class PanicReport:
    panicked_at: float
    already_latched: bool
    cancelled_actions: int
    latency_ms: float  # time to cancel every in-flight token and latch
    source: str

    def as_dict(self) -> dict[str, object]:
        return {
            "panicked": True,
            "already_latched": self.already_latched,
            "cancelled_actions": self.cancelled_actions,
            "latency_ms": round(self.latency_ms, 3),
            "source": self.source,
        }


PanicListener = Callable[[], None]


class ComputerUseController:
    """Mode, in-flight actions and the panic latch for computer use."""

    def __init__(self, mode: Mode = "observe", *, clock: Callable[[], float] = time.monotonic):
        if mode not in MODES:
            raise ValueError(f"unknown computer-use mode {mode!r}")
        self._mode: Mode = mode
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: dict[str, CancelToken] = {}
        self._panicked = False
        self._panic_source = ""
        self._panicked_at = 0.0
        self._listeners: list[PanicListener] = []

    # -- state -----------------------------------------------------------------
    @property
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown computer-use mode {mode!r}")
        with self._lock:
            self._mode = mode

    @property
    def panicked(self) -> bool:
        return self._panicked

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "mode": self._mode,
                "panicked": self._panicked,
                "panic_source": self._panic_source,
                "inflight_actions": len(self._inflight),
            }

    def on_panic(self, listener: PanicListener) -> None:
        """Register a non-blocking callback run on panic (e.g. 'close the browser')."""
        with self._lock:
            self._listeners.append(listener)

    def remove_panic_listener(self, listener: PanicListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def permits(self, kind: str) -> tuple[bool, str]:
        """Whether the current mode lets the agent *perform* ``kind`` itself."""
        if kind in OBSERVE_KINDS:
            return True, ""
        if kind not in ACT_KINDS:
            return False, f"unknown computer-use action kind {kind!r}"
        if self._mode == "takeover":
            return True, ""
        return False, f"mode is {self._mode!r}: the agent does not drive input"

    # -- actions ---------------------------------------------------------------
    def begin(self, kind: str) -> CancelToken:
        """Register an in-flight action; rejected immediately while panic is latched."""
        token = CancelToken(kind)
        with self._lock:
            if self._panicked:
                raise ComputerUseCancelled(
                    "computer use is stopped (panic); a human must reset it before any "
                    "further UI action"
                )
            self._inflight[token.action_id] = token
        return token

    def end(self, token: CancelToken) -> None:
        with self._lock:
            self._inflight.pop(token.action_id, None)

    @contextmanager
    def action(self, kind: str) -> Iterator[CancelToken]:
        token = self.begin(kind)
        try:
            yield token
        finally:
            self.end(token)

    # -- human control ---------------------------------------------------------
    def panic(self, source: str = "user") -> PanicReport:
        """Cancel everything in flight and block new actions until :meth:`reset`.

        Idempotent. Never waits for the tools: tokens are flagged and listeners
        only set flags, so this returns in well under the 100 ms budget (12 §5).
        """
        start = self._clock()
        with self._lock:
            already = self._panicked
            self._panicked = True
            if not already:
                self._panic_source = str(source or "user")[:64]
                self._panicked_at = time.time()
            tokens = list(self._inflight.values())
            listeners = list(self._listeners)
        for token in tokens:
            token.cancel("panic")
        latency_ms = (self._clock() - start) * 1000.0
        for listener in listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001 - a listener never stops the panic
                logger.exception("computer_use.panic_listener_error")
        logger.warning(
            "computer_use.panic",
            extra={"source": source, "cancelled": len(tokens), "already_latched": already},
        )
        return PanicReport(
            panicked_at=self._panicked_at,
            already_latched=already,
            cancelled_actions=len(tokens),
            latency_ms=latency_ms,
            source=self._panic_source,
        )

    def reset(self, actor: str = "user") -> None:
        """Clear the panic latch (a human decision; the mode drops to ``observe``)."""
        with self._lock:
            self._panicked = False
            self._panic_source = ""
            self._mode = "observe"
        logger.info("computer_use.reset", extra={"actor": actor})


# --------------------------------------------------------------------------- #
# Process-wide controller
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_DEFAULT: ComputerUseController | None = None
_INSTALLED = False


def get_controller() -> ComputerUseController:
    """The process controller every computer-use tool and the panic endpoint share."""
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is None:
            _DEFAULT = ComputerUseController()
        return _DEFAULT


def install_controller(controller: ComputerUseController | None) -> None:
    """Install (``None``: uninstall) the controller computer-use runs are wired to.

    Installing is the wiring fact the posture page reports (``computer_use``);
    an uninstalled process still has a default controller so panic always works.
    """
    global _DEFAULT, _INSTALLED
    with _LOCK:
        if controller is None:
            _INSTALLED = False
            return
        _DEFAULT = controller
        _INSTALLED = True


def controller_installed() -> bool:
    return _INSTALLED
