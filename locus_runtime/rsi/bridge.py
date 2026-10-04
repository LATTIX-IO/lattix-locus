"""The stdio bridge between a jailed RSI candidate and its trusted parent (LOCUS-379).

The jailed candidate (:mod:`locus_runtime.rsi.jail`) has **no network at all**
(an AppContainer without capabilities, ``bwrap --unshare-net``, a seatbelt
profile without ``network*``). Everything it needs from outside the jail goes
through this bridge, over the stdin/stdout pipes the parent created when it
launched the child -- no socket, no named object, no loopback exemption:

* ``http`` on channel ``model``: an OpenAI-compatible request the parent relays
  to the metering proxy (:mod:`.metering`), which still meters tokens, watches
  canaries and refuses any other path or model;
* ``http`` on channel ``opa``: an OPA REST call the parent relays to **its own**
  OPA sidecar (the trusted binary with the trusted policy bundle). The
  candidate's gateway evaluates every decision on the real engine; when the
  bridge fails the candidate's engine sees a transport error and denies;
* ``exec``: a command of the candidate's agent, which the parent runs in the
  tool jail (its own AppContainer profile / bwrap / seatbelt, the sample's
  workspace as the only writable root, no network);
* ``git``: the two host-git operations a workspace needs (diff,
  uncommitted-changes), run by the parent with hardened ``GitOps`` on a sealed
  ``.git`` (LOCUS-362: git cannot run inside the AppContainer).

Wire format: a 4-byte big-endian length followed by a UTF-8 JSON object.
Requests are ``{"id", "op", "payload"}``; responses ``{"id", "ok", "result"}``
or ``{"id", "ok": false, "error"}``. Every request is validated by the parent;
the child is untrusted and nothing it sends is executed outside a jail.

This module imports only the standard library at import time (the child may be
started on a stdlib-only runtime for the isolation probe); ``httpx`` and the
harness are imported where they are used.
"""

from __future__ import annotations

import base64
import json
import struct
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import IO, Any

PROTOCOL = 1
#: Largest frame either side accepts (a model response with a long completion).
MAX_FRAME_BYTES = 48 * 1024 * 1024
_HEADER = struct.Struct(">I")
#: Request headers the bridge carries for an ``http`` call (nothing else, never auth).
HTTP_HEADERS = ("content-type", "accept")
Handler = Callable[[dict[str, Any]], dict[str, Any]]


class BridgeError(RuntimeError):
    """The bridge is closed, a frame is malformed or the parent refused a request."""


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #
def encode_frame(message: Mapping[str, Any]) -> bytes:
    body = json.dumps(dict(message), separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise BridgeError(f"frame too large ({len(body)} bytes)")
    return _HEADER.pack(len(body)) + body


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: IO[bytes]) -> dict[str, Any] | None:
    """The next message, or ``None`` at a clean end of stream."""
    header = _read_exact(stream, _HEADER.size)
    if header is None:
        return None
    (size,) = _HEADER.unpack(header)
    if size > MAX_FRAME_BYTES:
        raise BridgeError(f"frame too large ({size} bytes)")
    body = _read_exact(stream, size)
    if body is None:
        raise BridgeError("truncated frame")
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BridgeError("malformed frame") from exc
    if not isinstance(message, dict):
        raise BridgeError("frame is not an object")
    return message


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(text: Any) -> bytes:
    if not isinstance(text, str):
        raise BridgeError("body is not base64 text")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise BridgeError("body is not valid base64") from exc


# --------------------------------------------------------------------------- #
# Child side
# --------------------------------------------------------------------------- #
class BridgeClient:
    """Thread-safe request/response client (used inside the jailed candidate)."""

    def __init__(self, reader: IO[bytes], writer: IO[bytes]) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._closed = ""
        self._thread = threading.Thread(target=self._read_loop, name="rsi-bridge", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        reason = "the parent closed the bridge"
        try:
            while True:
                message = read_frame(self._reader)
                if message is None:
                    break
                ident = message.get("id")
                with self._lock:
                    slot = self._pending.pop(ident, None) if isinstance(ident, int) else None
                if slot is not None:
                    slot[1].append(message)
                    slot[0].set()
        except (BridgeError, OSError, ValueError) as exc:
            reason = f"bridge read failed ({type(exc).__name__})"
        with self._lock:
            self._closed = reason
            pending, self._pending = self._pending, {}
        for event, _box in pending.values():
            event.set()

    def call(
        self, op: str, payload: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        """Send one request and wait for its response (raises :class:`BridgeError`)."""
        event = threading.Event()
        box: list[dict[str, Any]] = []
        with self._lock:
            if self._closed:
                raise BridgeError(self._closed)
            self._next_id += 1
            ident = self._next_id
            self._pending[ident] = (event, box)
        frame = encode_frame({"id": ident, "op": op, "payload": dict(payload or {})})
        try:
            with self._write_lock:
                self._writer.write(frame)
                self._writer.flush()
        except (OSError, ValueError) as exc:
            with self._lock:
                self._pending.pop(ident, None)
            raise BridgeError(f"bridge write failed ({type(exc).__name__})") from exc
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(ident, None)
            raise BridgeError(f"bridge call {op!r} timed out")
        if not box:
            raise BridgeError(self._closed or "the bridge closed")
        response = box[0]
        if response.get("ok") is not True:
            raise BridgeError(str(response.get("error") or "the parent refused the request")[:500])
        result = response.get("result")
        if not isinstance(result, dict):
            raise BridgeError("malformed bridge response")
        return result

    def close(self) -> None:
        try:
            self._writer.close()
        except OSError:
            pass


def http_transport(client: BridgeClient, channel: str, *, timeout: float = 900.0) -> Any:
    """An ``httpx.BaseTransport`` that sends every request over the bridge on
    ``channel`` (``model`` or ``opa``). The URL's host is ignored by the parent:
    a channel reaches exactly one trusted upstream. Failures surface as
    ``httpx.ConnectError`` so callers fail as they would on a dead endpoint
    (the OPA engine then denies)."""
    import httpx

    class BridgeTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            body = request.read()
            headers = {
                k.lower(): v for k, v in request.headers.items() if k.lower() in HTTP_HEADERS
            }
            try:
                result = client.call(
                    "http",
                    {
                        "channel": channel,
                        "method": request.method,
                        "path": request.url.raw_path.decode("ascii", errors="replace"),
                        "headers": headers,
                        "body": b64encode(body),
                    },
                    timeout=timeout,
                )
                status = int(result.get("status") or 502)
                content = b64decode(result.get("body") or "")
            except (BridgeError, TypeError, ValueError) as exc:
                raise httpx.ConnectError(f"rsi bridge: {exc}", request=request) from exc
            out_headers = {"content-type": str(result.get("content_type") or "application/json")}
            return httpx.Response(status, headers=out_headers, content=content, request=request)

    return BridgeTransport()


class BridgeHostGit:
    """:class:`~locus_runtime.harness.workspace.HostGit` answered by the parent."""

    def __init__(self, client: BridgeClient) -> None:
        self._client = client

    def diff(self, base: str, pathspecs: Any) -> str:
        result = self._client.call(
            "git", {"call": "diff", "base": str(base), "pathspecs": [str(p) for p in pathspecs]}
        )
        return str(result.get("diff") or "")

    def has_uncommitted_changes(self) -> bool:
        result = self._client.call("git", {"call": "has_uncommitted_changes"})
        return bool(result.get("changes"))


def bridged_executor(
    client: BridgeClient,
    root: str,
    *,
    gateway_session: Any = None,
    jail: Mapping[str, Any] | None = None,
    shell: str = "sh",
) -> Any:
    """An executor whose process execution happens in the parent's tool jail.

    File operations stay in this (jailed) process, gated by the same session, on
    the workspace the candidate was granted. ``jail`` are the parent's facts for
    the tool jail (tool_jail decides on them exactly as for a local jail)."""
    from pathlib import Path

    from locus_runtime import telemetry
    from locus_runtime.gateway import JailFacts
    from locus_runtime.harness.executor import (
        ExecResult,
        LocalDirectExecutor,
        _blocked_result,
        _GatedExecutor,
    )

    facts = JailFacts(**{k: v for k, v in dict(jail or {}).items() if isinstance(k, str)})

    class BridgedExecutor(_GatedExecutor):
        backend = "rsi-bridge"

        def __init__(self) -> None:
            self.root = Path(root).expanduser().resolve()
            self.gateway_session = gateway_session
            self._direct = LocalDirectExecutor(self.root, gateway_session=gateway_session)

        def jail_facts(self) -> JailFacts:
            return facts

        def workdir(self) -> str:
            return str(self.root)

        def run_shell(self, script: str, *, timeout: int = 60) -> ExecResult:
            flag = "-c" if shell == "sh" else "-lc"
            return self.run([shell, flag, script], timeout=timeout)

        def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
            decision = self._gate("process_exec", str(self.root), command=command)
            if not decision.allowed:
                return _blocked_result(decision, self.backend)
            with self._exec_span(command) as span:
                result = self._spawn(command, timeout=timeout)
                telemetry.record_exec_result(span, result)
            return result

        def _spawn(self, command: list[str], *, timeout: int) -> ExecResult:
            try:
                out = client.call(
                    "exec",
                    {"command": [str(c) for c in command], "timeout": int(timeout)},
                    timeout=float(timeout) + 120.0,
                )
            except BridgeError as exc:
                return ExecResult(
                    exit_code=126,
                    stdout="",
                    stderr=f"[rsi bridge] {exc}",
                    duration_seconds=0.0,
                    backend=self.backend,
                )
            return ExecResult(
                exit_code=int(out.get("exit_code", 1)),
                stdout=str(out.get("stdout") or ""),
                stderr=str(out.get("stderr") or ""),
                duration_seconds=float(out.get("duration_seconds") or 0.0),
                timed_out=bool(out.get("timed_out")),
                backend=str(out.get("backend") or self.backend),
            )

        def allows(self, path: str) -> bool:
            return self._direct.allows(path)

        def read_file(self, path: str) -> str | None:
            return self._direct.read_file(path)

        def write_file(self, path: str, content: str) -> None:
            self._direct.write_file(path, content)

        def exists(self, path: str) -> bool:
            return self._direct.exists(path)

    return BridgedExecutor()


# --------------------------------------------------------------------------- #
# Parent side
# --------------------------------------------------------------------------- #
class BridgeServer:
    """Serves one child's requests with ``handlers`` (op name -> function).

    Requests are handled on a small thread pool, so a long model call does not
    block policy decisions. A malformed frame stops the server (fail closed: the
    child's requests then fail). It never logs request content."""

    def __init__(
        self,
        reader: IO[bytes],
        writer: IO[bytes],
        handlers: Mapping[str, Handler],
        *,
        workers: int = 4,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._handlers = dict(handlers)
        self._write_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="rsi-br")
        self._thread = threading.Thread(target=self._serve, name="rsi-bridge-srv", daemon=True)
        self.counts: dict[str, int] = {}
        self.refused = 0
        self.error = ""

    def start(self) -> BridgeServer:
        self._thread.start()
        return self

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)
        self._pool.shutdown(wait=True, cancel_futures=True)

    def _send(self, message: Mapping[str, Any]) -> None:
        try:
            frame = encode_frame(message)
        except (BridgeError, TypeError, ValueError):
            frame = encode_frame(
                {"id": message.get("id"), "ok": False, "error": "response not serializable"}
            )
        try:
            with self._write_lock:
                self._writer.write(frame)
                self._writer.flush()
        except (OSError, ValueError):
            pass  # the child is gone

    def _serve(self) -> None:
        try:
            while True:
                message = read_frame(self._reader)
                if message is None:
                    return
                self._pool.submit(self._dispatch, message)
        except (BridgeError, OSError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            try:
                self._writer.close()
            except OSError:
                pass

    def _dispatch(self, message: dict[str, Any]) -> None:
        ident = message.get("id")
        op = message.get("op")
        payload = message.get("payload")
        if not isinstance(ident, int) or not isinstance(op, str) or not isinstance(payload, dict):
            self.refused += 1
            self._send(
                {"id": ident if isinstance(ident, int) else -1, "ok": False, "error": "bad request"}
            )
            return
        handler = self._handlers.get(op)
        if handler is None:
            self.refused += 1
            self._send({"id": ident, "ok": False, "error": f"unknown op {op[:40]!r}"})
            return
        self.counts[op] = self.counts.get(op, 0) + 1
        try:
            result = handler(payload)
        except BridgeError as exc:
            self.refused += 1
            self._send({"id": ident, "ok": False, "error": str(exc)[:500]})
            return
        except Exception as exc:  # noqa: BLE001 - a handler crash is a refused request
            self._send({"id": ident, "ok": False, "error": f"{op} failed ({type(exc).__name__})"})
            return
        self._send({"id": ident, "ok": True, "result": result})
