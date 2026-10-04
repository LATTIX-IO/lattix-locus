"""Native-messaging host for the Locus browser extension (LOCUS-350).

The browser starts this process when the extension calls
``runtime.connectNative("io.lattix.locus_browser")`` -- only for an extension
ID listed in the host manifest (see ``locus_tooling.native_messaging``). It
relays between the extension (stdio, 4-byte native-endian length + JSON) and
the Locus backend relay (loopback HTTP):

* refuses callers whose origin is not a pinned Locus extension ID;
* reads the pairing key from the OS secret store (never from the extension);
* talks only to a loopback backend URL;
* forwards backend commands to the extension, and the extension's results
  (and its panic button) to the backend. It never invents commands.

Entry points: the frozen desktop backend detects a native-messaging launch
from its arguments (``is_native_messaging_invocation``), so the host manifest
can point straight at ``locus-backend``; ``python -m
locus_runtime.computer_use.user_browser.native_host`` works from a checkout.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO, Protocol
from urllib import parse as urlparse

from locus_runtime.computer_use.user_browser.pairing import (
    ALLOWED_ORIGINS,
    FIREFOX_EXTENSION_ID,
    load_pairing_key,
    origin_family,
)

logger = logging.getLogger(__name__)

HOST_FLAG = "--native-messaging-host"
BACKEND_URL_ENV = "LOCUS_USER_BROWSER_BACKEND_URL"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
#: Chrome refuses host→extension messages over 1 MiB.
MAX_TO_EXTENSION = 1024 * 1024
#: Extension→host (a screenshot result is the largest).
MAX_FROM_EXTENSION = 16 * 1024 * 1024
POLL_WAIT_S = 20.0
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class NativeMessagingError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #
def read_message(stream: BinaryIO, *, limit: int = MAX_FROM_EXTENSION) -> dict[str, Any] | None:
    """One framed message, or ``None`` at end of stream."""
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise NativeMessagingError("truncated length prefix")
    (length,) = struct.unpack("=I", header)
    if length == 0 or length > limit:
        raise NativeMessagingError(f"message length {length} out of bounds")
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            raise NativeMessagingError("truncated message body")
        body += chunk
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise NativeMessagingError("message is not JSON") from exc
    if not isinstance(message, dict):
        raise NativeMessagingError("message is not a JSON object")
    return message


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    data = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_TO_EXTENSION:
        raise NativeMessagingError("message for the extension is over 1 MiB")
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


# --------------------------------------------------------------------------- #
# Invocation
# --------------------------------------------------------------------------- #
def caller_origin(argv: Sequence[str]) -> str:
    """The calling extension: Chromium passes its origin, Firefox its add-on ID."""
    for arg in argv[1:]:
        text = str(arg)
        if text.startswith("chrome-extension://"):
            return text if text.endswith("/") else text + "/"
        if text == FIREFOX_EXTENSION_ID:
            return text
    return ""


def is_native_messaging_invocation(argv: Sequence[str]) -> bool:
    """A browser launched us as the host (or a wrapper passed ``--native-messaging-host``)."""
    if HOST_FLAG in argv[1:]:
        return True
    return caller_origin(argv) in ALLOWED_ORIGINS


def backend_url(env: dict[str, str] | None = None) -> str:
    """The backend base URL; loopback ``http`` only (the pairing key never leaves the host)."""
    raw = str((env if env is not None else os.environ).get(BACKEND_URL_ENV) or "").strip()
    url = raw or DEFAULT_BACKEND_URL
    parts = urlparse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in _LOOPBACK:
        raise NativeMessagingError("the Locus backend URL must be http on loopback")
    return f"http://{parts.netloc}"


# --------------------------------------------------------------------------- #
# Backend relay client
# --------------------------------------------------------------------------- #
class RelayAuthRefused(NativeMessagingError):
    pass


class RelayTransport(Protocol):
    def hello(self, origin: str, key: str, browser: str, version: str) -> None: ...

    def next(self, wait_s: float) -> list[dict[str, Any]]: ...

    def result(self, message: dict[str, Any]) -> None: ...

    def event(self, message: dict[str, Any]) -> None: ...

    def bye(self) -> None: ...


class HttpRelay:
    """The backend relay over loopback HTTP."""

    def __init__(self, base_url: str, *, client: Any = None) -> None:
        import httpx

        self._http = client or httpx.Client(
            base_url=base_url, timeout=POLL_WAIT_S + 10.0, trust_env=False
        )
        self._session: dict[str, str] = {}

    def _post(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        response = self._http.post(path, json=body, headers=headers)
        if response.status_code in {401, 403}:
            raise RelayAuthRefused(f"relay refused ({response.status_code})")
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def hello(self, origin: str, key: str, browser: str, version: str) -> None:
        data = self._post(
            "/user-browser/relay/hello",
            {"origin": origin, "browser": browser, "extension_version": version},
            {"X-Locus-Pairing-Key": key},
        )
        self._session = {
            "X-Locus-Relay-Client": str(data.get("client_id") or ""),
            "X-Locus-Relay-Session": str(data.get("session_token") or ""),
        }

    def next(self, wait_s: float) -> list[dict[str, Any]]:
        data = self._post("/user-browser/relay/next", {"wait_s": wait_s}, self._session)
        commands = data.get("commands")
        return [c for c in commands if isinstance(c, dict)] if isinstance(commands, list) else []

    def result(self, message: dict[str, Any]) -> None:
        self._post("/user-browser/relay/result", message, self._session)

    def event(self, message: dict[str, Any]) -> None:
        self._post("/user-browser/relay/event", message, self._session)

    def bye(self) -> None:
        try:
            self._post("/user-browser/relay/bye", {}, self._session)
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass


# --------------------------------------------------------------------------- #
# The host
# --------------------------------------------------------------------------- #
class NativeHost:
    def __init__(
        self,
        *,
        stdin: BinaryIO,
        stdout: BinaryIO,
        origin: str,
        relay_factory: Callable[[], RelayTransport],
        key_loader: Callable[[], str | None] = load_pairing_key,
        poll_wait_s: float = POLL_WAIT_S,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._origin = origin
        self._relay_factory = relay_factory
        self._key_loader = key_loader
        self._wait = poll_wait_s
        self._write_lock = threading.Lock()
        self._stop = threading.Event()

    def _send(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            write_message(self._stdout, message)

    def _status(self, connected: bool, error: str = "") -> None:
        try:
            self._send({"type": "status", "connected": connected, "error": error})
        except Exception:  # noqa: BLE001 - the browser may already be gone
            pass

    def run(self) -> int:
        if origin_family(self._origin) is None:
            self._status(False, "origin_not_pinned")
            return 2
        key = self._key_loader()
        if not key:
            self._status(False, "not_paired")
            return 3
        first = read_message(self._stdin)
        if first is None:
            return 0
        browser = str(first.get("browser") or "")[:40] if first.get("type") == "hello" else ""
        version = str(first.get("version") or "")[:20] if first.get("type") == "hello" else ""
        relay = self._relay_factory()
        try:
            relay.hello(self._origin, key, browser, version)
        except RelayAuthRefused:
            self._status(False, "pairing_refused")
            return 4
        except Exception:  # noqa: BLE001 - backend down: tell the popup, exit
            self._status(False, "backend_unreachable")
            return 5
        del key
        self._status(True)
        reader = threading.Thread(target=self._pump_from_extension, args=(relay,), daemon=True)
        reader.start()
        try:
            return self._pump_to_extension(relay)
        finally:
            self._stop.set()
            relay.bye()

    def _pump_from_extension(self, relay: RelayTransport) -> None:
        try:
            while not self._stop.is_set():
                message = read_message(self._stdin)
                if message is None:
                    break
                kind = message.get("type")
                try:
                    if kind == "result":
                        relay.result(message)
                    elif kind == "event" and message.get("event") == "panic":
                        relay.event({"event": "panic"})
                except RelayAuthRefused:
                    break
                except Exception:  # noqa: BLE001 - one lost result times out backend-side
                    logger.debug("user_browser.host_forward_error", exc_info=True)
        except NativeMessagingError:
            logger.warning("user_browser.host_bad_message")
        finally:
            self._stop.set()

    def _pump_to_extension(self, relay: RelayTransport) -> int:
        failures = 0
        while True:
            try:
                commands = relay.next(self._wait)
                failures = 0
            except RelayAuthRefused:
                self._status(False, "pairing_refused")
                return 4
            except Exception:  # noqa: BLE001 - backend restarting: back off, keep going
                failures += 1
                if failures > 30:
                    self._status(False, "backend_unreachable")
                    return 5
                time.sleep(min(5.0, 0.5 * failures))
                commands = []
            for command in commands:
                try:
                    self._send(command)
                except NativeMessagingError:
                    logger.warning("user_browser.host_command_too_large")
            if self._stop.is_set():
                return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    origin = caller_origin(args)
    if sys.platform == "win32":  # pragma: no cover - Windows stdio is binary for framing
        import msvcrt

        for stream in (sys.stdin, sys.stdout):
            msvcrt.setmode(stream.fileno(), os.O_BINARY)
    try:
        base = backend_url()
    except NativeMessagingError:
        write_message(
            sys.stdout.buffer, {"type": "status", "connected": False, "error": "bad_backend_url"}
        )
        return 6
    host = NativeHost(
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        origin=origin,
        relay_factory=lambda: HttpRelay(base),
    )
    return host.run()


if __name__ == "__main__":  # pragma: no cover - launched by the browser
    sys.exit(main())
