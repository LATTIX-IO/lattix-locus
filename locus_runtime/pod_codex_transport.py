"""LOCUS-470/B: bounded Docker-attached Codex frame pump.

Supervisor-side only. The trusted image's runner sends three frame types:
ready, model_request, and done. No raw Codex stdout or model text becomes
audit telemetry. Request servicing occurs on a daemon worker thread so the
outer StageDockerBroker continues its independent guardian/deadline/revocation
checks every 200 ms while loopback Ollama is generating.
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import subprocess
import threading
import time

from locus_runtime.pod_codex_bridge import CodexOllamaBridge, CodexRelayDenied, _frame

_SCHEMA = "locus.codex.stdio.v1"
_VERSION = "0.163.0"
_MAX_FRAME = 2 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class CodexProtocolDenied(CodexRelayDenied):
    """Container protocol unavailable or violated: fail closed."""


class CodexFramePump:
    """A single-use protocol session for one exact, pinned Docker process."""

    def __init__(
        self, process: subprocess.Popen[bytes], bridge: CodexOllamaBridge,
        init: bytes,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise CodexProtocolDenied("attached Docker stdin/stdout required")
        self.process = process
        self.bridge = bridge
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=32)
        self._lock = threading.Lock()
        self._error: str | None = None
        self._ready = False
        self._done = False
        self.terminal_sha256 = ""
        self._pending: threading.Thread | None = None
        self._reader = threading.Thread(target=self._read, daemon=True)
        try:
            process.stdin.write(init + b"\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise CodexProtocolDenied("Docker transport initialization failed") from exc
        self._reader.start()

    def _fail(self, message: str) -> None:
        with self._lock:
            if self._error is None:
                self._error = message

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while True:
                raw = self.process.stdout.readline(_MAX_FRAME + 2)
                if not raw:
                    break
                if not raw.endswith(b"\n") or len(raw) > _MAX_FRAME + 1:
                    self._fail("unbounded or unterminated Codex frame")
                    break
                try:
                    self._queue.put(raw.rstrip(b"\n"), timeout=0.5)
                except queue.Full:
                    self._fail("Codex event backlog exceeded")
                    break
        except (OSError, ValueError):
            self._fail("Codex frame stream failed")
        finally:
            try:
                self._queue.put(None, timeout=0.5)
            except queue.Full:
                self._fail("Codex frame stream not drained")

    def _service(self, frame: bytes) -> None:
        try:
            response = self.bridge.handle(frame)
            assert self.process.stdin is not None
            with self._lock:
                if self._error is not None:
                    return
                self.process.stdin.write(response)
                self.process.stdin.flush()
        except (CodexRelayDenied, OSError, ValueError):
            self._fail("Gateway model transport denied or unavailable")
        except Exception:
            # Unexpected implementation bugs must not silently approve a
            # provider/model tool call, and must not leak exception strings.
            self._fail("Codex model transport failed")

    def poll(self) -> None:
        if self._error:
            raise CodexProtocolDenied(self._error)
        for _ in range(32):
            try:
                raw = self._queue.get_nowait()
            except queue.Empty:
                break
            if raw is None:
                continue
            frame = _frame(raw)
            if frame.get("schema") != _SCHEMA:
                raise CodexProtocolDenied("unexpected Codex protocol version")
            kind = frame.get("kind")
            if kind == "ready":
                if (
                    self._ready or self._done
                    or set(frame) != {"schema", "kind", "version"}
                    or frame.get("version") != _VERSION
                ):
                    raise CodexProtocolDenied("unexpected Codex version or repeated ready")
                self._ready = True
            elif kind == "model_request":
                if not self._ready or self._done:
                    raise CodexProtocolDenied("model call outside active Codex session")
                if self._pending is not None and self._pending.is_alive():
                    raise CodexProtocolDenied("concurrent model request not supported")
                self._pending = threading.Thread(
                    target=self._service, args=(raw,), daemon=True,
                )
                self._pending.start()
            elif kind == "done":
                if self._done or not self._ready or set(frame) not in (
                    {"schema", "kind", "ok", "events"},
                    {"schema", "kind", "ok", "events", "sha256", "stdout_sha256", "bytes"},
                ):
                    raise CodexProtocolDenied("malformed Codex terminal frame")
                if frame.get("ok") is not True:
                    raise CodexProtocolDenied("Codex CLI reported unsuccessful execution")
                digest = frame.get("sha256")
                stdout_digest = frame.get("stdout_sha256")
                amount = frame.get("bytes")
                events = frame.get("events")
                if (
                    not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
                    or not isinstance(stdout_digest, str)
                    or _SHA256.fullmatch(stdout_digest) is None
                    or type(amount) is not int or not 0 < amount <= 65536
                    or type(events) is not int or not 0 < events <= 8192
                    or self._pending is not None and self._pending.is_alive()
                ):
                    raise CodexProtocolDenied("missing bounded Codex terminal evidence")
                self.terminal_sha256 = digest
                self._done = True
            else:
                raise CodexProtocolDenied("unknown Codex protocol frame")
        if self._error:
            raise CodexProtocolDenied(self._error)

    def finish(self) -> str:
        """A successful Docker exit alone is NOT Codex task acceptance."""
        deadline = time.monotonic() + 2
        while not self._done and time.monotonic() < deadline:
            self.poll()
            time.sleep(0.01)
        self.poll()
        if not self._done:
            raise CodexProtocolDenied("Codex exited without verified terminal frame")
        return self.terminal_sha256
