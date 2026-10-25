"""LOCUS-475: route and identity contract for the staged desktop frontend."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from scripts.desktop_frontend_smoke import validate_home, validate_static


@contextmanager
def _server(
    *,
    status: int = 200,
    version: str | None = "0.10.4",
    content_type: str = "text/html; charset=utf-8",
    payload: bytes = b"<html><body>Locus</body></html>",
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if version is not None:
                self.send_header("X-Locus-Desktop-Build", version)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_exact_installed_build_and_html_are_healthy() -> None:
    with _server() as url:
        validate_home(url, version="0.10.4")
        validate_static(url)


@pytest.mark.parametrize(
    ("version", "content_type", "payload"),
    [
        ("0.4.0", "text/html", b"<html>old UI</html>"),
        ("", "text/html", b"<html>no build marker</html>"),
        ("0.10.4", "application/json", b"<html>invalid content type</html>"),
        ("0.10.4", "text/html", b"not an HTML document"),
    ],
)
def test_wrong_stale_or_non_html_frontend_cannot_pass(
    version: str, content_type: str, payload: bytes,
) -> None:
    with _server(version=version or None, content_type=content_type, payload=payload) as url:
        with pytest.raises(RuntimeError):
            validate_home(url, version="0.10.4")


def test_http_500_does_not_pass_as_a_working_frontend() -> None:
    with _server(status=500) as url:
        with pytest.raises(Exception):
            validate_home(url, version="0.10.4")


def test_missing_static_asset_is_not_ignored() -> None:
    with _server(status=404) as url:
        with pytest.raises(Exception):
            validate_static(url)
