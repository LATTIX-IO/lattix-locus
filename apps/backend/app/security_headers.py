from __future__ import annotations

import os

from typing import Any


SECURITY_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), browsing-topics=()",
    "x-permitted-cross-domain-policies": "none",
    "cross-origin-opener-policy": "same-origin",
}

HOSTED_SECURITY_HEADERS: dict[str, str] = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
}


def _runtime_profile() -> str:
    value = str(os.getenv("LOCUS_RUNTIME_PROFILE", "local-lightweight") or "").strip().lower()
    return value or "local-lightweight"


def apply_security_headers(response: Any) -> Any:
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if _runtime_profile() == "hosted":
        for header, value in HOSTED_SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
    return response
