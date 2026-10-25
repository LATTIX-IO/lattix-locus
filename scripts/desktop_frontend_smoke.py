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


def validate_rendered_ui(base: str, *, browser_channel: str) -> None:
    """Run real React in Windows Edge: HTTP 200 alone is insufficient.

    Exercise both normal web rendering and a simulated Tauri global. The test
    intentionally has no backend; the visible session gate must still render.
    Browser smoke cannot replace installed Tauri/WebView2 acceptance.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=True)
        try:
            for desktop in (False, True):
                context = browser.new_context()
                try:
                    if desktop:
                        context.add_init_script('''window.__TAURI__ = {
                          core: { invoke: async () => null },
                          app: { getVersion: async () => "0.10.0" },
                          event: { listen: async () => () => {} }
                        };''')
                    page = context.new_page()
                    errors: list[str] = []
                    missing_chunks: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)[:450]))
                    page.on(
                        "response",
                        lambda response: missing_chunks.append(
                            str(response.status) + ": " + response.url.split("?")[0][-180:]
                        ) if "/_next/static/" in response.url and response.status >= 400 else None,
                    )
                    page.goto(base + "/home", wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_function(
                        '''() => {
                          const main = document.querySelector("main");
                          if (!main) return false;
                          const content = main.innerText || "";
                          return content.includes("Starting Locus") ||
                            content.includes("Can't reach the Locus backend") ||
                            content.includes("Opening Home") ||
                            content.includes("Skip to content");
                        }''',
                        timeout=25000,
                    )
                    page.wait_for_timeout(1500)
                    main = page.locator("main")
                    if not main.is_visible() or not main.inner_text().strip():
                        raise RuntimeError("browser rendered an empty/invisible main element")
                    if "The interface could not load" in page.locator("body").inner_text():
                        raise RuntimeError("React root global-error boundary rendered")
                    if errors or missing_chunks:
                        raise RuntimeError(
                            "browser frontend errors: "
                            + "; ".join((errors + missing_chunks)[:8])
                        )
                    print(
                        "Desktop frontend rendered in Edge: "
                        + ("simulated Tauri" if desktop else "browser")
                        + ", visible main (" + str(len(main.inner_text())) + " chars), 0 JS faults"
                    )
                finally:
                    context.close()
        finally:
            browser.close()


def smoke(
    frontend: Path, *, node: str, version: str,
    deadline_seconds: float = 50.0, browser_channel: str | None = None,
) -> None:
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
                    if browser_channel:
                        validate_rendered_ui(base, browser_channel=browser_channel)
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
    parser.add_argument(
        "--browser-channel", choices=("msedge", "chrome", "chromium"),
        default=None, help="render/hydrate real React UI and reject blank WebViews",
    )
    options = parser.parse_args()
    if not options.version or len(options.version) > 64:
        parser.error("a bounded, nonempty version is required")
    smoke(
        options.frontend.resolve(), node=options.node, version=options.version,
        browser_channel=options.browser_channel,
    )


if __name__ == "__main__":
    main()
