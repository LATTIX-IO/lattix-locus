"""LOCUS-475: exercise the actual staged Next.js standalone desktop bundle.

Called on both Windows and macOS *before* building the Tauri installer.
No backend, auth token, or external network is needed: /home is a React shell.
The check proves that the packaged server and static JS assets are reachable
and identify the exact desktop version the native shell will enforce.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def choose_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def validate_home(
    url: str,
    *,
    version: str,
    timeout: float = 3.0,
) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"frontend /home returned HTTP {response.status}")
        announced = response.headers.get("X-Locus-Desktop-Build")
        if announced != version:
            raise RuntimeError(
                f"frontend build mismatch: expected {version!r}, found {announced!r}"
            )
        if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
            raise RuntimeError("frontend /home is not HTML")
        sample = response.read(128 * 1024)
        if b"<html" not in sample.lower():
            raise RuntimeError("frontend /home did not render an HTML document")


def validate_static(url: str, *, timeout: float = 3.0) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200 or not response.read(128):
            raise RuntimeError("packaged static chunk is missing or empty")


def smoke(frontend: Path, *, node: str, version: str, deadline_seconds: float = 50.0) -> None:
    server = frontend / "server.js"
    static = frontend / ".next" / "static"
    if not server.is_file():
        raise RuntimeError(f"missing standalone server: {server}")
    if not static.is_dir():
        raise RuntimeError(f"missing packaged _next/static: {static}")
    chunks = sorted(static.rglob("*.js"))
    if not chunks:
        raise RuntimeError(f"no packaged JavaScript chunks under: {static}")
    port = choose_loopback_port()
    env = {
        **os.environ,
        "HOSTNAME": "127.0.0.1",
        "PORT": str(port),
        "LOCUS_BACKEND_PROXY_URL": "http://127.0.0.1:8000",
        # The identity must be compiled into the Next headers; setting it in
        # the process environment must not make a wrongly built bundle pass.
        "LOCUS_DESKTOP_BUILD_VERSION": "runtime-override-must-not-win",
    }
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        proc = subprocess.Popen(
            [node, str(server)],
            cwd=str(frontend),
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + deadline_seconds
            last_error = "frontend never responded"
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    last_error = f"standalone server exited ({proc.returncode})"
                    break
                try:
                    validate_home(f"{base}/home", version=version)
                    relative = chunks[0].relative_to(static).as_posix()
                    validate_static(f"{base}/_next/static/{relative}")
                    print(
                        f"Desktop frontend HTTP acceptance passed: build {version}, "
                        f"HTML /home, static /_next/static/{relative}"
                    )
                    return
                except (OSError, RuntimeError, urllib.error.URLError, TimeoutError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.5)
            output.seek(0)
            logs = output.read()[-4096:]
            raise RuntimeError(f"desktop frontend smoke failed: {last_error}\n{logs}")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=8)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--version", required=True)
    options = parser.parse_args()
    if not options.version or len(options.version) > 64:
        parser.error("a bounded, nonempty version is required")
    smoke(options.frontend.resolve(), node=options.node, version=options.version)


if __name__ == "__main__":
    main()
