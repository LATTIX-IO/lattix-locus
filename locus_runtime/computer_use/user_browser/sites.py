"""Registrable sites (eTLD+1) for the user-browser tiers (LOCUS-350).

Tier lists and grants name *sites*. ``site_of("https://mail.google.com/x")`` is
``google.com``. The Public Suffix List comes from ``tldextract`` (BSD-3-Clause,
already shipped via presidio-analyzer) using its bundled snapshot only -- no
network fetch -- with the PSL *private* section on, so hosting suffixes such as
``github.io`` or ``vercel.app`` are suffixes and ``alice.github.io`` is its own
site (a grant for one tenant never covers another).

If the PSL cannot be loaded the full host is the site: matching is then
stricter, never wider.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from typing import Any
from urllib import parse as urlparse

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_EXTRACTOR: Any = None
_EXTRACTOR_FAILED = False
_MAX_SITE_CHARS = 253


def _extractor() -> Any:
    global _EXTRACTOR, _EXTRACTOR_FAILED
    with _LOCK:
        if _EXTRACTOR is None and not _EXTRACTOR_FAILED:
            try:
                import tldextract

                _EXTRACTOR = tldextract.TLDExtract(
                    cache_dir=None,
                    suffix_list_urls=(),
                    fallback_to_snapshot=True,
                    include_psl_private_domains=True,
                )
            except Exception:  # noqa: BLE001 - fall back to exact hosts (stricter)
                logger.warning("user_browser.psl_unavailable: sites are exact hosts")
                _EXTRACTOR_FAILED = True
        return _EXTRACTOR


def host_of(url: str) -> str:
    try:
        return (urlparse.urlsplit(str(url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def scheme_of(url: str) -> str:
    try:
        return urlparse.urlsplit(str(url or "").strip()).scheme.lower()
    except ValueError:
        return ""


def site_of_host(host: str) -> str:
    """eTLD+1 of ``host``; IP literals, ``localhost`` and bare suffixes are the host."""
    text = str(host or "").strip().lower().rstrip(".").strip("[]")
    if not text or len(text) > _MAX_SITE_CHARS:
        return ""
    try:
        ipaddress.ip_address(text)
        return text
    except ValueError:
        pass
    if "." not in text:
        return text
    extractor = _extractor()
    if extractor is None:
        return text
    try:
        parts = extractor(text)
    except Exception:  # noqa: BLE001 - unknown shape: the host itself (stricter)
        return text
    registered = str(getattr(parts, "top_domain_under_public_suffix", "") or "")
    if not registered:
        domain, suffix = str(parts.domain or ""), str(parts.suffix or "")
        registered = f"{domain}.{suffix}" if domain and suffix else ""
    return registered or text


def site_of(url: str) -> str:
    """Registrable site of a URL's host; empty for URLs without a host."""
    return site_of_host(host_of(url))


def normalize_site(value: str) -> str:
    """A principal-entered site ("https://Mail.Example.com/x" or "example.com") as eTLD+1."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        return site_of(text)
    return site_of_host(text.split("/", 1)[0].split(":", 1)[0])


def display_url(url: str) -> str:
    """Scheme, host and path only: query strings and fragments can carry tokens."""
    try:
        parts = urlparse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    return urlparse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:300]


__all__ = ["display_url", "host_of", "normalize_site", "scheme_of", "site_of", "site_of_host"]
