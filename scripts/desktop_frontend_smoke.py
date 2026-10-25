"""LOCUS-475: exercise the actual staged Next.js standalone desktop bundle.

Called on both Windows and macOS *before* building the Tauri installer.
No backend, auth token, or external network is needed: /home is a React shell.
The check proves that the packaged server and static JS assets are reachable
and identify the exact desktop version the native shell will enforce.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit


def choose_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def validate_home(
    url: str,
    *,
    version: str,
    timeout: float = 3.0,
) -> bytes:
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
        # Reading only the first 128 KiB can miss route-specific client chunks.
        # Bounded HTML is required so every referenced initial asset can be checked.
        sample = response.read(2 * 1024 * 1024 + 1)
        if len(sample) > 2 * 1024 * 1024:
            raise RuntimeError("frontend /home HTML exceeds 2 MiB diagnostic limit")
        if b"<html" not in sample.lower():
            raise RuntimeError("frontend /home did not render an HTML document")
        return sample


def validate_static(
    url: str, *, timeout: float = 3.0, expected_type: str | None = None,
) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200 or not response.read(128):
            raise RuntimeError("packaged static chunk is missing or empty")
        if expected_type:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            acceptable = (
                {"application/javascript", "text/javascript"} if expected_type == "js"
                else {"text/css"}
            )
            if content_type not in acceptable:
                raise RuntimeError(
                    f"static {expected_type} response has wrong content type: {content_type!r}"
                )


# Check the page's own initial assets. A single arbitrary chunk succeeding is
# not evidence that the app's required CSS/JS exists: owner v0.10.4 had 6
# missing assets among the first 12, despite a 200 /home response.
_ASSET_REF = re.compile(
    r"/_next/static/[A-Za-z0-9._~%+/-]+?\.(?:js|css)(?:\?[A-Za-z0-9_.~%=&+-]*)?",
    flags=re.IGNORECASE,
)


def referenced_assets(html_body: bytes) -> list[str]:
    document = html.unescape(html_body.decode("utf-8", errors="replace"))
    return sorted(set(_ASSET_REF.findall(document)))


def validate_referenced_assets(
    base: str, html_body: bytes, static: Path, *, timeout: float = 3.0,
) -> int:
    assets = referenced_assets(html_body)
    if not assets:
        raise RuntimeError("frontend /home references no Next.js JS/CSS assets")
    if len(assets) > 512:
        raise RuntimeError("frontend /home references an unexpected number of assets")
    for reference in assets:
        raw_path = urlsplit(reference).path
        path = unquote(raw_path)
        if not path.startswith("/_next/static/"):
            raise RuntimeError("invalid Next.js asset reference")
        relative = path[len("/_next/static/"):]
        segments = relative.split("/")
        if not relative or any(part in ("", ".", "..") for part in segments) or "\\" in relative:
            raise RuntimeError("unsafe Next.js asset reference")
        expected_type = "js" if relative.lower().endswith(".js") else "css"
        installed_file = static.joinpath(*segments)
        if not installed_file.is_file() or installed_file.stat().st_size == 0:
            raise RuntimeError(f"referenced Next.js asset not packaged: {path}")
        try:
            validate_static(
                base + reference, timeout=timeout, expected_type=expected_type,
            )
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            raise RuntimeError(
                f"referenced Next.js asset cannot load: {path} ({type(exc).__name__}: {exc})"
            ) from exc
    return len(assets)


def validate_rendered_ui(base: str, *, browser_channel: str) -> None:
    """Prove authenticated Home renders in real Edge, not just the splash.

    LOCUS-475/v0.10.5: earlier smoke accepted "Starting Locus" or a backend
    error as success. Both can appear while the actual Home route is unusable.
    Run the installed frontend with a *synthetic* authenticated API response
    and require real Home content and a task composer. No provider credentials,
    real backend, or production API mutations are used. This remains distinct
    from full installed Windows UAT.
    """
    from playwright.sync_api import sync_playwright

    session = {
        "authenticated": True,
        "actor": "smoke-operator",
        "principal_id": "smoke-operator",
        "principal_type": "user",
        "display_name": "Smoke Operator",
        "subject": "smoke-operator",
        "roles": ["owner"],
        "auth_mode": "local-desktop",
        "capabilities": {"can_admin": True, "can_builder": True},
        "allowed_modes": ["user"],
        "default_mode": "user",
        "oidc": {"configured": False, "issuer": "", "audience": "", "provider": ""},
    }

    def synthetic_backend(route: object) -> None:
        # The route handler is installed only in the isolated headless smoke.
        # Do not turn this into a production auth bypass or persist the session.
        path = urlsplit(route.request.url).path
        responses: dict[str, object] = {
            "/api/auth/session": session,
            "/api/workflow-runs": [],
            "/api/agent-definitions": [],
            "/api/playbooks": [],
            "/api/workflows/published": [],
            "/api/platform/version": {"version": "smoke-only"},
            "/api/platform/settings": {},
            "/api/healthz/details": {"postgres": "connected"},
        }
        if path in responses:
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(responses[path]),
            )
        else:
            # Unknown endpoints must not silently return privileged success.
            route.fulfill(
                status=404, content_type="application/json",
                body='{"detail":"synthetic smoke endpoint not implemented"}',
            )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=True)
        try:
            for desktop in (False, True):
                context = browser.new_context()
                try:
                    # Avoid a first-run modal obscuring whether the actual Home
                    # component paints. This is a test-only browser context.
                    context.add_init_script(
                        "window.localStorage.setItem('locus-first-run-complete', '1');"
                    )
                    if desktop:
                        context.add_init_script('''window.__TAURI__ = {
                          core: { invoke: async () => null },
                          app: { getVersion: async () => "smoke-only" },
                          event: { listen: async () => () => {} }
                        };''')
                    context.route("**/api/**", synthetic_backend)
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
                    # A splash/session gate is NOT accepted as Home. In
                    # particular, "Starting Locus" alone made v0.10.5 green.
                    page.get_by_role(
                        "heading", name="What should Locus do?", exact=True,
                    ).wait_for(state="visible", timeout=25000)
                    page.get_by_role(
                        "button", name="Start task", exact=True,
                    ).wait_for(state="visible", timeout=10000)
                    page.wait_for_timeout(800)
                    main = page.locator("main#main-content")
                    if not main.is_visible() or "What should Locus do?" not in main.inner_text():
                        raise RuntimeError("authenticated Home component did not render")
                    body_text = page.locator("body").inner_text()
                    if "The interface could not load" in body_text:
                        raise RuntimeError("React root global-error boundary rendered")
                    if errors or missing_chunks:
                        raise RuntimeError(
                            "browser frontend errors: "
                            + "; ".join((errors + missing_chunks)[:8])
                        )
                    print(
                        "Desktop frontend authenticated Home rendered in Edge: "
                        + ("simulated Tauri" if desktop else "browser")
                        + ", real task composer, 0 JS faults"
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
                    html_body = validate_home(f"{base}/home", version=version)
                    asset_count = validate_referenced_assets(base, html_body, static)
                    print(
                        f"Desktop frontend HTTP acceptance passed: build {version}, "
                        f"HTML /home and all {asset_count} referenced JS/CSS assets"
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
