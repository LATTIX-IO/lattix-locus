"""The process facts the ``user_browser`` policy evaluates (LOCUS-350).

Read by the gateway for every ``user_browser_*`` action. Everything here is
state the principal controls (tier store), the relay (pairing) or the panic
latch -- nothing from the action, the run envelope or page content.
"""

from __future__ import annotations

from typing import Any

from locus_runtime.computer_use.controller import get_controller
from locus_runtime.computer_use.user_browser.relay import get_hub
from locus_runtime.computer_use.user_browser.tiers import current_tier_settings


def user_browser_snapshot() -> dict[str, Any]:
    settings = current_tier_settings()
    try:
        paired = get_hub().connected()
    except Exception:  # noqa: BLE001 - unknown pairing state is "not paired"
        paired = False
    return {
        "tier": settings.tier,
        "tier_consent": settings.tier_consent,
        "allowlisted_sites": list(settings.allowlisted_sites),
        "granted_sites": list(settings.granted_sites),
        "extension_paired": paired,
        "panicked": get_controller().panicked,
    }


__all__ = ["user_browser_snapshot"]
