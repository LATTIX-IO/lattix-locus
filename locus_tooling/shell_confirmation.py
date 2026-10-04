"""Out-of-band principal confirmation from the desktop shell (LOCUS-350).

On the desktop install the backend's local-operator bootstrap authenticates
any loopback request as the operator. So, for changes that **widen** what the
agent may do in the principal's own browser (a wider browser tier, more
allowlisted / granted sites, pairing a browser), principal authentication
alone is not enough there: the request must also carry a proof that the human
confirmed it in a native OS dialog shown by the Tauri shell.

Secret hand-off
---------------
At sidecar spawn the shell generates a random 32-byte secret (OS CSPRNG) and
writes it to the backend's **stdin** as one line::

    locus-shell-secret:v1:<64 hex chars>\\n

It also sets ``LOCUS_SHELL_CONFIRMATION=stdin`` (a flag, not the secret). The
frozen backend (``desktop_main``) calls :func:`receive_from_stdin` once at
startup: it reads that line, keeps the secret only in this module's memory,
then points fd 0 / ``STD_INPUT_HANDLE`` at the null device so no child process
can inherit the pipe. The secret is never put in the environment, argv, a
file or a log, and is never passed to a child.

Proof
-----
The shell sends ``X-Locus-Shell-Proof: v1:<unix ts>:<nonce hex>:<hmac hex>``
where the HMAC-SHA256 (keyed by the secret) covers a canonical message bound
to the exact request (:func:`tier_message`, :func:`pairing_message`). Proofs
expire after :data:`MAX_SKEW_S` seconds and each nonce is accepted once.

The webview UI never sees the secret: it asks the shell (Tauri command
``confirm_browser_tier`` / ``confirm_browser_pairing``), the shell shows the
dialog and, only on the human's confirm, signs and sends the request itself.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import IO

SHELL_CONFIRMATION_ENV = "LOCUS_SHELL_CONFIRMATION"
SECRET_LINE_PREFIX = "locus-shell-secret:v1:"
PROOF_HEADER = "x-locus-shell-proof"
MESSAGE_PREFIX = "locus-shell-proof/v1"
MAX_SKEW_S = 60
SECRET_BYTES = 32
READ_TIMEOUT_S = 10.0
_MAX_NONCES = 4096
_NONCE = re.compile(r"^[0-9a-f]{32,128}$")
_MAC = re.compile(r"^[0-9a-f]{64}$")
# Characters a list item may not contain (they delimit the canonical message).
_FORBIDDEN_ITEM = re.compile(r"[|,~\r\n\x00-\x1f]")


class ShellProofError(PermissionError):
    """The shell confirmation proof is missing or invalid; ``code`` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(f"shell confirmation refused: {code}")
        self.code = code


class _SecretBox:
    """Holds the secret in memory only. Its repr never shows the value."""

    __slots__ = ("_value",)

    def __init__(self) -> None:
        self._value: bytes | None = None

    def __repr__(self) -> str:
        return "<shell secret: set>" if self._value else "<shell secret: unset>"


_LOCK = threading.Lock()
_BOX = _SecretBox()
_SEEN: OrderedDict[str, float] = OrderedDict()


# --------------------------------------------------------------------------- #
# Secret hand-off
# --------------------------------------------------------------------------- #
def install_secret(secret: bytes | None) -> None:
    """Install (``None``: clear) the per-launch secret. Tests and startup only."""
    if secret is not None and len(secret) < SECRET_BYTES:
        raise ValueError("the shell secret must be at least 32 bytes")
    with _LOCK:
        _BOX._value = bytes(secret) if secret is not None else None  # noqa: SLF001
        _SEEN.clear()


def secret_installed() -> bool:
    with _LOCK:
        return _BOX._value is not None  # noqa: SLF001


def parse_secret_line(line: str) -> bytes:
    text = str(line or "").strip()
    if not text.startswith(SECRET_LINE_PREFIX):
        raise ValueError("not a shell secret line")
    raw = text[len(SECRET_LINE_PREFIX) :]
    if not re.fullmatch(r"[0-9a-fA-F]{64,256}", raw) or len(raw) % 2:
        raise ValueError("malformed shell secret")
    return bytes.fromhex(raw)


def _null_stdin() -> None:
    """Point fd 0 (and the Windows standard input handle) at the null device,
    closing the inherited pipe, so no child process can inherit it."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(devnull, 0)
    finally:
        os.close(devnull)
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        std_input_handle = -10
        kernel32.SetStdHandle(std_input_handle, msvcrt.get_osfhandle(0))
    try:
        sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - process-lifetime
    except OSError:
        sys.stdin = None


def receive_from_stdin(
    *,
    stream: IO[str] | None = None,
    timeout_s: float = READ_TIMEOUT_S,
    detach: Callable[[], None] = _null_stdin,
) -> bool:
    """Read the shell's secret line from stdin once (only when the shell said so).

    Returns whether a secret was installed. Whatever happens, when the shell
    flag is set stdin is detached afterwards, so the pipe never reaches a child.
    """
    if os.environ.get(SHELL_CONFIRMATION_ENV) != "stdin":
        return False
    source = stream if stream is not None else sys.stdin
    holder: dict[str, str] = {}

    def read() -> None:
        try:
            holder["line"] = source.readline() if source is not None else ""
        except (OSError, ValueError):
            holder["line"] = ""

    reader = threading.Thread(target=read, name="locus-shell-secret", daemon=True)
    reader.start()
    reader.join(max(0.1, timeout_s))
    try:
        secret = parse_secret_line(holder.get("line", ""))
    except ValueError:
        secret = None
    holder.clear()
    # The flag stays harmless in the environment; the secret never goes there.
    detach()
    if secret is None:
        return False
    install_secret(secret)
    return True


# --------------------------------------------------------------------------- #
# Canonical messages (mirrored byte for byte in the Tauri shell)
# --------------------------------------------------------------------------- #
def _list_field(items: Iterable[object] | None) -> str:
    if items is None:
        return "~"
    values = [str(item) for item in items]
    for value in values:
        if _FORBIDDEN_ITEM.search(value):
            raise ShellProofError("unsupported_site_text")
    return ",".join(values)


def tier_message(
    *,
    tier: str,
    allowlisted_sites: Iterable[object] | None,
    granted_sites: Iterable[object] | None,
    nonce: str,
    timestamp: int,
) -> str:
    if _FORBIDDEN_ITEM.search(str(tier)):
        raise ShellProofError("unsupported_site_text")
    return "|".join(
        (
            MESSAGE_PREFIX,
            "browser-tier",
            str(tier),
            _list_field(allowlisted_sites),
            _list_field(granted_sites),
            nonce,
            str(int(timestamp)),
        )
    )


def pairing_message(*, nonce: str, timestamp: int) -> str:
    return "|".join((MESSAGE_PREFIX, "browser-pair", nonce, str(int(timestamp))))


def sign(secret: bytes, message: str) -> str:
    return hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


def proof_header(
    secret: bytes, message_for: Callable[[str, int], str], *, nonce: str, timestamp: int
) -> str:
    """What the shell sends (used by tests; the shell computes the same in Rust)."""
    return f"v1:{timestamp}:{nonce}:{sign(secret, message_for(nonce, timestamp))}"


def parse_proof(header: str | None) -> tuple[int, str, str]:
    parts = str(header or "").strip().split(":")
    if len(parts) != 4 or parts[0] != "v1":
        raise ShellProofError("missing_proof" if not header else "malformed_proof")
    _, ts, nonce, mac = parts
    if not ts.isdigit() or not _NONCE.fullmatch(nonce) or not _MAC.fullmatch(mac):
        raise ShellProofError("malformed_proof")
    return int(ts), nonce, mac


def verify(
    header: str | None,
    message_for: Callable[[str, int], str],
    *,
    now: float | None = None,
) -> None:
    """Verify a proof bound to one request; consume its nonce. Raises :class:`ShellProofError`."""
    with _LOCK:
        secret = _BOX._value  # noqa: SLF001
    if secret is None:
        raise ShellProofError("no_shell")
    timestamp, nonce, mac = parse_proof(header)
    moment = time.time() if now is None else now
    if abs(moment - timestamp) > MAX_SKEW_S:
        raise ShellProofError("expired_proof")
    expected = sign(secret, message_for(nonce, timestamp))
    if not hmac.compare_digest(expected, mac):
        raise ShellProofError("bad_proof")
    with _LOCK:
        for seen, at in list(_SEEN.items()):
            if moment - at > 2 * MAX_SKEW_S:
                del _SEEN[seen]
        if nonce in _SEEN:
            raise ShellProofError("replayed_proof")
        _SEEN[nonce] = moment
        while len(_SEEN) > _MAX_NONCES:
            _SEEN.popitem(last=False)


__all__ = [
    "MAX_SKEW_S",
    "PROOF_HEADER",
    "SECRET_LINE_PREFIX",
    "SHELL_CONFIRMATION_ENV",
    "ShellProofError",
    "install_secret",
    "pairing_message",
    "parse_proof",
    "parse_secret_line",
    "proof_header",
    "receive_from_stdin",
    "secret_installed",
    "sign",
    "tier_message",
    "verify",
]
