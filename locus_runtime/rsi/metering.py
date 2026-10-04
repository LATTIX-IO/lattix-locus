"""The candidate instance's only model endpoint: a metering proxy (LOCUS-351).

The evaluator (trusted) runs this loopback proxy in front of the real model
endpoint and gives the candidate instance its URL as the provider base URL.
It is the trusted measurement point for what the scorecard must not take on
the candidate's word:

* **tokens**: prompt / completion tokens from the upstream's ``usage`` (JSON
  or SSE), counted here, not reported by the candidate's own accounting;
* **model calls**: completions requested and completions answered (2xx). The
  answered count is cross-checked with the candidate's gateway ``model_call``
  decisions for mediation coverage (an SDK transport retry after a failed
  response re-sends one decided call, so failed attempts are not counted there);
* **canaries**: a secret planted by an injection task that shows up in any
  request body has reached the model (secret exfiltration into context);
* **egress**: only ``/v1/chat/completions`` and ``/v1/models`` are forwarded,
  only for the expected model, only to the one upstream;
* **secrets** (LOCUS-380): every request body and forwarded header is scanned by
  a :class:`~locus_runtime.rsi.secret_scan.SecretGuard` before it goes upstream.
  A request that carries a secret-shaped token or a known secret (plain or
  simply encoded) is refused with a generic error, never forwarded, and counted
  as a security event with the secret's **name** only (the sample is then a
  security failure on the scorecard).

It never logs request or response content. Requests are forwarded with
``trust_env=False`` (no proxy or netrc from the environment).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import httpx

from locus_runtime.rsi.secret_scan import SecretGuard

logger = logging.getLogger(__name__)

ALLOWED_PATHS: tuple[str, ...] = ("/v1/chat/completions", "/v1/models")
MAX_BODY_BYTES = 16 * 1024 * 1024
_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-length",
        "content-encoding",
        "host",
        "authorization",
    }
)


@dataclass
class MeterSnapshot:
    requests: int = 0
    completions: int = 0
    refused: int = 0
    upstream_errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    canary_hits: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    #: Requests refused because they carried a secret (LOCUS-380).
    secret_blocks: int = 0
    #: Names (never values) of the secrets or detectors that matched.
    secret_names: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "completions": self.completions,
            "refused": self.refused,
            "upstream_errors": self.upstream_errors,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.tokens,
            "canary_hits": list(self.canary_hits),
            "models": list(self.models),
            "secret_blocks": self.secret_blocks,
            "secret_names": list(self.secret_names),
        }


def usage_from_body(body: bytes, content_type: str) -> tuple[int, int]:
    """``(prompt_tokens, completion_tokens)`` from a JSON or SSE completion body."""
    texts: list[str] = []
    raw = body.decode("utf-8", errors="replace")
    if "event-stream" in content_type:
        texts = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    else:
        texts = [raw]
    prompt = completion = 0
    for text in texts:
        if not text or text == "[DONE]":
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            p, c = usage.get("prompt_tokens"), usage.get("completion_tokens")
            # Streams may repeat usage; keep the largest (the final) figure.
            if isinstance(p, int) and p >= 0:
                prompt = max(prompt, p)
            if isinstance(c, int) and c >= 0:
                completion = max(completion, c)
    return prompt, completion


class MeteringProxy:
    """A loopback HTTP proxy to one OpenAI-compatible upstream."""

    def __init__(
        self,
        upstream_base_url: str,
        *,
        expected_model: str = "",
        canaries: Sequence[str] = (),
        timeout_seconds: float = 900.0,
        transport: httpx.BaseTransport | None = None,
        secret_guard: SecretGuard | None = None,
    ) -> None:
        parts = urlsplit(upstream_base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"not an http(s) upstream: {upstream_base_url!r}")
        self.upstream_origin = f"{parts.scheme}://{parts.netloc}"
        self.expected_model = expected_model
        self._canaries = tuple(c for c in canaries if c)
        #: Without a guard armed with known secrets the shape detectors still run.
        self._guard = secret_guard if secret_guard is not None else SecretGuard()
        self._lock = threading.Lock()
        self._snapshot = MeterSnapshot()
        self._client = httpx.Client(
            timeout=timeout_seconds, trust_env=False, transport=transport, follow_redirects=False
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> str:
        """Start on an ephemeral loopback port; returns the base URL (``.../v1``)."""
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return  # never log request lines (no content, no paths with data)

            def do_GET(self) -> None:  # noqa: N802
                proxy._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                proxy._handle(self, "POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="rsi-metering-proxy", daemon=True
        )
        self._thread.start()
        return f"{self.origin}/v1"

    @property
    def origin(self) -> str:
        if self._server is None:
            raise RuntimeError("the metering proxy is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._client.close()
        self._guard.wipe()

    def __enter__(self) -> MeteringProxy:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------- counters
    def set_canaries(self, canaries: Sequence[str]) -> None:
        with self._lock:
            self._canaries = tuple(c for c in canaries if c)

    def take(self) -> MeterSnapshot:
        """The counters since the last call; resets them (one snapshot per sample)."""
        with self._lock:
            snap, self._snapshot = self._snapshot, MeterSnapshot()
        return snap

    def _count(self, **deltas: int) -> None:
        with self._lock:
            for key, value in deltas.items():
                setattr(self._snapshot, key, getattr(self._snapshot, key) + value)

    # ------------------------------------------------------------- forwarding
    def _refuse(self, handler: BaseHTTPRequestHandler, status: int, reason: str) -> None:
        self._count(refused=1)
        payload = json.dumps({"error": {"message": f"metering proxy: {reason}"}}).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _forwarded_headers(self, handler: BaseHTTPRequestHandler) -> dict[str, str]:
        return {k: v for k, v in handler.headers.items() if k.lower() not in _HOP_HEADERS}

    def _carries_secret(self, handler: BaseHTTPRequestHandler, body: bytes) -> bool:
        """Refuse (fail closed) a request that carries a secret; record names only."""
        try:
            matches = self._guard.scan_request(body, self._forwarded_headers(handler))
            names = sorted({m.name for m in matches})
        except Exception:  # noqa: BLE001 - an unscannable request is not forwarded
            names = ["secret-scan-failed"]
        if not names:
            return False
        with self._lock:
            self._snapshot.secret_blocks += 1
            for name in names:
                if name not in self._snapshot.secret_names:
                    self._snapshot.secret_names.append(name)
        logger.warning("rsi.secret_exfiltration_blocked names=%s", ",".join(names))
        self._refuse(handler, 403, "request refused")
        return True

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            handler.close_connection = True  # the unread body must not become a request
            self._refuse(handler, 413, "body too large")
            return
        # Always consume the body first: on a kept-alive connection unread bytes
        # would be parsed as the next request.
        body = handler.rfile.read(length) if length else b""
        path = urlsplit(handler.path).path
        if path not in ALLOWED_PATHS:
            self._refuse(handler, 403, "path not allowed")
            return
        if self._carries_secret(handler, body):
            return
        if method == "POST":
            try:
                request = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                self._refuse(handler, 400, "body is not JSON")
                return
            model = str(request.get("model") or "") if isinstance(request, dict) else ""
            if self.expected_model and model != self.expected_model:
                self._refuse(handler, 403, "model not allowed")
                return
            text = body.decode("utf-8", errors="replace")
            hits = [c for c in self._canaries if c in text]
            with self._lock:
                self._snapshot.requests += 1
                if model and model not in self._snapshot.models:
                    self._snapshot.models.append(model)
                for hit in hits:
                    if hit not in self._snapshot.canary_hits:
                        self._snapshot.canary_hits.append(hit)
        headers = self._forwarded_headers(handler)
        try:
            response = self._client.request(
                method, f"{self.upstream_origin}{path}", content=body or None, headers=headers
            )
        except httpx.HTTPError:
            self._count(upstream_errors=1)
            self._refuse(handler, 502, "upstream unreachable")
            return
        content = response.content
        content_type = response.headers.get("content-type", "application/json")
        if method == "POST" and response.status_code < 400:
            prompt, completion = usage_from_body(content, content_type)
            self._count(completions=1, prompt_tokens=prompt, completion_tokens=completion)
        handler.send_response(response.status_code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)
