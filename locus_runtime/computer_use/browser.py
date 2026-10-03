"""The agent browser: Playwright Chromium in a dedicated Locus profile (LOCUS-341).

Doc 12 §7. Tools: :meth:`AgentBrowser.navigate`, :meth:`~AgentBrowser.read`,
:meth:`~AgentBrowser.act` (click / fill / press / select) and
:meth:`~AgentBrowser.screenshot`. Every call is authorized by the gateway with
its own action kind (``browser_navigate``, ``browser_read``, ``browser_act``)
and the :class:`~locus_runtime.gateway.UiFacts` read from the live DOM, inside a
:class:`~locus_runtime.computer_use.controller.ComputerUseController` action so
a panic stops it.

Containment:

* **Profile** -- a persistent profile under ``<app_home>/computer_use/
  agent-browser-profile``; never the user's own browser profile (paths inside
  known Chrome / Edge / Brave / Firefox profile roots are refused).
* **Egress** -- navigation hosts must pass ``network_egress``; every request is
  intercepted (``BrowserContext.route``) and requests to hosts the gateway does
  not allow are aborted and audited; and because interception misses redirect
  hops and WebSockets, the browser's only route out is a loopback
  :class:`~locus_runtime.computer_use.egress_proxy.EgressProxy` that authorizes
  every connection by host. Service workers are blocked, downloads refused,
  non-proxied WebRTC UDP disabled.
* **Secrets** -- password, card, CVV, SSN and OTP field values are never put in
  ``read`` output; screenshots mask those fields; typing into them is R4.
* **Untrusted content** -- everything read from the page is wrapped as
  untrusted data; element text only ever raises an action's risk class.

Playwright for Python (Microsoft, Apache-2.0) is the P30 choice: it is the
maintained FOSS driver for Chromium over CDP, with accessibility-aware locators.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

from locus_runtime.computer_use.common import (
    UiResult,
    cancelled_result,
    computer_use_home,
    frame_retention_days,
    gate,
    mode_refusal,
    wrap_untrusted,
)
from locus_runtime.computer_use.controller import (
    CancelToken,
    ComputerUseCancelled,
    ComputerUseController,
    get_controller,
)
from locus_runtime.computer_use.egress_proxy import EgressProxy
from locus_runtime.gateway import GatewaySession, UiFacts, authorize_action, redact_text

logger = logging.getLogger(__name__)

#: App id the agent browser presents in UiFacts (it is not a user desktop app).
AGENT_BROWSER_APP = "locus-agent-browser"
BROWSER_ACT_CONTROLS = ("click", "fill", "press", "select")
_PASSTHROUGH_SCHEMES = frozenset({"data", "blob", "about"})
_POLL_SECONDS = 0.025
_MAX_FILL_CHARS = 10_000
_MAX_ELEMENTS = 200

# Fields whose values never leave the page and that screenshots mask.
SENSITIVE_FIELD_SELECTOR = ", ".join(
    [
        "input[type=password i]",
        *(
            f"[autocomplete~='{token}' i]"
            for token in (
                "current-password",
                "new-password",
                "one-time-code",
                "cc-number",
                "cc-csc",
                "cc-exp",
                "cc-exp-month",
                "cc-exp-year",
            )
        ),
        *(
            f"input[{attr}*='{word}' i]"
            for attr in ("name", "id", "aria-label", "placeholder")
            for word in ("pass", "pwd", "cvv", "cvc", "card", "ssn", "otp", "security code")
        ),
    ]
)

# Known roots of real browser profiles: the agent never uses them.
_USER_PROFILE_MARKERS = (
    "google/chrome/user data",
    "microsoft/edge/user data",
    "bravesoftware/brave-browser",
    "mozilla/firefox",
    "library/application support/google/chrome",
    "library/application support/microsoft edge",
    "library/application support/firefox",
    ".config/google-chrome",
    ".config/chromium",
    ".mozilla/firefox",
)

_COLLECT_JS = (
    """
() => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, '
    + '[role=button], [role=link], [role=checkbox], [role=radio], [role=tab], '
    + '[role=menuitem], [role=option], [role=switch], [role=textbox], [role=combobox], '
    + '[contenteditable=""], [contenteditable=true], h1, h2, h3';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= %d) break;
    const r = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (r.width === 0 && r.height === 0) continue;
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    out.push(el);
  }
  return out;
}
"""
    % _MAX_ELEMENTS
)

# Describes one element. Never returns the value of a password / card / OTP input.
_FACTS_JS = r"""
(el) => {
  const txt = (s) => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  const tag = el.tagName.toLowerCase();
  const rawType = (el.getAttribute('type') || '').toLowerCase();
  const type = rawType || (tag === 'button' ? 'submit' : '');
  const auto = (el.getAttribute('autocomplete') || '').toLowerCase();
  let labelText = '';
  if (el.labels && el.labels.length) {
    labelText = Array.from(el.labels).map((l) => l.innerText).join(' ');
  }
  const lb = el.getAttribute('aria-labelledby');
  if (lb) {
    labelText += ' ' + lb.split(/\s+/).map((id) => {
      const n = document.getElementById(id); return n ? n.innerText : '';
    }).join(' ');
  }
  const isButtonInput = tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(type);
  const own = ['button', 'a', 'summary', 'option', 'h1', 'h2', 'h3'].includes(tag)
    || ['button', 'link', 'tab', 'menuitem', 'option'].includes(el.getAttribute('role') || '')
    ? el.innerText : '';
  const name = txt(el.getAttribute('aria-label') || labelText || own
    || (isButtonInput ? el.value : '') || el.getAttribute('alt') || el.getAttribute('title')
    || el.getAttribute('placeholder') || '');
  const label = txt([labelText, el.getAttribute('placeholder'), el.getAttribute('title')]
    .filter(Boolean).join(' '));
  const implicit = {a: 'link', button: 'button', select: 'combobox', textarea: 'textbox',
    summary: 'button', h1: 'heading', h2: 'heading', h3: 'heading'};
  let role = el.getAttribute('role') || implicit[tag] || '';
  if (!role && tag === 'input') {
    role = isButtonInput ? 'button'
      : (['checkbox', 'radio'].includes(type) ? type : 'textbox');
  }
  const secretAuto = ['current-password', 'new-password', 'one-time-code', 'cc-number',
    'cc-csc', 'cc-exp', 'cc-exp-month', 'cc-exp-year'];
  const secret = type === 'password' || auto.split(/\s+/).some((t) => secretAuto.includes(t));
  const form = el.form || el.closest('form');
  const isSubmit = !!form && ((tag === 'button' && type === 'submit')
    || (tag === 'input' && (type === 'submit' || type === 'image')));
  const fields = [];
  let formText = '';
  if (form) {
    for (const f of form.querySelectorAll('input, select, textarea')) {
      if (fields.length >= 50) break;
      const flabels = f.labels ? Array.from(f.labels).map((l) => l.innerText).join(' ') : '';
      fields.push({
        type: (f.getAttribute('type') || '').toLowerCase(),
        autocomplete: (f.getAttribute('autocomplete') || '').toLowerCase(),
        name: txt(f.getAttribute('aria-label') || flabels || f.getAttribute('placeholder')),
        field_id: txt([f.getAttribute('name'), f.id].filter(Boolean).join(' ')),
      });
    }
    formText = txt(Array.from(form.querySelectorAll(
      'button, input[type=submit], input[type=image]'))
      .map((b) => b.innerText || b.value || b.getAttribute('aria-label') || '').join(' '));
  }
  const valueOk = !secret && ['input', 'textarea', 'select'].includes(tag)
    && !['submit', 'button', 'reset', 'image', 'file'].includes(type);
  return {
    tag, type: rawType, role, name, label, autocomplete: auto,
    field_id: txt([el.getAttribute('name'), el.id].filter(Boolean).join(' ')),
    is_password: type === 'password', secret, is_submit: isSubmit, in_form: !!form,
    form_fields: fields, form_text: formText,
    value: valueOk ? txt(el.value).slice(0, 120) : '',
    disabled: !!el.disabled, checked: !!el.checked,
  };
}
"""


class BrowserUnavailable(RuntimeError):
    """Playwright or its Chromium build is not installed here."""


def is_user_browser_profile(path: Path) -> bool:
    text = str(path).replace("\\", "/").lower()
    return any(marker in text for marker in _USER_PROFILE_MARKERS)


def default_profile_dir(app_home: Path | None = None) -> Path:
    return computer_use_home(app_home) / "agent-browser-profile"


def _display_url(url: str) -> str:
    """Scheme, host and path only: query strings and fragments can carry tokens."""
    try:
        parts = urlparse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    return redact_text(
        urlparse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), limit=300
    )


def _host(url: str) -> str:
    try:
        return (urlparse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _form_is_sensitive(fields: list[dict[str, Any]]) -> bool:
    for item in fields or []:
        facts = UiFacts.create(
            surface="browser",
            control="fill",
            input_type=item.get("type", ""),
            autocomplete=item.get("autocomplete", ""),
            name=item.get("name", ""),
            field_id=item.get("field_id", ""),
        )
        if facts.sensitive_field:
            return True
    return False


def ui_facts_from_dom(control: str, facts: dict[str, Any], *, key: str = "") -> UiFacts:
    """UiFacts for a browser action from the element description ``_FACTS_JS`` returns."""
    in_form = bool(facts.get("in_form"))
    enter = key.strip().lower() in {"enter", "return", "numpadenter"}
    submits = (control == "click" and bool(facts.get("is_submit"))) or (
        control == "press" and enter and in_form
    )
    return UiFacts.create(
        surface="browser",
        control=control,
        app=AGENT_BROWSER_APP,
        role=facts.get("role", ""),
        name=facts.get("name", ""),
        label=facts.get("label", ""),
        input_type=facts.get("type", ""),
        autocomplete=facts.get("autocomplete", ""),
        field_id=facts.get("field_id", ""),
        is_password=bool(facts.get("is_password")) or bool(facts.get("secret")),
        submits_form=submits,
        form_sensitive=_form_is_sensitive(list(facts.get("form_fields") or [])),
        form_text=facts.get("form_text", ""),
        key=key,
    )


@dataclass
class BlockedRequest:
    host: str
    via: str  # "route" (request interception) or "proxy" (egress proxy)
    audit_id: str


class AgentBrowser:
    """Gated browser tools over a dedicated Playwright Chromium profile."""

    def __init__(
        self,
        session: GatewaySession | None,
        *,
        controller: ComputerUseController | None = None,
        run_dir: Path | None = None,
        profile_dir: Path | None = None,
        app_home: Path | None = None,
        headless: bool = True,
        read_max_chars: int = 16_000,
        action_timeout_ms: int = 5_000,
        navigation_timeout_s: float = 20.0,
    ) -> None:
        self._session = session
        self._controller = controller or get_controller()
        run_id = session.caller.run_id if session is not None else "unbound"
        self._run_dir = Path(run_dir) if run_dir else computer_use_home(app_home) / "runs" / run_id
        self._profile_dir = Path(profile_dir) if profile_dir else default_profile_dir(app_home)
        if is_user_browser_profile(self._profile_dir):
            raise ValueError("the agent browser never uses a user's own browser profile")
        self._headless = headless
        self._read_max = max(1_000, int(read_max_chars))
        self._action_timeout_ms = max(100, int(action_timeout_ms))
        self._nav_timeout_s = max(1.0, float(navigation_timeout_s))
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._proxy: EgressProxy | None = None
        self._refs: dict[str, Any] = {}
        self._ref_list: Any = None
        self._allowed_hosts: set[str] = set()
        self._close_requested = False
        self.blocked_requests: list[BlockedRequest] = []
        self._controller.on_panic(self._on_panic)

    # -- lifecycle -------------------------------------------------------------
    @property
    def profile_dir(self) -> Path:
        return self._profile_dir

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    def _on_panic(self) -> None:
        # Never touch Playwright from the panic thread: flag it; the owning thread
        # closes the browser on its next call (or close()).
        self._close_requested = True

    def _start(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(
                "Playwright is not installed (pip install playwright; "
                "python -m playwright install chromium)"
            ) from exc
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._proxy = EgressProxy(self._authorize_proxy).start()
        self._pw = sync_playwright().start()
        try:
            self._context = self._pw.chromium.launch_persistent_context(
                str(self._profile_dir),
                headless=self._headless,
                proxy={"server": self._proxy.url, "bypass": "<-loopback>"},
                service_workers="block",
                accept_downloads=False,
                args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
            )
        except Exception as exc:
            self._shutdown()
            raise BrowserUnavailable(f"could not launch the agent browser: {exc}") from exc
        self._context.set_default_timeout(self._action_timeout_ms)
        self._context.route("**/*", self._route)
        self._context.route_web_socket("**/*", self._route_ws)
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        return self._page

    def _page_for(self, token: CancelToken) -> Any:
        token.check()
        if self._close_requested:
            self.close()
        if self._page is None or self._page.is_closed():
            self._close_requested = False
            self._start()
        return self._page

    def _current_page(self) -> Any:
        """The open page, or None. A panic since the last call closes the browser
        first (panic closes agent browser profiles, 12 §5)."""
        if self._close_requested:
            self.close()
        page = self._page
        return None if page is None or page.is_closed() else page

    def _shutdown(self) -> None:
        context, pw, proxy = self._context, self._pw, self._proxy
        self._context = self._pw = self._page = self._proxy = None
        self._refs = {}
        self._ref_list = None
        for closer in (
            context.close if context is not None else None,
            pw.stop if pw is not None else None,
            proxy.close if proxy is not None else None,
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 - best-effort teardown
                logger.debug("computer_use.browser_close_error", exc_info=True)

    def close(self) -> None:
        """Close the browser (the profile on disk is kept)."""
        self._shutdown()
        self._close_requested = False

    def detach(self) -> None:
        """Close and stop listening for panics (end of the run)."""
        self.close()
        self._controller.remove_panic_listener(self._on_panic)

    # -- egress ----------------------------------------------------------------
    def _authorize_host(self, host: str, *, via: str, port: int = 0) -> bool:
        if self._controller.panicked:
            return False
        decision = authorize_action(
            self._session,
            kind="network_egress",
            tool=f"browser.{via}",
            target=host or "(none)",
            egress_host=host,
            args={"port": port} if port else {},
        )
        if not decision.allowed:
            self.blocked_requests.append(BlockedRequest(host, via, decision.audit_id))
            logger.info(
                "computer_use.egress_blocked",
                extra={"host": host, "via": via, "audit_id": decision.audit_id},
            )
        return decision.allowed

    def _authorize_proxy(self, host: str, port: int) -> bool:
        return self._authorize_host(host, via="proxy", port=port)

    def _route(self, route: Any) -> None:
        url = route.request.url
        scheme = url.split(":", 1)[0].lower()
        if scheme in _PASSTHROUGH_SCHEMES:
            route.continue_()
            return
        host = _host(url)
        if scheme in {"http", "https"} and host and host in self._allowed_hosts:
            route.continue_()
            return
        if scheme in {"http", "https"} and host and self._authorize_host(host, via="route"):
            self._allowed_hosts.add(host)
            route.continue_()
            return
        if not (scheme in {"http", "https"} and host):
            self._authorize_host(host, via="route")  # audited deny (non-http scheme)
        route.abort("blockedbyclient")

    def _route_ws(self, ws: Any) -> None:
        host = _host(ws.url)
        if host and (host in self._allowed_hosts or self._authorize_host(host, via="websocket")):
            ws.connect_to_server()
            return
        ws.close(code=1008, reason="blocked by Locus egress policy")

    # -- helpers ---------------------------------------------------------------
    def _wait_settled(self, page: Any, token: CancelToken, seconds: float) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            token.check()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=50)
                return
            except PlaywrightTimeout:
                continue

    def _resolve(
        self,
        page: Any,
        token: CancelToken,
        *,
        ref: str,
        selector: str,
        role: str,
        name: str,
    ) -> Any:
        """One element handle for the target, polling (cancellable) until it appears."""
        if ref:
            handle = self._refs.get(ref)
            if handle is None:
                raise LookupError(f"unknown element ref {ref!r}; call browser_read again")
            return handle
        if selector:
            locator = page.locator(selector)
        elif role:
            locator = page.get_by_role(role, name=name or None, exact=bool(name))
        else:
            raise LookupError("give a ref (from browser_read), a selector, or a role and name")
        deadline = time.monotonic() + self._action_timeout_ms / 1000.0
        while True:
            token.check()
            count = locator.count()
            if count == 1:
                return locator.element_handle(timeout=self._action_timeout_ms)
            if count > 1:
                raise LookupError(f"target is ambiguous ({count} matches); be more specific")
            if time.monotonic() >= deadline:
                raise LookupError("no element matches the target")
            token.wait(_POLL_SECONDS)

    # -- tools -----------------------------------------------------------------
    def navigate(self, url: str) -> UiResult:
        tool = "browser_navigate"
        text = str(url or "").strip()
        try:
            parts = urlparse.urlsplit(text)
        except ValueError:
            return UiResult("error", f"[error] {tool}: not a valid URL.")
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        target = _display_url(text)
        ui = UiFacts.create(
            surface="browser", control="navigate", app=AGENT_BROWSER_APP, url_scheme=scheme
        )
        try:
            with self._controller.action("browser_navigate") as token:
                refusal = mode_refusal(self._controller, "browser_navigate", tool, ui, target)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="browser_navigate",
                    tool=tool,
                    ui=ui,
                    target=target,
                    egress_host=host,
                    args={"url": target},
                )
                if blocked is not None:
                    return blocked
                page = self._page_for(token)
                self._allowed_hosts = {host}
                self._refs, self._ref_list = {}, None
                token.check()
                try:
                    page.goto(text, wait_until="commit", timeout=self._nav_timeout_s * 1000)
                except Exception as exc:  # noqa: BLE001 - reported to the agent
                    return UiResult(
                        "error",
                        f"[error] {tool}: navigation to {target} failed "
                        f"({redact_text(str(exc).splitlines()[0], limit=200)}).",
                        decision=decision,
                    )
                self._wait_settled(page, token, self._nav_timeout_s)
                final = _display_url(page.url)
                return UiResult(
                    "done",
                    f"Navigated to {final}. Call browser_read to see the page.",
                    decision=decision,
                    data={"url": page.url},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def read(self) -> UiResult:
        tool = "browser_read"
        ui = UiFacts.create(surface="browser", control="read", app=AGENT_BROWSER_APP)
        try:
            with self._controller.action("browser_read") as token:
                page = self._current_page()
                target = _display_url(page.url) if page is not None else ""
                decision, blocked = gate(
                    self._session, kind="browser_read", tool=tool, ui=ui, target=target
                )
                if blocked is not None:
                    return blocked
                if page is None:
                    return UiResult(
                        "error", "[error] browser_read: no page is open; navigate first."
                    )
                token.check()
                lines, elements = self._snapshot(page, token)
                token.check()
                body = str(page.evaluate("() => document.body ? document.body.innerText : ''"))
                budget = self._read_max - sum(len(line) + 1 for line in lines)
                visible = body[: max(0, budget)] + (
                    "\n[... truncated]" if len(body) > budget else ""
                )
                content = "Elements:\n" + "\n".join(lines) + "\n\nVisible text:\n" + visible
                return UiResult(
                    "done",
                    f"Page {target}\n"
                    + wrap_untrusted(content[: self._read_max], source="web-page"),
                    decision=decision,
                    data={"url": page.url, "elements": elements},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def _snapshot(self, page: Any, token: CancelToken) -> tuple[list[str], list[dict[str, Any]]]:
        if self._ref_list is not None:
            try:
                self._ref_list.dispose()
            except Exception:  # noqa: BLE001 - stale handle
                pass
        array = page.evaluate_handle(_COLLECT_JS)
        self._ref_list = array
        handles = [
            prop.as_element()
            for _, prop in sorted(array.get_properties().items(), key=lambda kv: int(kv[0]))
        ]
        self._refs = {}
        lines: list[str] = []
        elements: list[dict[str, Any]] = []
        for index, handle in enumerate(handles, start=1):
            if handle is None:
                continue
            token.check()
            facts = handle.evaluate(_FACTS_JS)
            ref = f"e{index}"
            self._refs[ref] = handle
            ui = ui_facts_from_dom("fill", facts)
            value = str(facts.get("value") or "")
            if ui.sensitive_field or facts.get("secret"):
                value = "[redacted]" if facts.get("tag") in {"input", "textarea"} else ""
            entry = {
                "ref": ref,
                "role": facts.get("role", ""),
                "name": facts.get("name", ""),
                "value": value,
                "disabled": bool(facts.get("disabled")),
            }
            elements.append(entry)
            line = f'[{ref}] {entry["role"] or facts.get("tag", "")} "{entry["name"]}"'
            if value:
                line += f" value={json.dumps(value)}"
            if entry["disabled"]:
                line += " (disabled)"
            lines.append(line)
        return lines, elements

    def act(
        self,
        action: str,
        *,
        ref: str = "",
        selector: str = "",
        role: str = "",
        name: str = "",
        value: str = "",
    ) -> UiResult:
        tool = "browser_act"
        control = str(action or "").strip().lower()
        if control not in BROWSER_ACT_CONTROLS:
            return UiResult(
                "error", f"[error] {tool}: action must be one of {BROWSER_ACT_CONTROLS}."
            )
        value = str(value or "")
        if control == "fill" and len(value) > _MAX_FILL_CHARS:
            return UiResult("error", f"[error] {tool}: value longer than {_MAX_FILL_CHARS} chars.")
        if control in {"press", "select"} and not value:
            return UiResult("error", f"[error] {tool}: '{control}' needs a value.")
        try:
            with self._controller.action("browser_act") as token:
                page = self._current_page()
                if page is None:
                    return UiResult("error", f"[error] {tool}: no page is open; navigate first.")
                try:
                    handle = self._resolve(
                        page, token, ref=ref, selector=selector, role=role, name=name
                    )
                    facts = handle.evaluate(_FACTS_JS)
                except LookupError as exc:
                    return UiResult("error", f"[error] {tool}: {exc}")
                except ComputerUseCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - stale handle, detached node
                    return UiResult(
                        "error",
                        f"[error] {tool}: element is gone ({redact_text(str(exc), limit=120)}); "
                        "call browser_read again.",
                    )
                ui = ui_facts_from_dom(control, facts, key=value if control == "press" else "")
                host = _host(page.url)
                describe = f'{control} on {ui.role or facts.get("tag", "")} "{ui.name}"'
                refusal = mode_refusal(self._controller, "browser_act", tool, ui, describe)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="browser_act",
                    tool=tool,
                    ui=ui,
                    target=f"{host}: {ui.role} '{ui.name}'",
                    args={
                        "control": control,
                        "target": ref or selector or f"{role}:{name}",
                        "text": value,
                    },
                )
                if blocked is not None:
                    return blocked
                token.check()
                timeout = self._action_timeout_ms
                if control == "click":
                    handle.click(timeout=timeout)
                elif control == "fill":
                    handle.fill(value, timeout=timeout)
                elif control == "press":
                    handle.press(value, timeout=timeout)
                else:
                    handle.select_option(value, timeout=timeout)
                self._wait_settled(page, token, 0.5)
                return UiResult(
                    "done",
                    f"{describe}: done. Page is now {_display_url(page.url)}. Read the page "
                    "again before the next action.",
                    decision=decision,
                    data={"url": page.url},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def screenshot(self) -> UiResult:
        tool = "browser_screenshot"
        ui = UiFacts.create(surface="browser", control="screenshot", app=AGENT_BROWSER_APP)
        try:
            with self._controller.action("browser_read") as token:
                page = self._current_page()
                target = _display_url(page.url) if page is not None else ""
                decision, blocked = gate(
                    self._session, kind="browser_read", tool=tool, ui=ui, target=target
                )
                if blocked is not None:
                    return blocked
                if page is None:
                    return UiResult("error", f"[error] {tool}: no page is open; navigate first.")
                token.check()
                frames = self._run_dir / "frames"
                frames.mkdir(parents=True, exist_ok=True)
                created = datetime.now(UTC)
                stem = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-browser"
                path = frames / f"{stem}.png"
                mask = page.locator(SENSITIVE_FIELD_SELECTOR)
                masked = mask.count()
                page.screenshot(
                    path=str(path),
                    mask=[mask],
                    mask_color="#000000",
                    timeout=self._action_timeout_ms,
                )
                days = frame_retention_days()
                meta = {
                    "kind": "browser_screenshot",
                    "run_id": self._session.caller.run_id if self._session else "",
                    "host": _host(page.url),
                    "created_at": created.isoformat(),
                    "expires_at": (created + timedelta(days=days)).isoformat(),
                    "retention_days": days,
                    "redaction": "password, payment-card, CVV, SSN and OTP inputs masked",
                    "masked_elements": masked,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "audit_id": decision.audit_id,
                }
                path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
                return UiResult(
                    "done",
                    f"Screenshot saved ({masked} sensitive field(s) masked); kept {days} days.",
                    decision=decision,
                    data={"path": str(path), "metadata": meta},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)


def prune_expired_frames(frames_dir: Path, *, now: datetime | None = None) -> list[Path]:
    """Delete frames whose retention metadata says they expired; returns what was removed.

    Only files this module wrote (a ``.png`` with a ``.json`` sidecar carrying
    ``expires_at``) are touched.
    """
    moment = now or datetime.now(UTC)
    removed: list[Path] = []
    if not frames_dir.is_dir():
        return removed
    for meta_path in frames_dir.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(str(meta["expires_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if expires <= moment:
            frame = meta_path.with_suffix(".png")
            for item in (frame, meta_path):
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    continue
            removed.append(frame)
    return removed
