"""Refuse candidate model requests that carry a secret (LOCUS-380).

The jailed RSI candidate (:mod:`.jail`) has no network; its model requests leave
the machine only through the trusted parent: the stdio bridge (:mod:`.bridge`)
relays them to the metering proxy (:mod:`.metering`), which forwards them to the
upstream (local Ollama, or a hosted NIM when configured). An AppContainer can
still read ``HKCU\\Environment`` (section 4.5 of ``docs/development/rsi-scorecard.md``),
so a secret saved there with ``setx`` could be read by the candidate and placed
in a prompt to a hosted upstream. :class:`SecretGuard` is the check the proxy runs
on every request body (and forwarded header) before it is sent upstream:

* **secret-shaped tokens**, with the platform redaction detectors
  (``locus_runtime.gateway`` and ``locus_runtime.telemetry.content``: private
  keys, provider key shapes such as ``sk-`` / ``nvapi-`` / ``AIza``, GitHub,
  Slack and AWS keys, bearer tokens, JWTs, URL credentials). A match must
  contain a digit and be at least :data:`MIN_SECRET_LENGTH` long, which keeps
  prose ("Bearer authentication") and package names from tripping the scan.
  The generic ``key=value`` redaction patterns are not used: a coding agent
  legitimately sends ``password = os.environ[...]``;
* **known secret values** the parent can resolve: the native secrets Locus
  stores (:data:`NATIVE_SECRET_NAMES` and the provider keys, read-only through
  :func:`locus_tooling.native_secrets.peek_secrets`), and secret-named
  variables (:func:`is_secret_name`) of the parent's environment and, on
  Windows, ``HKCU\\Environment``. Each value is matched as is and in simple
  encodings: base64 (standard and URL-safe, at every byte alignment, padding
  ignored), hex (lower and upper case), reversed, and URL-encoded. Whitespace
  is also removed before matching, so line-wrapped base64 still matches.

**Limits.** Values shorter than :data:`MIN_SECRET_LENGTH` (8) characters,
filesystem paths and switch words (``disabled``, ``keychain``, ...) are ignored:
they are not credentials, and matching them would refuse ordinary prompts. The
scan cannot catch an arbitrary transformation (a cipher, a split across
requests, a re-encoding chain), and nothing here sees side channels such as
token timing or request sizes.

**Handling of values.** Values are never logged, returned or stored on disk.
The guard keeps only the encoded comparators in memory (the plain value is one
of them) and :meth:`SecretGuard.wipe` drops them when the run ends; Python
strings cannot be zeroed, so this bounds their lifetime, it does not erase
them. A match reports the secret's **name** (or the detector's label), never
the value.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, quote_plus

#: Shorter values are ignored (documented false-positive guard).
MIN_SECRET_LENGTH = 8
#: Native secrets Locus stores or resolves itself (the provider keys are added
#: from ``locus_runtime.model_client.PROVIDERS``).
NATIVE_SECRET_NAMES: tuple[str, ...] = (
    "LOCUS_API_BEARER_TOKEN",
    "POSTGRES_PASSWORD",
    "A2A_JWT_SECRET",
    "LOCUS_GRANT_AUTHORITY_KEY",
    "LOCUS_USER_BROWSER_PAIRING_KEY",
    "LINEAR_API_KEY",
)
#: The Windows registry key that holds persistent user variables (``setx``).
USER_ENV_KEY = r"HKCU\Environment"
#: Variable names that hold credentials: ``*_API_KEY``, ``*_TOKEN``, ``*_SECRET``,
#: ``*PASSWORD*`` and similar. ``PYTHON_KEYRING_BACKEND``, ``MAX_TOKENS``,
#: ``LOCUS_SECRET_STORAGE_MODE`` and ``PWD`` do not match.
_SECRET_NAME = re.compile(
    r"(?:^|_)(?:KEY|APIKEY|TOKEN|SECRETS?|PAT|AUTH|CREDENTIALS?|PASSPHRASE)$"
    r"|(?:^|_)(?:API|SECRET|PRIVATE|ACCESS|SIGNING|ENCRYPTION|CLIENT)_?KEY(?:_|$)"
    r"|PASSWORD|PASSWD",
    re.IGNORECASE,
)
_SWITCH_WORDS = frozenset(
    {"disabled", "enabled", "required", "optional", "keychain", "default", "unavailable"}
)
_PATH_LIKE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/~%$])")
_WHITESPACE = re.compile(r"\s+")
_DIGIT = re.compile(r"\d")
_URLSAFE = str.maketrans("+/", "-_")
#: Nested JSON (tool-call arguments are JSON in a string) is decoded this deep.
_MAX_JSON_NESTING = 4
#: Detector labels, by a fragment of the detector's regular expression (first match wins).
_DETECTOR_LABELS: tuple[tuple[str, str], ...] = (
    ("PRIVATE KEY", "private-key"),
    ("AKIA", "aws-access-key"),
    ("github_pat", "github-token"),
    ("gh[pousr]", "github-token"),
    ("xox", "slack-token"),
    ("[?&]", "url-query-credential"),
    ("://", "url-credentials"),
    ("bearer", "bearer-token"),
    ("basic", "basic-auth"),
    ("nvapi", "nvidia-api-key"),
    ("-lf-", "langfuse-key"),
    ("lsv2", "langsmith-key"),
    ("xai-", "xai-api-key"),
    ("AIza", "google-api-key"),
    ("eyJ", "jwt"),
    ("sk-", "sk-api-key"),
)

Encoding = Literal["plain", "base64", "hex", "reversed", "url"]
EnvReader = Callable[[], Mapping[str, str]]
NativeReader = Callable[[Iterable[str]], Mapping[str, str]]


@dataclass(frozen=True)
class SecretMatch:
    """One hit: the secret's name (or detector label), never its value."""

    name: str
    kind: Literal["known", "pattern"]
    encoding: Encoding | Literal["shape"]


def is_secret_name(name: str) -> bool:
    """Whether a variable name says it holds a credential."""
    return bool(_SECRET_NAME.search(name))


def is_candidate_value(value: str) -> bool:
    """Whether a value is worth matching (long enough, not a path or a switch word)."""
    text = value.strip()
    if len(text) < MIN_SECRET_LENGTH:
        return False
    return not (_PATH_LIKE.match(text) or text.lower() in _SWITCH_WORDS)


# --------------------------------------------------------------------------- #
# Sources (values never leave this module except as comparators)
# --------------------------------------------------------------------------- #
def read_user_environment() -> dict[str, str]:
    """``HKCU\\Environment`` (persistent user variables) on Windows; ``{}`` elsewhere."""
    if sys.platform != "win32":
        return {}
    import winreg

    out: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try:
                    name, data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(name, str) and isinstance(data, str):
                    out[name] = data
    except OSError:
        return {}
    return out


def persistent_env_secret_names(reader: EnvReader | None = None) -> list[str]:
    """Names (never values) of secret-named variables in ``HKCU\\Environment``."""
    values = (reader if reader is not None else read_user_environment)()
    return sorted(name for name in values if is_secret_name(name))


def persistent_env_warning(reader: EnvReader | None = None) -> str:
    """A ``lattix loop status`` warning when ``HKCU\\Environment`` holds secret-named
    variables (names only), or ``""``."""
    names = persistent_env_secret_names(reader)
    if not names:
        return ""
    return (
        f"{USER_ENV_KEY} holds secret-named variables ({', '.join(names)}): every "
        "AppContainer, the RSI candidate included, can read persistent user variables. "
        "Move each one to the keychain with `lattix secrets set <NAME>` and remove it "
        "from the user environment."
    )


def native_secret_names() -> tuple[str, ...]:
    """The native secrets Locus may resolve: its own and every provider key name."""
    names = list(NATIVE_SECRET_NAMES)
    try:
        from locus_runtime.model_client import PROVIDERS

        for spec in PROVIDERS.values():
            names.extend(spec.key_env)
    except Exception:  # noqa: BLE001 - the static list still applies
        pass
    return tuple(dict.fromkeys(names))


def _peek_native(names: Iterable[str]) -> Mapping[str, str]:
    from locus_tooling.native_secrets import peek_secrets

    return peek_secrets(names)


# --------------------------------------------------------------------------- #
# Comparators
# --------------------------------------------------------------------------- #
def _base64_cores(raw: bytes) -> set[str]:
    """The base64 text of ``raw`` that is the same at any byte alignment in a
    longer encoded stream (standard and URL-safe alphabets, no padding)."""
    cores: set[str] = set()
    for offset in range(3):
        encoded = base64.b64encode(b"\0" * offset + raw).decode("ascii")
        start = 4 if offset else 0  # the first group mixes the prefix in
        end = len(encoded) - (4 if (offset + len(raw)) % 3 else 0)  # partial last group
        core = encoded[start:end]
        if len(core) >= MIN_SECRET_LENGTH:
            cores.add(core)
            cores.add(core.translate(_URLSAFE))
    return cores


def encodings(value: str) -> list[tuple[str, Encoding]]:
    """Every comparator for one value (plain, base64, hex, reversed, URL-encoded)."""
    raw = value.encode("utf-8")
    out: dict[str, Encoding] = {value: "plain"}
    for core in sorted(_base64_cores(raw)):
        out.setdefault(core, "base64")
    out.setdefault(raw.hex(), "hex")
    out.setdefault(raw.hex().upper(), "hex")
    out.setdefault(value[::-1], "reversed")
    out.setdefault(quote(value, safe=""), "url")
    out.setdefault(quote_plus(value, safe=""), "url")
    return [(text, kind) for text, kind in out.items() if len(text) >= MIN_SECRET_LENGTH]


def _detectors() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The platform redaction detectors that recognise a token by its shape."""
    from locus_runtime.gateway import _EXTRA_SECRET_PATTERNS
    from locus_runtime.telemetry.content import _EXTRA_PATTERNS

    out: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for pattern, _replacement in (*_EXTRA_SECRET_PATTERNS, *_EXTRA_PATTERNS):
        if pattern.pattern in seen:
            continue
        seen.add(pattern.pattern)
        label = next(
            (name for fragment, name in _DETECTOR_LABELS if fragment in pattern.pattern),
            "secret-shaped",
        )
        out.append((label, pattern))
    return tuple(out)


def _secret_part(match: re.Match[str]) -> str:
    """The token itself: detectors with a prefix group (``bearer``, ``://user:``)
    replace only what follows the group."""
    text = match.group(0)
    if match.re.groups and match.group(1):
        return text[len(match.group(1)) :]
    return text


def _strings(value: Any) -> Iterator[str]:
    """Every string (keys included) in a decoded JSON value, decoding JSON nested
    in strings (tool-call arguments). Iterative: deep nesting cannot recurse."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, str):
            yield item
            head = item.lstrip()[:1]
            if head in ("{", "[") and depth < _MAX_JSON_NESTING:
                try:
                    nested = json.loads(item)
                except ValueError:
                    continue
                stack.append((nested, depth + 1))
        elif isinstance(item, dict):
            for key, child in item.items():
                yield str(key)
                stack.append((child, depth))
        elif isinstance(item, list):
            stack.extend((child, depth) for child in item)
        elif item is not None and not isinstance(item, bool):
            yield str(item)


class SecretGuard:
    """Scans a model request for secrets before it leaves the trusted parent.

    ``known`` maps a secret's name to its value; only comparators are kept. A
    guard without ``known`` values still runs the shape detectors."""

    def __init__(self, known: Mapping[str, str] | None = None, *, patterns: bool = True) -> None:
        by_text: dict[str, tuple[set[str], Encoding]] = {}
        for name, value in dict(known or {}).items():
            if not isinstance(value, str) or not is_candidate_value(value):
                continue
            for text, encoding in encodings(value.strip()):
                names, _ = by_text.setdefault(text, (set(), encoding))
                names.add(str(name))
        self._lock = threading.Lock()
        self._known: tuple[tuple[str, tuple[str, ...], Encoding], ...] = tuple(
            (text, tuple(sorted(names)), encoding) for text, (names, encoding) in by_text.items()
        )
        self._names = tuple(sorted({n for _t, names, _e in self._known for n in names}))
        self._detectors = _detectors() if patterns else ()

    @classmethod
    def from_host(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        user_env: EnvReader | None = None,
        native: NativeReader | None = None,
        native_names: Iterable[str] | None = None,
    ) -> SecretGuard:
        """A guard armed with every secret the parent can resolve.

        ``environ`` defaults to this process's environment, ``user_env`` to
        :func:`read_user_environment` (``HKCU\\Environment``) and ``native`` to the
        read-only native secret lookup; tests inject stand-ins. A source that
        fails is skipped (the others still apply)."""
        import os

        known: dict[str, str] = {}
        env = dict(os.environ if environ is None else environ)
        known.update({k: v for k, v in env.items() if is_secret_name(k)})
        try:
            persistent = (user_env if user_env is not None else read_user_environment)()
        except Exception:  # noqa: BLE001 - an unreadable registry is no source
            persistent = {}
        for name, value in persistent.items():
            if is_secret_name(name):
                known.setdefault(f"{name} ({USER_ENV_KEY})", value)
        names = tuple(native_names if native_names is not None else native_secret_names())
        try:
            resolved = (native if native is not None else _peek_native)(names)
        except Exception:  # noqa: BLE001 - no keychain is no source
            resolved = {}
        for name, value in resolved.items():
            known.setdefault(name, value)
        return cls(known)

    @property
    def names(self) -> tuple[str, ...]:
        """The names of the armed secrets (never their values)."""
        return self._names

    def __repr__(self) -> str:
        return f"SecretGuard(known={len(self._names)}, detectors={len(self._detectors)})"

    def wipe(self) -> None:
        """Drop the comparators (end of the run); shape detectors keep working."""
        with self._lock:
            self._known = ()
            self._names = ()

    def scan_text(self, texts: Iterable[str]) -> list[SecretMatch]:
        """Matches in ``texts``. Known values are also looked for with whitespace
        removed (line-wrapped base64); shapes only in the text as sent, since
        removing whitespace would join unrelated words into one "token"."""
        parts = list(texts)
        haystack = "\x00".join(parts)
        compact = "\x00".join(_WHITESPACE.sub("", text) for text in parts)
        with self._lock:
            known = self._known
        found: dict[str, SecretMatch] = {}
        for text, names, encoding in known:
            if text in haystack or text in compact:
                for name in names:
                    found.setdefault(name, SecretMatch(name, "known", encoding))
        for label, pattern in self._detectors:
            for match in pattern.finditer(haystack):
                token = _secret_part(match)
                if len(token) >= MIN_SECRET_LENGTH and _DIGIT.search(token):
                    found.setdefault(label, SecretMatch(label, "pattern", "shape"))
                    break
        return sorted(found.values(), key=lambda m: m.name)

    def scan_request(
        self, body: bytes, headers: Mapping[str, str] | None = None
    ) -> list[SecretMatch]:
        """Matches anywhere in a request: every string of the JSON body (message
        contents, tool arguments, any field, keys too) and every header value.
        A body that is not JSON is scanned as text."""
        texts: list[str] = [str(v) for v in dict(headers or {}).values()]
        raw = body.decode("utf-8", errors="replace")
        try:
            texts.extend(_strings(json.loads(raw)))
        except (ValueError, RecursionError):
            texts.append(raw)
        return self.scan_text(texts)
