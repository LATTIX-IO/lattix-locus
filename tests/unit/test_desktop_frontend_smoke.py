"""LOCUS-475: route and identity contract for the staged desktop frontend."""
from __future__ import annotations

import threading
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from scripts.desktop_frontend_smoke import (
    referenced_assets,
    validate_home,
    validate_referenced_assets,
    validate_static,
)


@contextmanager
def _server(
    *,
    status: int = 200,
    version: str | None = "0.10.4",
    content_type: str = "text/html; charset=utf-8",
    payload: bytes = b"<html><body>Locus</body></html>",
    asset_responses: dict[str, tuple[int, str, bytes]] | None = None,
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response_status, response_type, response_body = status, content_type, payload
            if asset_responses is not None and self.path.startswith("/_next/static/"):
                response_status, response_type, response_body = asset_responses.get(
                    self.path, (404, "text/plain", b"missing"),
                )
            self.send_response(response_status)
            self.send_header("Content-Type", response_type)
            if version is not None:
                self.send_header("X-Locus-Desktop-Build", version)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

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


def _assets_home() -> bytes:
    return (
        b'<html><head><link rel="stylesheet" href="/_next/static/chunks/app.css">'
        b'</head><body><script src="/_next/static/chunks/runtime.js"></script>'
        b'<script src="/_next/static/chunks/page.js"></script></body></html>'
    )


def _packaged_static(tmp_path: Path) -> Path:
    static = tmp_path / ".next" / "static"
    (static / "chunks").mkdir(parents=True)
    for name in ("app.css", "runtime.js", "page.js"):
        (static / "chunks" / name).write_text("test", encoding="utf-8")
    return static


def _asset_responses() -> dict[str, tuple[int, str, bytes]]:
    return {
        "/_next/static/chunks/app.css": (200, "text/css", b"body{color:#fff}"),
        "/_next/static/chunks/runtime.js": (200, "application/javascript", b"window.x=1"),
        "/_next/static/chunks/page.js": (200, "text/javascript", b"window.y=1"),
    }


def test_all_referenced_css_and_js_must_be_present_and_served(tmp_path: Path) -> None:
    static = _packaged_static(tmp_path)
    with _server(payload=_assets_home(), asset_responses=_asset_responses()) as base:
        html_body = validate_home(base + "/home", version="0.10.4")
        assert validate_referenced_assets(base, html_body, static) == 3


def test_second_or_third_referenced_chunk_404_fails_smoke(tmp_path: Path) -> None:
    static = _packaged_static(tmp_path)
    responses = _asset_responses()
    responses["/_next/static/chunks/page.js"] = (404, "text/plain", b"missing")
    with _server(payload=_assets_home(), asset_responses=responses) as base:
        with pytest.raises(RuntimeError, match="page.js"):
            validate_referenced_assets(base, validate_home(base + "/home", version="0.10.4"), static)


def test_referenced_file_missing_on_disk_fails_smoke(tmp_path: Path) -> None:
    static = _packaged_static(tmp_path)
    (static / "chunks" / "runtime.js").unlink()
    with _server(payload=_assets_home(), asset_responses=_asset_responses()) as base:
        with pytest.raises(RuntimeError, match="not packaged: .*runtime.js"):
            validate_referenced_assets(base, validate_home(base + "/home", version="0.10.4"), static)


def test_referenced_css_wrong_content_type_fails_smoke(tmp_path: Path) -> None:
    static = _packaged_static(tmp_path)
    responses = _asset_responses()
    responses["/_next/static/chunks/app.css"] = (200, "text/html", b"<html>404</html>")
    with _server(payload=_assets_home(), asset_responses=responses) as base:
        with pytest.raises(RuntimeError, match="wrong content type"):
            validate_referenced_assets(base, validate_home(base + "/home", version="0.10.4"), static)


def test_no_referenced_assets_is_not_a_healthy_page(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="references no Next.js"):
        validate_referenced_assets("http://127.0.0.1:3000", b"<html>empty</html>", tmp_path)


def test_asset_reference_deduplication_and_path_rejection(tmp_path: Path) -> None:
    refs = referenced_assets(
        b'<script src="/_next/static/chunks/runtime.js"></script>'
        b'<link href="/_next/static/chunks/runtime.js">'
    )
    assert refs == ["/_next/static/chunks/runtime.js"]
    with pytest.raises(RuntimeError, match="unsafe Next.js"):
        validate_referenced_assets(
            "http://127.0.0.1:3000",
            b'<script src="/_next/static/../evil.js"></script>',
            tmp_path,
        )
