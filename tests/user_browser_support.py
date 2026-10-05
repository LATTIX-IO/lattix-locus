"""Test harness for the user-browser driver (LOCUS-350). Test-only.

:class:`ExtensionPump` plays the native-messaging host without touching the
registry or any real browser profile: it launches Playwright Chromium on a
**temporary** persistent profile with the unpacked Locus extension loaded,
admits itself to a :class:`RelayHub` with a test pairing key (exactly as the
host does), and pumps commands into the extension's background worker through
the same entry point the native port uses (``globalThis.locus.onHostMessage``).

Playwright's sync API is bound to the thread that started it, so the pump owns
Chromium on its own thread; the test drives the driver from the main thread.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from locus_runtime.computer_use.user_browser.pairing import CHROMIUM_ORIGIN, EXTENSION_DIR
from locus_runtime.computer_use.user_browser.relay import RelayHub

TEST_PAIRING_KEY = "test-pairing-key-not-a-secret"


class ExtensionUnavailable(RuntimeError):
    pass


class ExtensionPump(threading.Thread):
    def __init__(self, hub: RelayHub, profile_dir: Path, *, key: str = TEST_PAIRING_KEY) -> None:
        super().__init__(name="locus-extension-pump", daemon=True)
        self.hub = hub
        self.profile_dir = profile_dir
        self.key = key
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.client_id = ""
        self._token = ""
        self._stop_event = threading.Event()
        self._controls: queue.Queue[tuple[Callable[..., Any], queue.Queue[Any]]] = queue.Queue()
        self.delivered: list[dict[str, Any]] = []

    # -- main-thread API -------------------------------------------------------
    def start_and_wait(self, timeout: float = 60.0) -> None:
        self.start()
        if not self.ready.wait(timeout):
            raise ExtensionUnavailable("extension pump did not start in time")
        if self.error is not None:
            raise ExtensionUnavailable(str(self.error))

    def control(self, fn: Callable[[Any, Any], Any], timeout: float = 30.0) -> Any:
        """Run ``fn(context, worker)`` on the pump thread (e.g. a human sharing a tab)."""
        reply: queue.Queue[Any] = queue.Queue()
        self._controls.put((fn, reply))
        ok, value = reply.get(timeout=timeout)
        if not ok:
            raise value
        return value

    def host_message(self, message: dict[str, Any]) -> Any:
        """Send one raw host message to the extension (bypassing the gateway)."""
        return self.control(
            lambda _ctx, worker: worker.evaluate("m => globalThis.locus.onHostMessage(m)", message)
        )

    def share_tab(self, tab_id: int, on: bool = True) -> None:
        """What the popup's 'Share this tab' button does (a human gesture)."""
        self.control(
            lambda _ctx, worker: worker.evaluate(
                "([id, on]) => globalThis.locus.setShared(id, on)", [tab_id, on]
            )
        )

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=30)

    # -- pump thread -----------------------------------------------------------
    def run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            self.error = exc
            self.ready.set()
            return
        try:
            with sync_playwright() as pw:
                ext = str(EXTENSION_DIR)
                context = pw.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    channel="chromium",  # the new headless mode loads extensions
                    headless=True,
                    args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"],
                )
                try:
                    worker = self._worker(context)
                    self.client_id, self._token = self.hub.hello(
                        origin=CHROMIUM_ORIGIN,
                        presented_key=self.key,
                        browser="chromium-test",
                        extension_version="test",
                    )
                    self.worker_url = worker.url
                    self.ready.set()
                    self._loop(context, worker)
                finally:
                    context.close()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test as a skip/fail
            self.error = exc
            self.ready.set()

    @staticmethod
    def _worker(context: Any) -> Any:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            workers = [w for w in context.service_workers if w.url.endswith("/background.js")]
            if workers:
                return workers[0]
            time.sleep(0.05)
        raise ExtensionUnavailable("the extension's service worker did not start")

    def _loop(self, context: Any, worker: Any) -> None:
        while not self._stop_event.is_set():
            while True:
                try:
                    fn, reply = self._controls.get_nowait()
                except queue.Empty:
                    break
                try:
                    reply.put((True, fn(context, worker)))
                except BaseException as exc:  # noqa: BLE001 - returned to the caller
                    reply.put((False, exc))
            try:
                commands = self.hub.next_commands(self.client_id, self._token, wait_s=0.02)
            except Exception:  # noqa: BLE001 - unpaired / revoked: keep serving controls
                commands = []
                time.sleep(0.02)
            for command in commands:
                answer = worker.evaluate("m => globalThis.locus.onHostMessage(m)", command)
                self.delivered.append(command)
                if isinstance(answer, dict) and answer.get("type") == "result":
                    try:
                        self.hub.post_result(self.client_id, self._token, answer)
                    except Exception:  # noqa: BLE001 - nobody waiting
                        pass


__all__ = ["TEST_PAIRING_KEY", "ExtensionPump", "ExtensionUnavailable"]
