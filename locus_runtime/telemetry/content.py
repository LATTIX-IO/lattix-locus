"""Privacy for telemetry: redaction of every string, and opt-in content capture (P10).

Two layers keep secrets out of every exporter:

1. **Capture.** Message and tool content is never put on a span unless the
   principal turned content capture on (:func:`capture`). When it is on, the
   content is serialized, redacted with the platform redaction helpers
   (``locus_runtime.gateway.redact_text``: key/value and token patterns,
   private keys, URL credentials, provider key shapes) plus an optional
   extra redactor (Presidio, when the backend enables it), then truncated.
2. **Export.** Every string attribute of every span and event is redacted
   again on the batch worker before any exporter sees it
   (:func:`scrub_value`), so a secret that reached an attribute by any other
   path is masked before it leaves the process or lands in SQLite.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from typing import Any

#: Bounded length for non-content string attributes (names, reasons, errors).
ATTRIBUTE_MAX_CHARS = 512
_TRUNCATION_MARK = "…[truncated]"

# Shapes ``gateway.redact_text`` does not cover on its own (model_client keeps the
# same list for provider errors).
_EXTRA_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)([?&](?:key|api_key|token|sig|signature)=)[^&\s'\"]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (
        re.compile(r"(?i)(\bbasic\s+)(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/=]{12,}"),
        r"\1[redacted]",
    ),
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{8,}"), "[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[redacted]"),
    (re.compile(r"\b(?:lsv2|ls)_(?:pt|sk)_[A-Za-z0-9_]{16,}"), "[redacted]"),
    (re.compile(r"\b(?:pk|sk)-lf-[A-Za-z0-9-]{8,}"), "[redacted]"),
    (re.compile(r"\bxai-[A-Za-z0-9]{16,}"), "[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[redacted]"),
)

_LOCK = threading.Lock()
_EXTRA_REDACTOR: Callable[[str], str] | None = None
_KNOWN_SECRETS: tuple[str, ...] = ()


def set_extra_redactor(redactor: Callable[[str], str] | None) -> None:
    """Install (``None`` removes) an extra redactor, e.g. a Presidio anonymizer."""
    global _EXTRA_REDACTOR
    with _LOCK:
        _EXTRA_REDACTOR = redactor


def set_known_secrets(values: tuple[str, ...]) -> None:
    """Exact values to mask wherever they appear (e.g. resolved exporter auth)."""
    global _KNOWN_SECRETS
    with _LOCK:
        _KNOWN_SECRETS = tuple(v for v in values if v and len(v) >= 6)


def truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    keep = max(0, limit - len(_TRUNCATION_MARK))
    return text[:keep] + _TRUNCATION_MARK, True


def redact(text: Any, *, limit: int = ATTRIBUTE_MAX_CHARS, deep: bool = False) -> str:
    """Redacted, bounded text. Never raises; on a redactor failure the text is dropped.

    ``deep`` also runs the extra redactor (Presidio), used for captured content."""
    value = str(text if text is not None else "")
    if not value:
        return ""
    try:
        # Lazy import: the gateway imports telemetry (decision spans).
        from locus_runtime.gateway import redact_text

        for secret in _KNOWN_SECRETS:
            value = value.replace(secret, "[redacted]")
        # The gateway helper truncates; give it room, truncate here afterwards.
        value = redact_text(value, limit=max(limit * 2, limit + 64))
        for pattern, replacement in _EXTRA_PATTERNS:
            value = pattern.sub(replacement, value)
        extra = _EXTRA_REDACTOR
        if deep and extra is not None:
            value = str(extra(value))
    except Exception:  # noqa: BLE001 - fail closed: unredactable text is not exported
        return "[redaction failed]"
    return truncate(value, limit)[0]


def serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=False)
    except (TypeError, ValueError):
        return str(value)


def scrub_value(value: Any, *, limit: int = ATTRIBUTE_MAX_CHARS) -> Any:
    """Redact one attribute value (strings and string sequences); others pass."""
    if isinstance(value, str):
        return redact(value, limit=limit)
    if isinstance(value, (list, tuple)):
        return tuple(redact(v, limit=limit) if isinstance(v, str) else v for v in value)
    return value
