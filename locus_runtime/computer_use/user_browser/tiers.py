"""Browser autonomy tiers for the principal's own browser (LOCUS-350, D-25).

The tier is a **principal-only** setting. It lives in a process
:class:`TierStore` that only the backend's principal endpoint writes
(``PUT /user-browser/tier``); the gateway reads it for every
``user_browser_*`` action (``locus_runtime.gateway.user_browser_input``). It is
never part of a run envelope, an action or a tool argument, so neither the
agent nor page content can set it.

Widening beyond ``strict`` records informed consent (who, when, which tier,
the risk text they acknowledged); the consent record is what makes a widened
tier effective in ``policies/user_browser.rego``. Narrowing never needs
consent and clears it.

The decisions themselves are Rego's. The only tier logic here is
:func:`visible_tabs`, which filters the tab *list* (one list call is one
gateway action; each tab is not an action of its own).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from locus_runtime.computer_use.user_browser.sites import normalize_site

logger = logging.getLogger(__name__)

Tier = Literal["strict", "assisted", "trusted", "open"]
TIERS: tuple[Tier, ...] = ("strict", "assisted", "trusted", "open")
DEFAULT_TIER: Tier = "strict"
MAX_SITES = 200
_MAX_HISTORY = 50

#: What the principal acknowledges when widening (shown in the UI and stored).
TIER_RISKS: Mapping[str, str] = {
    "strict": "Every action asks; the agent only reads tabs you share.",
    "assisted": (
        "On allowlisted sites the agent reads pages and navigates without asking, using your "
        "signed-in sessions. Clicks and typing still ask."
    ),
    "trusted": (
        "On granted sites the agent clicks and types in your signed-in sessions without asking. "
        "Only irreversible actions (send, pay, purchase, delete, account or security settings) ask."
    ),
    "open": (
        "The agent acts in every tab and site of your signed-in browser without asking, including "
        "irreversible actions such as sending, paying, purchasing and deleting. A prompt injection "
        "on any page can make it do so. Secret fields, panic and audit still apply."
    ),
}


class TierChangeRefused(PermissionError):
    """The change is not allowed (not the principal, no acknowledgement, bad value)."""


@dataclass(frozen=True)
class ConsentRecord:
    tier: str
    actor: str
    principal_type: str
    recorded_at: float
    risk_acknowledged: str

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "recorded_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.recorded_at)),
        }


@dataclass(frozen=True)
class TierSettings:
    tier: Tier = DEFAULT_TIER
    allowlisted_sites: tuple[str, ...] = ()
    granted_sites: tuple[str, ...] = ()
    consent: ConsentRecord | None = None
    updated_by: str = ""
    updated_at: float = 0.0

    @property
    def tier_consent(self) -> bool:
        """A widened tier is effective only with a consent record for that tier."""
        return self.consent is not None and self.consent.tier == self.tier

    @property
    def effective_tier(self) -> Tier:
        if self.tier != "strict" and not self.tier_consent:
            return "strict"
        return self.tier

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "effective_tier": self.effective_tier,
            "allowlisted_sites": list(self.allowlisted_sites),
            "granted_sites": list(self.granted_sites),
            "consent": self.consent.as_dict() if self.consent is not None else None,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


def _site_list(values: Iterable[Any] | None) -> tuple[str, ...]:
    out: list[str] = []
    for value in values or ():
        site = normalize_site(str(value or ""))
        if site and site not in out:
            out.append(site)
        if len(out) > MAX_SITES:
            raise TierChangeRefused(f"at most {MAX_SITES} sites per list")
    return tuple(out)


def _site_covered(site: str, listed: Iterable[str]) -> bool:
    return bool(site) and any(site == item or site.endswith("." + item) for item in listed)


def visible_tabs(settings: TierSettings, tabs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Tabs the agent may see in a tab list under the effective tier.

    strict: shared tabs only; assisted: plus allowlisted (and granted) sites;
    trusted: the same lists; open: every tab.
    """
    tier = settings.effective_tier
    listed = (*settings.allowlisted_sites, *settings.granted_sites)
    out: list[dict[str, Any]] = []
    for tab in tabs:
        item = dict(tab)
        if tier == "open" or item.get("shared") is True:
            out.append(item)
        elif tier in {"assisted", "trusted"} and _site_covered(str(item.get("site") or ""), listed):
            out.append(item)
    return out


class TierStore:
    """Thread-safe holder of the principal's tier settings, optionally persisted."""

    def __init__(self, path: Path | None = None, *, clock: Any = time.time) -> None:
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._lock = threading.Lock()
        self._settings = TierSettings()
        self._history: list[dict[str, Any]] = []
        if self._path is not None:
            self._load()

    @property
    def settings(self) -> TierSettings:
        with self._lock:
            return self._settings

    @property
    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def update(
        self,
        *,
        tier: str,
        allowlisted_sites: Iterable[Any] | None = None,
        granted_sites: Iterable[Any] | None = None,
        actor: str,
        principal_type: str,
        acknowledge_risk: bool = False,
    ) -> TierSettings:
        """Set the tier and site lists. Only a human principal may call this.

        Widening beyond strict needs ``acknowledge_risk=True`` and records consent.
        Lists left as ``None`` keep their current value.
        """
        actor = str(actor or "").strip()[:128]
        if not actor or str(principal_type or "") != "user":
            raise TierChangeRefused("only the human principal can change the browser tier")
        if tier not in TIERS:
            raise TierChangeRefused(f"tier must be one of {', '.join(TIERS)}")
        with self._lock:
            current = self._settings
            allow = (
                current.allowlisted_sites
                if allowlisted_sites is None
                else _site_list(allowlisted_sites)
            )
            grant = current.granted_sites if granted_sites is None else _site_list(granted_sites)
            now = float(self._clock())
            consent: ConsentRecord | None = None
            if tier != "strict":
                widening = (
                    tier != current.tier
                    or not current.tier_consent
                    or not set(allow) <= set(current.allowlisted_sites)
                    or not set(grant) <= set(current.granted_sites)
                )
                if widening and not acknowledge_risk:
                    raise TierChangeRefused(
                        f"widening the browser tier to {tier!r} needs the principal to "
                        "acknowledge its risk (acknowledge_risk: true)"
                    )
                consent = (
                    ConsentRecord(tier, actor, "user", now, TIER_RISKS[tier])
                    if widening
                    else current.consent
                )
            settings = TierSettings(
                tier=tier,  # type: ignore[arg-type]
                allowlisted_sites=allow,
                granted_sites=grant,
                consent=consent,
                updated_by=actor,
                updated_at=now,
            )
            self._settings = settings
            self._history.append(
                {
                    "at": now,
                    "actor": actor,
                    "from_tier": current.tier,
                    "to_tier": tier,
                    "consent_recorded": consent is not None and consent is not current.consent,
                }
            )
            del self._history[:-_MAX_HISTORY]
            self._save_locked()
        logger.warning(
            "user_browser.tier_changed",
            extra={"actor": actor, "from_tier": current.tier, "to_tier": tier},
        )
        return settings

    def reset(self) -> None:
        with self._lock:
            self._settings = TierSettings()
            self._history = []
            self._save_locked()

    # -- persistence ------------------------------------------------------------
    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.warning("user_browser.tier_store_unreadable: falling back to strict")
            return
        try:
            consent_raw = raw.get("consent")
            consent = (
                ConsentRecord(
                    tier=str(consent_raw["tier"]),
                    actor=str(consent_raw["actor"]),
                    principal_type=str(consent_raw["principal_type"]),
                    recorded_at=float(consent_raw["recorded_at"]),
                    risk_acknowledged=str(consent_raw["risk_acknowledged"]),
                )
                if isinstance(consent_raw, dict)
                else None
            )
            tier = str(raw.get("tier") or DEFAULT_TIER)
            if tier not in TIERS:
                tier = DEFAULT_TIER
            self._settings = TierSettings(
                tier=tier,  # type: ignore[arg-type]
                allowlisted_sites=_site_list(raw.get("allowlisted_sites") or ()),
                granted_sites=_site_list(raw.get("granted_sites") or ()),
                consent=consent,
                updated_by=str(raw.get("updated_by") or ""),
                updated_at=float(raw.get("updated_at") or 0.0),
            )
            history = raw.get("history")
            self._history = list(history)[-_MAX_HISTORY:] if isinstance(history, list) else []
        except (KeyError, TypeError, ValueError, TierChangeRefused):
            logger.warning("user_browser.tier_store_malformed: falling back to strict")
            self._settings = TierSettings()

    def _save_locked(self) -> None:
        if self._path is None:
            return
        payload = {**self._settings.as_dict(), "history": self._history}
        payload["consent"] = asdict(self._settings.consent) if self._settings.consent else None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


# --------------------------------------------------------------------------- #
# Process-wide store
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_STORE: TierStore | None = None


def get_tier_store() -> TierStore:
    """The store the gateway reads. Defaults to an in-memory strict store."""
    global _STORE
    with _LOCK:
        if _STORE is None:
            _STORE = TierStore()
        return _STORE


def install_tier_store(store: TierStore | None) -> None:
    global _STORE
    with _LOCK:
        _STORE = store


def current_tier_settings() -> TierSettings:
    return get_tier_store().settings


__all__ = [
    "DEFAULT_TIER",
    "TIERS",
    "TIER_RISKS",
    "ConsentRecord",
    "TierChangeRefused",
    "TierSettings",
    "TierStore",
    "current_tier_settings",
    "get_tier_store",
    "install_tier_store",
    "visible_tabs",
]
