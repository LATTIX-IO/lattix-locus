"""Loopback forward proxy that enforces the agent browser's egress allowlist.

Playwright request interception (``BrowserContext.route``) does not see every
request: redirect hops are followed by the network stack without calling the
route handler, and WebSockets use a separate path. So the agent browser is
launched with this proxy as its only route out (``--proxy-server``, loopback
not bypassed) and every connection -- page loads, redirects, subresources,
workers, WebSockets -- is authorized by host before a byte goes upstream.

* ``CONNECT host:port`` (HTTPS / WSS): the host is authorized, then the TLS
  stream is tunnelled untouched (no interception, no certificates).
* ``GET http://host/...`` (plain HTTP / WS): the host is authorized, the request
  line is rewritten to origin form and the connection is pumped to that host
  only; a keep-alive connection can never be re-pointed at another host.
* Anything else (malformed request, other schemes, panic latched) → ``403``.

Authorization is a callback (the agent browser passes one that calls the
gateway with a ``network_egress`` action, so every allowed and every blocked
connection is audited). It binds to 127.0.0.1 on an ephemeral port only.

P30: the alternatives were mitmproxy (MIT; full TLS interception -- far more
than a host allowlist needs, and a CA to manage) and Chromium's
``--host-resolver-rules`` (static at launch, no audit). A ~150-line CONNECT
filter is the smaller trusted surface.
"""

from __future__ import annotations

import logging
import selectors
import socket
import socketserver
import threading
from collections.abc import Callable
from urllib import parse as urlparse

logger = logging.getLogger(__name__)

HostAuthorizer = Callable[[str, int], bool]

_MAX_HEAD = 64 * 1024
_IDLE_TIMEOUT = 60.0
_CONNECT_TIMEOUT = 10.0
_FORBIDDEN = (
    b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: 30\r\n"
    b"Connection: close\r\n\r\nblocked by Locus egress policy"
)
_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def _read_head(sock: socket.socket) -> tuple[bytes, bytes] | None:
    """Read up to the end of the request head; returns (head, already-read body)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > _MAX_HEAD:
            return None
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _split_host_port(value: str, default_port: int) -> tuple[str, int] | None:
    text = value.strip()
    if text.startswith("["):  # [v6]:port
        host, _, tail = text[1:].partition("]")
        port_text = tail[1:] if tail.startswith(":") else ""
    else:
        host, sep, port_text = text.rpartition(":")
        if not sep:
            host, port_text = text, ""
    try:
        port = int(port_text) if port_text else default_port
    except ValueError:
        return None
    if not host or not 0 < port < 65536:
        return None
    return host.lower(), port


def _pump(a: socket.socket, b: socket.socket, stop: threading.Event) -> None:
    selector = selectors.DefaultSelector()
    selector.register(a, selectors.EVENT_READ, b)
    selector.register(b, selectors.EVENT_READ, a)
    try:
        while not stop.is_set():
            events = selector.select(timeout=_IDLE_TIMEOUT)
            if not events:
                return
            for key, _ in events:
                src: socket.socket = key.fileobj  # type: ignore[assignment]
                dst: socket.socket = key.data
                try:
                    chunk = src.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                try:
                    dst.sendall(chunk)
                except OSError:
                    return
    finally:
        selector.close()


class _Handler(socketserver.BaseRequestHandler):
    server: _ProxyServer

    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(_CONNECT_TIMEOUT)
        upstream: socket.socket | None = None
        try:
            got = _read_head(client)
            if got is None:
                return
            head, rest = got
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3:
                client.sendall(_BAD_REQUEST)
                return
            method, target, version = parts
            if method.upper() == "CONNECT":
                hostport = _split_host_port(target, 443)
                if hostport is None:
                    client.sendall(_BAD_REQUEST)
                    return
                if not self.server.authorize(*hostport):
                    client.sendall(_FORBIDDEN)
                    return
                upstream = socket.create_connection(hostport, timeout=_CONNECT_TIMEOUT)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                url = urlparse.urlsplit(target)
                if url.scheme.lower() != "http" or not url.hostname:
                    client.sendall(_FORBIDDEN)
                    return
                host, port = url.hostname.lower(), url.port or 80
                if not self.server.authorize(host, port):
                    client.sendall(_FORBIDDEN)
                    return
                path = url.path or "/"
                if url.query:
                    path += "?" + url.query
                headers = [
                    line
                    for line in lines[1:]
                    if not line.lower().startswith(("proxy-connection:", "proxy-authorization:"))
                ]
                new_head = "\r\n".join([f"{method} {path} {version}", *headers]) + "\r\n\r\n"
                try:
                    upstream = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
                except OSError:
                    client.sendall(_BAD_GATEWAY)
                    return
                upstream.sendall(new_head.encode("latin-1") + rest)
            client.settimeout(None)
            upstream.settimeout(None)
            _pump(client, upstream, self.server.stopping)
        except OSError:
            try:
                client.sendall(_BAD_GATEWAY)
            except OSError:
                pass
        except Exception:  # noqa: BLE001 - a proxy error closes that connection only
            logger.exception("computer_use.egress_proxy_error")
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass


class _ProxyServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, authorize: HostAuthorizer) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self._authorize = authorize
        self.stopping = threading.Event()

    def authorize(self, host: str, port: int) -> bool:
        if self.stopping.is_set():
            return False
        try:
            return bool(self._authorize(host, port))
        except Exception:  # noqa: BLE001 - an authorizer failure blocks
            logger.exception("computer_use.egress_authorizer_error")
            return False


class EgressProxy:
    """A running loopback proxy; every upstream connection passes ``authorize``."""

    def __init__(self, authorize: HostAuthorizer) -> None:
        self._server = _ProxyServer(authorize)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="locus-cu-egress-proxy", daemon=True
        )

    def start(self) -> EgressProxy:
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        port = int(self._server.server_address[1])
        return f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.stopping.set()
        self._server.shutdown()
        self._server.server_close()
