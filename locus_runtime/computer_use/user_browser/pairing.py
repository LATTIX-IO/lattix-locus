"""Pairing between the backend and the Locus browser extension (LOCUS-350).

Trust chain:

1. The browser starts the native-messaging host only for an extension whose
   ID is in the host manifest's ``allowed_origins`` (Chromium) or
   ``allowed_extensions`` (Firefox) -- the pinned IDs below.
2. The host checks the caller origin it is given against the same pins.
3. The host authenticates to the backend relay with the **pairing key**: a
   per-install random key created when the principal pairs, stored only in
   the OS secret store via :mod:`locus_tooling.native_secrets` (keychain /
   DPAPI). It is never in the extension, its storage, a URL or a log.
4. The backend relay accepts only loopback clients with that key and a pinned
   origin; it then hands the host a short-lived session token.

Unpairing deletes the key, so every host is refused from the next request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path

#: Native-messaging host name (registry key / manifest file name).
NATIVE_HOST_NAME = "io.lattix.locus_browser"
#: Secret name in the OS secret store (never an environment variable in practice).
PAIRING_SECRET_NAME = "LOCUS_USER_BROWSER_PAIRING_KEY"

#: Public key in ``apps/browser-extension/manifest.json`` (``key``). It pins the
#: Chromium extension ID for unpacked / self-distributed builds. Only the public
#: half exists in the repo; no private key was kept.
CHROMIUM_EXTENSION_PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA9QOeGfLH4gI780+sXIO5kuVtqNzfmW0i3IcnRdBDTBWU"
    "zlJl/C+0qfDINLO3PCnPgvLPr0QJ77DhQzegyYs1rt1qAopg9jpOarPo3bf2iwcc34pCnXKjWHUc6tDyrO2DJnjS"
    "Bg/Rc6oVm4hRpt+g1IprOd2OZe1jefAJILtYUMDMdM3q3TPU77vyEbFL0dasthEOEaQzkMdO/aYt+SKcLnm9CYIR"
    "G/3SRhpuCLhMSFfCkSti3Q2nQyKrNGSx9XrJcDSsDDQoiGmVgrlRC1PFQItcpa6VoVRll5HMIWMEfuoDa/bkMeGU"
    "yeV++cKFosyVMf+UwsnTTxfs+NwFfmYcmQIDAQAB"
)
#: Firefox add-on ID (``browser_specific_settings.gecko.id``).
FIREFOX_EXTENSION_ID = "locus-browser@lattix.io"

EXTENSION_DIR = Path(__file__).resolve().parents[3] / "apps" / "browser-extension"


def chromium_extension_id(public_key_b64: str = CHROMIUM_EXTENSION_PUBLIC_KEY) -> str:
    """Chromium's extension ID for a manifest ``key``: sha256(DER)[:16] as a-p letters."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


CHROMIUM_EXTENSION_ID = chromium_extension_id()
CHROMIUM_ORIGIN = f"chrome-extension://{CHROMIUM_EXTENSION_ID}/"
#: Origins the relay and the host accept, mapped to the browser family.
ALLOWED_ORIGINS: dict[str, str] = {
    CHROMIUM_ORIGIN: "chromium",
    FIREFOX_EXTENSION_ID: "firefox",
}


def origin_family(origin: str) -> str | None:
    """``"chromium"`` / ``"firefox"`` for a pinned origin, else ``None``."""
    return ALLOWED_ORIGINS.get(str(origin or "").strip())


def keys_match(presented: str, expected: str | None) -> bool:
    """Constant-time comparison; an absent or empty expected key never matches."""
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def load_pairing_key() -> str | None:
    """The stored pairing key, or ``None`` (not paired / no secure store)."""
    from locus_tooling.native_secrets import SecretStorageUnavailable, get_secret

    try:
        return get_secret(PAIRING_SECRET_NAME)
    except SecretStorageUnavailable:
        return None


def create_pairing_key() -> str:
    """Create (or rotate) the pairing key in the OS secret store; returns it.

    Callers must never return or log the value; only the native host reads it
    back, from the same secret store.
    """
    from locus_tooling.native_secrets import generate_secret, set_secret

    value = generate_secret(32)
    set_secret(PAIRING_SECRET_NAME, value)
    return value


def delete_pairing_key() -> None:
    from locus_tooling.native_secrets import delete_secret

    delete_secret(PAIRING_SECRET_NAME)


__all__ = [
    "ALLOWED_ORIGINS",
    "CHROMIUM_EXTENSION_ID",
    "CHROMIUM_EXTENSION_PUBLIC_KEY",
    "CHROMIUM_ORIGIN",
    "EXTENSION_DIR",
    "FIREFOX_EXTENSION_ID",
    "NATIVE_HOST_NAME",
    "PAIRING_SECRET_NAME",
    "chromium_extension_id",
    "create_pairing_key",
    "delete_pairing_key",
    "keys_match",
    "load_pairing_key",
    "origin_family",
]
