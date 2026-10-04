"""In-process relay between the user-browser tool and the extension (LOCUS-350).

::

    UserBrowserDriver ──call()──▶ RelayHub ◀──long poll / results── native host ◀─stdio─▶ extension
                       (backend process)      (loopback HTTP, pairing key)        (browser-launched)

The extension never initiates work: it only answers commands the hub queued,
and the hub only queues commands the driver sends *after* the gateway allowed
them (the driver authorizes first). The hub's own rules:

* **Pairing** -- :meth:`RelayHub.hello` admits a host only with the stored
  pairing key (constant-time compare) and a pinned extension origin. The host
  then uses a per-connection session token (only its hash is kept).
* **Panic** -- the hub listens on the computer-use controller. A panic fails
  every pending call, queues a ``panic`` message to every connected client and
  bumps the command epoch, so the extension refuses any command issued before
  the panic even if it is still in flight. While the latch is set no command is
  queued at all.
* **Bounded** -- per-client queues, result sizes and waits are bounded.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from locus_runtime.computer_use.controller import (
    CancelToken,
    ComputerUseController,
    get_controller,
)
from locus_runtime.computer_use.user_browser.pairing import (
    keys_match,
    load_pairing_key,
    origin_family,
)

logger = logging.getLogger(__name__)

MAX_QUEUE = 64
MAX_LONG_POLL_S = 25.0
DEFAULT_CALL_TIMEOUT_S = 20.0
STALE_AFTER_S = 60.0
_POLL_S = 0.025


class RelayError(RuntimeError):
    """A relay call failed; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class RelayAuthError(RelayError):
    """Unpaired, mismatched or unknown client: the request is refused."""


@dataclass
class RelayClient:
    client_id: str
    family: str
    origin: str
    browser: str
    extension_version: str
    session_digest: str
    connected_at: float
    last_seen: float
    queue: deque[dict[str, Any]] = field(default_factory=deque)

    def view(self, now: float) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "family": self.family,
            "browser": self.browser,
            "extension_version": self.extension_version,
            "connected_at": self.connected_at,
            "last_seen_s_ago": round(max(0.0, now - self.last_seen), 1),
            "connected": now - self.last_seen <= STALE_AFTER_S,
        }


@dataclass
class _Pending:
    client_id: str
    event: threading.Event = field(default_factory=threading.Event)
    message: dict[str, Any] | None = None
    failure: str = ""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RelayHub:
    """Command queue and result rendezvous for paired extension clients."""

    def __init__(
        self,
        *,
        key_loader: Callable[[], str | None] = load_pairing_key,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key_loader = key_loader
        self._clock = clock
        self._cond = threading.Condition()
        self._clients: dict[str, RelayClient] = {}
        self._pending: dict[str, _Pending] = {}
        self._epoch = 1
        self._attached: set[int] = set()

    # -- controller ------------------------------------------------------------
    def attach(self, controller: ComputerUseController) -> None:
        """Listen for panics on ``controller`` (idempotent)."""
        with self._cond:
            if id(controller) in self._attached:
                return
            self._attached.add(id(controller))
        controller.on_panic(self._on_panic)

    def _on_panic(self) -> None:
        # Runs on the panic caller's thread: only flags, queues and wake-ups.
        with self._cond:
            epoch = self._epoch
            self._epoch += 1
            for pending in self._pending.values():
                pending.failure = "panic"
                pending.event.set()
            for client in self._clients.values():
                client.queue.clear()
                client.queue.append({"type": "panic", "epoch": epoch})
            self._cond.notify_all()
        logger.warning("user_browser.relay_panic", extra={"epoch": epoch})

    @property
    def epoch(self) -> int:
        with self._cond:
            return self._epoch

    # -- pairing / host side ---------------------------------------------------
    @property
    def paired(self) -> bool:
        """A pairing key exists in the secret store."""
        try:
            return bool(self._key_loader())
        except Exception:  # noqa: BLE001 - an unreadable store is "not paired"
            return False

    def hello(
        self,
        *,
        origin: str,
        presented_key: str,
        browser: str = "",
        extension_version: str = "",
    ) -> tuple[str, str]:
        """Admit a native host. Returns ``(client_id, session_token)``."""
        family = origin_family(origin)
        if family is None:
            raise RelayAuthError("origin_not_pinned", "extension origin is not a pinned Locus ID")
        try:
            expected = self._key_loader()
        except Exception:  # noqa: BLE001 - unreadable store refuses
            expected = None
        if not expected:
            raise RelayAuthError("not_paired", "no browser is paired with this Locus install")
        if not keys_match(str(presented_key or ""), expected):
            raise RelayAuthError("pairing_mismatch", "pairing key does not match")
        token = secrets.token_urlsafe(32)
        now = self._clock()
        client = RelayClient(
            client_id=f"ub-{uuid4().hex[:12]}",
            family=family,
            origin=str(origin),
            browser=str(browser or family)[:40],
            extension_version=str(extension_version or "")[:20],
            session_digest=_digest(token),
            connected_at=now,
            last_seen=now,
        )
        with self._cond:
            self._clients[client.client_id] = client
            self._cond.notify_all()
        logger.info(
            "user_browser.client_connected",
            extra={"client_id": client.client_id, "family": family, "browser": client.browser},
        )
        return client.client_id, token

    def _client(self, client_id: str, session_token: str) -> RelayClient:
        # Caller holds self._cond.
        client = self._clients.get(str(client_id or ""))
        if client is None or not keys_match(
            _digest(str(session_token or "")), client.session_digest
        ):
            raise RelayAuthError("unknown_client", "unknown relay client or session")
        if not self.paired:
            # Unpairing revokes every live session from the next request.
            self._clients.pop(client.client_id, None)
            raise RelayAuthError("not_paired", "this browser is no longer paired")
        return client

    def authenticate(self, client_id: str, session_token: str) -> str:
        """The client's id if its session is valid (and the browser still paired)."""
        with self._cond:
            client = self._client(client_id, session_token)
            client.last_seen = self._clock()
            return client.client_id

    def next_commands(
        self, client_id: str, session_token: str, *, wait_s: float = MAX_LONG_POLL_S
    ) -> list[dict[str, Any]]:
        """Long poll: commands queued for this client (possibly none after ``wait_s``)."""
        deadline = self._clock() + max(0.0, min(float(wait_s), MAX_LONG_POLL_S))
        with self._cond:
            client = self._client(client_id, session_token)
            while True:
                client.last_seen = self._clock()
                if client.queue:
                    out = list(client.queue)
                    client.queue.clear()
                    return out
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return []
                self._cond.wait(min(remaining, 1.0))
                if self._clients.get(client.client_id) is not client:
                    raise RelayAuthError("unknown_client", "relay session ended")

    def post_result(self, client_id: str, session_token: str, message: dict[str, Any]) -> bool:
        """Deliver one result message; ``False`` if nobody is waiting for it."""
        with self._cond:
            client = self._client(client_id, session_token)
            client.last_seen = self._clock()
            command_id = str(message.get("id") or "")
            pending = self._pending.get(command_id)
            if pending is None or pending.client_id != client.client_id:
                return False
            pending.message = dict(message)
            pending.event.set()
            return True

    def bye(self, client_id: str, session_token: str) -> None:
        with self._cond:
            client = self._client(client_id, session_token)
            self._drop_locked(client.client_id, "client_gone")

    def revoke_all(self, reason: str = "unpaired") -> None:
        """Drop every client (unpairing); pending calls fail."""
        with self._cond:
            for client_id in list(self._clients):
                self._drop_locked(client_id, reason)

    def _drop_locked(self, client_id: str, reason: str) -> None:
        self._clients.pop(client_id, None)
        for pending in self._pending.values():
            if pending.client_id == client_id:
                pending.failure = reason
                pending.event.set()
        self._cond.notify_all()

    # -- driver side -----------------------------------------------------------
    def clients(self) -> list[dict[str, Any]]:
        now = self._clock()
        with self._cond:
            return [client.view(now) for client in self._clients.values()]

    def _live_client(self, client_id: str = "") -> RelayClient | None:
        now = self._clock()
        live = [c for c in self._clients.values() if now - c.last_seen <= STALE_AFTER_S]
        if client_id:
            live = [c for c in live if c.client_id == client_id or c.browser == client_id]
        return max(live, key=lambda c: c.last_seen) if live else None

    def connected(self, client_id: str = "") -> bool:
        if not self.paired:
            return False
        with self._cond:
            return self._live_client(client_id) is not None

    def call(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        client_id: str = "",
        cancel: CancelToken | None = None,
        controller: ComputerUseController | None = None,
        timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Queue one command for the extension and wait for its result."""
        controller = controller or get_controller()
        self.attach(controller)
        if controller.panicked:
            raise RelayError("panic", "computer use is stopped (panic)")
        if not self.paired:
            raise RelayError("not_paired", "no browser is paired with Locus")
        command_id = f"cmd-{uuid4().hex[:16]}"
        with self._cond:
            client = self._live_client(client_id)
            if client is None:
                raise RelayError("no_browser", "no paired browser is connected")
            if len(client.queue) >= MAX_QUEUE:
                raise RelayError("busy", "the browser has too many pending commands")
            pending = _Pending(client.client_id)
            self._pending[command_id] = pending
            client.queue.append(
                {
                    "type": "command",
                    "id": command_id,
                    "op": str(op),
                    "args": dict(args or {}),
                    "epoch": self._epoch,
                }
            )
            self._cond.notify_all()
        deadline = self._clock() + max(0.1, float(timeout_s))
        try:
            while not pending.event.is_set():
                if cancel is not None and cancel.cancelled:
                    self._send_cancel(pending.client_id, command_id)
                    cancel.check()
                if self._clock() >= deadline:
                    self._send_cancel(pending.client_id, command_id)
                    raise RelayError("timeout", f"the browser did not answer {op!r} in time")
                pending.event.wait(_POLL_S)
        finally:
            with self._cond:
                self._pending.pop(command_id, None)
        if cancel is not None:
            cancel.check()
        if pending.failure:
            raise RelayError(pending.failure, f"browser command {op!r} failed: {pending.failure}")
        message = pending.message or {}
        if message.get("ok") is not True:
            error = message.get("error") if isinstance(message.get("error"), dict) else {}
            code = str((error or {}).get("code") or "extension_error")[:64]
            text = str((error or {}).get("message") or code)[:300]
            raise RelayError(code, text)
        result = message.get("result")
        return dict(result) if isinstance(result, dict) else {}

    def _send_cancel(self, client_id: str, command_id: str) -> None:
        with self._cond:
            client = self._clients.get(client_id)
            if client is not None:
                client.queue.append({"type": "cancel", "id": command_id})
                self._cond.notify_all()


# --------------------------------------------------------------------------- #
# Process-wide hub
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_HUB: RelayHub | None = None


def get_hub() -> RelayHub:
    global _HUB
    with _LOCK:
        if _HUB is None:
            _HUB = RelayHub()
            _HUB.attach(get_controller())
        return _HUB


def install_hub(hub: RelayHub | None) -> None:
    global _HUB
    with _LOCK:
        _HUB = hub


__all__ = [
    "DEFAULT_CALL_TIMEOUT_S",
    "MAX_LONG_POLL_S",
    "RelayAuthError",
    "RelayClient",
    "RelayError",
    "RelayHub",
    "get_hub",
    "install_hub",
]
