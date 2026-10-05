"""``UserBrowserDriver``: the ``"user"`` BrowserDriver (LOCUS-350, D-25).

Drives the principal's own signed-in Chrome / Edge / Firefox through the
Locus extension, via the :class:`~.relay.RelayHub`. For every port action:

1. Register a controller action (panic cancels it; observe / assist modes
   refuse or propose acts, exactly as for the agent browser).
2. Perceive the facts the gateway classifies on, from the browser itself:
   the tab's URL (``tab_info``: the browser's tab URL, not page text) and, for
   an act, the target element's description (``inspect``: role, name, type,
   autocomplete, form facts and a digest; never a field value). These internal
   reads are never shown to the model.
3. Authorize one ``user_browser_*`` gateway action. The tier, the site lists
   and the floor are evaluated there (``policies/user_browser.rego`` + the
   gateway risk classes), never here.
4. Only when allowed, send the command. Acts carry the element digest; the
   extension refuses if the element changed since it was authorized, and
   refuses data entry into secret fields on its own as well.
5. Return page-derived text wrapped as untrusted, with secret values redacted
   (the extension never sends them; the driver re-checks every element).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from locus_runtime.computer_use.browser import ui_facts_from_dom
from locus_runtime.computer_use.browser_contract import (
    PORT_VERSION,
    BrowserAction,
    BrowserObservation,
    BrowserProfile,
    unsupported,
)
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
from locus_runtime.computer_use.user_browser.relay import RelayError, RelayHub, get_hub
from locus_runtime.computer_use.user_browser.sites import display_url, scheme_of, site_of
from locus_runtime.computer_use.user_browser.tiers import current_tier_settings, visible_tabs
from locus_runtime.gateway import GatewaySession, UiFacts, redact_text

logger = logging.getLogger(__name__)

#: App id the user browser presents in UiFacts.
USER_BROWSER_APP = "locus-user-browser"
_MAX_TEXT = 16_000
_MAX_SCREENSHOT_BYTES = 12 * 1024 * 1024


def _tab_arg(tab_id: str) -> int | None:
    text = str(tab_id or "").strip()
    if not text:
        return None
    if not text.isdigit() or len(text) > 12:
        raise ValueError("tab_id must be a tab number from user_browser_tabs")
    return int(text)


def _redact_elements(raw: Any) -> list[dict[str, Any]]:
    """Element entries with every secret value removed (defence in depth)."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        facts = UiFacts.create(
            surface="browser",
            control="fill",
            input_type=item.get("type", ""),
            autocomplete=item.get("autocomplete", ""),
            name=item.get("name", ""),
            label=item.get("label", ""),
            field_id=item.get("field_id", ""),
            is_password=bool(item.get("secret")),
        )
        value = str(item.get("value") or "")[:120]
        if facts.sensitive_field or item.get("secret"):
            value = "[redacted]" if item.get("tag") in {"input", "textarea"} else ""
        out.append(
            {
                "ref": str(item.get("ref") or "")[:64],
                "role": str(item.get("role") or item.get("tag") or "")[:64],
                "name": str(item.get("name") or "")[:300],
                "value": value,
                "disabled": bool(item.get("disabled")),
            }
        )
    return out


class UserBrowserDriver:
    """Gated actions on the principal's own browser profile (``profile="user"``)."""

    profile: BrowserProfile = "user"
    port_version: str = PORT_VERSION

    def __init__(
        self,
        session: GatewaySession | None,
        *,
        controller: ComputerUseController | None = None,
        hub: RelayHub | None = None,
        client: str = "",
        run_dir: Path | None = None,
        app_home: Path | None = None,
        call_timeout_s: float = 20.0,
    ) -> None:
        self._session = session
        self._controller = controller or get_controller()
        self._hub = hub or get_hub()
        self._hub.attach(self._controller)
        self._client = client
        run_id = session.caller.run_id if session is not None else "unbound"
        self._run_dir = Path(run_dir) if run_dir else computer_use_home(app_home) / "runs" / run_id
        self._timeout = max(1.0, float(call_timeout_s))

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    def detach(self) -> None:
        """Nothing to close: the browser is the principal's. In-flight work ends with the run."""
        return None

    # -- port ------------------------------------------------------------------
    def perform(self, action: BrowserAction) -> BrowserObservation:
        try:
            tab = _tab_arg(action.tab_id)
        except ValueError as exc:
            return unsupported(self.profile, action, str(exc))
        if action.op == "tabs":
            result = self.tabs()
        elif action.op == "read":
            result = self.observe(tab)
        elif action.op == "navigate":
            result = self.navigate(action.url, tab)
        elif action.op == "screenshot":
            result = self.screenshot(tab)
        else:
            if action.control is None:
                return unsupported(self.profile, action, "act needs an action.")
            result = self.act(tab, action.control, ref=action.ref, value=action.value)
        return BrowserObservation.from_ui_result(self.profile, result)

    # -- helpers ---------------------------------------------------------------
    def _call(self, op: str, args: dict[str, Any], token: CancelToken) -> dict[str, Any]:
        token.check()
        return self._hub.call(
            op,
            args,
            client_id=self._client,
            cancel=token,
            controller=self._controller,
            timeout_s=self._timeout,
        )

    def _tab(self, tab: int | None, token: CancelToken) -> dict[str, Any]:
        info = self._call("tab_info", {"tab_id": tab}, token)
        url = str(info.get("url") or "")
        return {
            "tab_id": info.get("tab_id"),
            "url": url,
            "site": site_of(url),
            "shared": info.get("shared") is True,
            "active": info.get("active") is True,
        }

    @staticmethod
    def _relay_failure(tool: str, exc: RelayError) -> UiResult:
        if exc.code == "panic":
            return UiResult(
                "cancelled",
                f"[stopped] {tool}: computer use was stopped (panic). Do not retry; the human "
                "has stopped computer use.",
            )
        return UiResult(
            "error",
            f"[error] {tool}: {exc.code} ({redact_text(str(exc), limit=200)}).",
            data={"relay_error": exc.code},
        )

    # -- tools -----------------------------------------------------------------
    def tabs(self) -> UiResult:
        tool = "user_browser_tabs"
        ui = UiFacts.create(surface="browser", control="tabs", app=USER_BROWSER_APP)
        try:
            with self._controller.action("user_browser_read") as token:
                decision, blocked = gate(
                    self._session, kind="user_browser_read", tool=tool, ui=ui, target="(tabs)"
                )
                if blocked is not None:
                    return blocked
                try:
                    listing = self._call("list_tabs", {}, token)
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                tabs = [
                    {
                        "tab_id": item.get("tab_id"),
                        "site": site_of(str(item.get("url") or "")),
                        "url": display_url(str(item.get("url") or "")),
                        "title": redact_text(str(item.get("title") or ""), limit=120),
                        "shared": item.get("shared") is True,
                        "active": item.get("active") is True,
                    }
                    for item in listing.get("tabs") or []
                    if isinstance(item, dict)
                ]
                shown = visible_tabs(current_tier_settings(), tabs)
                lines = [
                    f'[{t["tab_id"]}] {t["url"]} "{t["title"]}"'
                    + (" (shared)" if t["shared"] else "")
                    + (" (active)" if t["active"] else "")
                    for t in shown
                ]
                hidden = len(tabs) - len(shown)
                body = "\n".join(lines) or "(no tabs visible to Locus)"
                note = (
                    f"\n{hidden} other tab(s) are not visible at this browser tier; ask the "
                    "human to share a tab from the Locus extension."
                    if hidden
                    else ""
                )
                return UiResult(
                    "done",
                    "Tabs in the principal's browser:\n"
                    + wrap_untrusted(body, source="user-browser-tabs")
                    + note,
                    decision=decision,
                    data={"tabs": shown},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def observe(self, tab: int | None) -> UiResult:
        tool = "user_browser_observe"
        try:
            with self._controller.action("user_browser_read") as token:
                try:
                    info = self._tab(tab, token)
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                ui = UiFacts.create(
                    surface="browser",
                    control="observe",
                    app=USER_BROWSER_APP,
                    site=info["site"],
                    tab_shared=info["shared"],
                )
                decision, blocked = gate(
                    self._session,
                    kind="user_browser_read",
                    tool=tool,
                    ui=ui,
                    target=display_url(info["url"]),
                )
                if blocked is not None:
                    return blocked
                try:
                    page = self._call(
                        "observe", {"tab_id": info["tab_id"], "max_chars": _MAX_TEXT}, token
                    )
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                elements = _redact_elements(page.get("elements"))
                lines = []
                for entry in elements:
                    line = f'[{entry["ref"]}] {entry["role"]} "{entry["name"]}"'
                    if entry["value"]:
                        line += f" value={json.dumps(entry['value'])}"
                    if entry["disabled"]:
                        line += " (disabled)"
                    lines.append(line)
                text = str(page.get("text") or "")
                budget = max(0, _MAX_TEXT - sum(len(line) + 1 for line in lines))
                visible = text[:budget] + ("\n[... truncated]" if len(text) > budget else "")
                content = "Elements:\n" + "\n".join(lines) + "\n\nVisible text:\n" + visible
                return UiResult(
                    "done",
                    f"Tab {info['tab_id']} {display_url(info['url'])}\n"
                    + wrap_untrusted(content[:_MAX_TEXT], source="user-browser-page"),
                    decision=decision,
                    data={"url": info["url"], "site": info["site"], "elements": elements},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def navigate(self, url: str, tab: int | None) -> UiResult:
        tool = "user_browser_navigate"
        text = str(url or "").strip()
        site, scheme = site_of(text), scheme_of(text)
        try:
            with self._controller.action("user_browser_navigate") as token:
                shared = True  # a new tab the agent opens is the agent's own
                if tab is not None:
                    try:
                        shared = self._tab(tab, token)["shared"]
                    except RelayError as exc:
                        return self._relay_failure(tool, exc)
                ui = UiFacts.create(
                    surface="browser",
                    control="navigate",
                    app=USER_BROWSER_APP,
                    url_scheme=scheme,
                    site=site,
                    tab_shared=shared,
                )
                target = display_url(text)
                refusal = mode_refusal(self._controller, "user_browser_navigate", tool, ui, target)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="user_browser_navigate",
                    tool=tool,
                    ui=ui,
                    target=target,
                    args={"url": target, "tab": "" if tab is None else str(tab)},
                )
                if blocked is not None:
                    return blocked
                try:
                    done = self._call("navigate", {"tab_id": tab, "url": text}, token)
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                final = str(done.get("url") or text)
                return UiResult(
                    "done",
                    f"Tab {done.get('tab_id')} is at {display_url(final)}. Call "
                    "user_browser_observe to read it.",
                    decision=decision,
                    data={"url": final, "site": site_of(final), "tab_id": done.get("tab_id")},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def act(self, tab: int | None, control: str, *, ref: str = "", value: str = "") -> UiResult:
        tool = "user_browser_act"
        control = str(control or "").strip().lower()
        value = str(value or "")
        if control in {"press", "select"} and not value:
            return UiResult("error", f"[error] {tool}: '{control}' needs a value.")
        if control != "scroll" and not ref:
            return UiResult(
                "error", f"[error] {tool}: give an element ref from user_browser_observe."
            )
        try:
            with self._controller.action("user_browser_act") as token:
                try:
                    info = self._tab(tab, token)
                    facts: dict[str, Any] = {}
                    digest = ""
                    if control != "scroll":
                        inspected = self._call(
                            "inspect", {"tab_id": info["tab_id"], "ref": ref}, token
                        )
                        facts = dict(inspected.get("facts") or {})
                        digest = str(inspected.get("digest") or "")
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                ui = ui_facts_from_dom(
                    control,
                    facts,
                    key=value if control == "press" else "",
                    app=USER_BROWSER_APP,
                    site=info["site"],
                    tab_shared=info["shared"],
                )
                describe = (
                    f"scroll {value or 'down'}"
                    if control == "scroll"
                    else f'{control} on {ui.role or facts.get("tag", "")} "{ui.name}"'
                )
                refusal = mode_refusal(self._controller, "user_browser_act", tool, ui, describe)
                if refusal is not None:
                    return refusal
                decision, blocked = gate(
                    self._session,
                    kind="user_browser_act",
                    tool=tool,
                    ui=ui,
                    target=f"{info['site']}: {ui.role} '{ui.name}'" if ref else info["site"],
                    args={"control": control, "target": ref, "text": value},
                )
                if blocked is not None:
                    return blocked
                try:
                    done = self._call(
                        "act",
                        {
                            "tab_id": info["tab_id"],
                            "ref": ref,
                            "control": control,
                            "value": value,
                            "expect_digest": digest,
                        },
                        token,
                    )
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                now = str(done.get("url") or info["url"])
                return UiResult(
                    "done",
                    f"{describe}: done. Tab {info['tab_id']} is at {display_url(now)}. Observe "
                    "again before the next action.",
                    decision=decision,
                    data={"url": now, "site": site_of(now)},
                )
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def screenshot(self, tab: int | None) -> UiResult:
        tool = "user_browser_screenshot"
        try:
            with self._controller.action("user_browser_read") as token:
                try:
                    info = self._tab(tab, token)
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                ui = UiFacts.create(
                    surface="browser",
                    control="screenshot",
                    app=USER_BROWSER_APP,
                    site=info["site"],
                    tab_shared=info["shared"],
                )
                decision, blocked = gate(
                    self._session,
                    kind="user_browser_read",
                    tool=tool,
                    ui=ui,
                    target=display_url(info["url"]),
                )
                if blocked is not None:
                    return blocked
                try:
                    shot = self._call("screenshot", {"tab_id": info["tab_id"]}, token)
                except RelayError as exc:
                    return self._relay_failure(tool, exc)
                data_url = str(shot.get("data_url") or "")
                prefix = "data:image/png;base64,"
                if not data_url.startswith(prefix):
                    return UiResult("error", f"[error] {tool}: the browser returned no PNG.")
                try:
                    png = base64.b64decode(data_url[len(prefix) :], validate=True)
                except (binascii.Error, ValueError):
                    return UiResult("error", f"[error] {tool}: the browser returned a bad PNG.")
                if len(png) > _MAX_SCREENSHOT_BYTES:
                    return UiResult("error", f"[error] {tool}: screenshot too large.")
                token.check()
                return self._save_frame(png, info, int(shot.get("masked") or 0), decision)
        except ComputerUseCancelled as exc:
            return cancelled_result(tool, exc)

    def _save_frame(self, png: bytes, info: dict[str, Any], masked: int, decision: Any) -> UiResult:
        frames = self._run_dir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        created = datetime.now(UTC)
        path = frames / f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-user-browser.png"
        path.write_bytes(png)
        days = frame_retention_days()
        meta = {
            "kind": "user_browser_screenshot",
            "run_id": self._session.caller.run_id if self._session else "",
            "site": info["site"],
            "created_at": created.isoformat(),
            "expires_at": (created + timedelta(days=days)).isoformat(),
            "retention_days": days,
            "redaction": "password, payment-card, CVV, SSN and OTP inputs masked by the extension",
            "masked_elements": masked,
            "sha256": hashlib.sha256(png).hexdigest(),
            "audit_id": getattr(decision, "audit_id", ""),
        }
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return UiResult(
            "done",
            f"Screenshot of tab {info['tab_id']} saved ({masked} sensitive field(s) masked); "
            f"kept {days} days.",
            decision=decision,
            data={"path": str(path), "metadata": meta, "site": info["site"]},
        )


__all__ = ["USER_BROWSER_APP", "UserBrowserDriver"]
