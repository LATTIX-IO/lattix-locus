"""Backend glue for the principal's own browser (LOCUS-350, decision D-25).

The HTTP handlers live in ``app.main`` next to the computer-use endpoints;
this module holds the checks they share:

* :func:`ensure_user_browser` -- install the persisted tier store and the relay
  hub (attached to the process computer-use controller, so panic reaches the
  extension).
* :func:`is_human_principal` -- tier and pairing changes are principal-only:
  an authenticated human user, never an agent token, a service or an internal
  caller.
* :func:`relay_request_refusal` -- the extension relay accepts loopback,
  non-browser callers only (the native host), before any pairing check.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from locus_runtime.computer_use.controller import get_controller
from locus_tooling import shell_confirmation
from locus_runtime.computer_use.user_browser.pairing import (
    CHROMIUM_EXTENSION_ID,
    FIREFOX_EXTENSION_ID,
    NATIVE_HOST_NAME,
)
from locus_runtime.computer_use.user_browser.relay import RelayHub, get_hub
from locus_runtime.computer_use.user_browser.sites import normalize_site
from locus_runtime.computer_use.user_browser.tiers import (
    TIER_RISKS,
    TierSettings,
    TierStore,
    get_tier_store,
    install_tier_store,
)

LOGGER = logging.getLogger(__name__)

#: Largest relay request body accepted (a PNG screenshot result, base64).
MAX_RELAY_BODY_BYTES = 16 * 1024 * 1024
_LOOPBACK_NAMES = frozenset({"localhost"})
# Headers browsers attach to page-initiated requests; the native host sends none.
_BROWSER_HEADERS = ("origin", "sec-fetch-site", "sec-fetch-mode", "referer")


#: The tier list an "Always allow on <site>" approval adds the site to: the
#: Assisted tier's allowlist (read and navigate) or the Trusted tier's grant
#: list (act). Strict and Open have no per-site list to add to.
SITE_LIST_FOR_TIER: Mapping[str, str] = {
    "assisted": "allowlisted_sites",
    "trusted": "granted_sites",
}


def _site_covered(site: str, listed: tuple[str, ...]) -> bool:
    return any(site == item or site.endswith("." + item) for item in listed)


def escalation_site_offer(
    action_kind: str, site: str, settings: TierSettings | None = None
) -> dict[str, Any]:
    """The site facts a user-browser ``ask`` carries to the approval UI.

    ``site`` is the registrable site of the tab the action targets (perceived by
    the browser, never agent text); ``browser_tier`` is the effective tier; and
    ``site_list`` names the list "Always allow on <site>" would add it to, or is
    ``None`` when the tier has no such list or the site is already on it. The
    offer only describes; adding the site is a separate, principal-only,
    shell-confirmed PUT /user-browser/tier with the full list.
    """
    if not str(action_kind or "").startswith("user_browser_"):
        return {}
    normalized = normalize_site(site)
    if not normalized:
        return {}
    current = settings if settings is not None else get_tier_store().settings
    tier = current.effective_tier
    list_key = SITE_LIST_FOR_TIER.get(tier)
    if list_key is not None and _site_covered(normalized, getattr(current, list_key)):
        list_key = None
    return {"site": normalized, "browser_tier": tier, "site_list": list_key}


def tier_store_path(app_home: Path) -> Path:
    return Path(app_home) / "computer_use" / "user-browser-tier.json"


def ensure_user_browser(app_home: Path | None) -> RelayHub:
    """Install the persisted tier store (idempotent) and return the relay hub."""
    store = get_tier_store()
    if app_home is not None and getattr(store, "_path", None) is None:
        install_tier_store(TierStore(tier_store_path(app_home)))
    hub = get_hub()
    hub.attach(get_controller())
    return hub


def is_human_principal(auth_context: Mapping[str, Any] | None) -> bool:
    """An authenticated human user: not an agent, service, NPE or internal caller."""
    if not isinstance(auth_context, Mapping) or auth_context.get("authenticated") is not True:
        return False
    if str(auth_context.get("principal_type") or "") != "user":
        return False
    if str(auth_context.get("agent_id") or "").strip():
        return False
    if auth_context.get("internal_service_authenticated") is True:
        return False
    return auth_context.get("trusted_subject_authenticated") is not True


def _is_loopback(host: str) -> bool:
    text = str(host or "").strip().lower().strip("[]")
    if text in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def relay_request_refusal(client_host: str, headers: Mapping[str, str]) -> str | None:
    """Why a relay request is refused before pairing is checked, or ``None``.

    Only the native host may call the relay: a loopback peer that is not a
    browser page (no ``Origin`` / ``Sec-Fetch-*`` / ``Referer``), so a web page
    in any browser cannot reach it even on loopback.
    """
    if not _is_loopback(client_host):
        return "not_loopback"
    lowered = {str(k).lower() for k in headers.keys()}
    if any(name in lowered for name in _BROWSER_HEADERS):
        return "browser_origin"
    return None


def cross_site_refusal(headers: Mapping[str, str], allowed_origins: list[str]) -> str | None:
    """Refuse browser requests from another site for principal-only changes.

    ``Sec-Fetch-Site: cross-site`` (every current browser sends it) or an
    ``Origin`` outside the CORS allowlist is refused. Non-browser clients (the
    desktop shell, curl) send neither and pass to normal authentication.
    """
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    if lowered.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return "cross_site"
    origin = lowered.get("origin", "").strip().rstrip("/")
    if origin and origin not in {o.rstrip("/") for o in allowed_origins}:
        return "origin_not_allowed"
    return None


def shell_proof_required(runtime_profile: str) -> bool:
    """Widening needs the desktop shell's proof on the desktop profile (loopback
    bootstrap) and wherever a shell secret was handed over. Fail closed: on the
    desktop profile with no shell secret, widening is refused."""
    return runtime_profile == "local-native" or shell_confirmation.secret_installed()


def _proof_header(headers: Mapping[str, str]) -> str | None:
    for key, value in headers.items():
        if str(key).lower() == shell_confirmation.PROOF_HEADER:
            return str(value)
    return None


def verify_tier_proof(headers: Mapping[str, str], payload: Mapping[str, Any]) -> None:
    """The shell's proof for this exact tier request (raises ``ShellProofError``).

    On the proof path the request must state both site lists, so the dialog
    the human confirmed showed every site the tier will cover.
    """
    allow, grant = payload.get("allowlisted_sites"), payload.get("granted_sites")
    if not isinstance(allow, list) or not isinstance(grant, list):
        raise shell_confirmation.ShellProofError("site_lists_required")
    tier = str(payload.get("tier") or "")
    shell_confirmation.verify(
        _proof_header(headers),
        lambda nonce, ts: shell_confirmation.tier_message(
            tier=tier, allowlisted_sites=allow, granted_sites=grant, nonce=nonce, timestamp=ts
        ),
    )


def verify_pairing_proof(headers: Mapping[str, str]) -> None:
    shell_confirmation.verify(
        _proof_header(headers),
        lambda nonce, ts: shell_confirmation.pairing_message(nonce=nonce, timestamp=ts),
    )


def status_payload(hub: RelayHub) -> dict[str, Any]:
    settings = get_tier_store().settings
    return {
        "paired": hub.paired,
        "connected": hub.connected(),
        "clients": hub.clients(),
        "native_host": NATIVE_HOST_NAME,
        "extension_ids": {"chromium": CHROMIUM_EXTENSION_ID, "firefox": FIREFOX_EXTENSION_ID},
        "tier": settings.as_dict(),
        "tier_risks": dict(TIER_RISKS),
    }


__all__ = [
    "MAX_RELAY_BODY_BYTES",
    "SITE_LIST_FOR_TIER",
    "escalation_site_offer",
    "cross_site_refusal",
    "ensure_user_browser",
    "is_human_principal",
    "relay_request_refusal",
    "shell_proof_required",
    "verify_pairing_proof",
    "verify_tier_proof",
    "status_payload",
    "tier_store_path",
]
