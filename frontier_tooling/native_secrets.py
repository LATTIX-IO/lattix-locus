"""Native (Dockerless) secret resolution — replaces Vault for the single-user
desktop install.

Resolution order for a secret: (1) environment variable, (2) OS keychain via the
optional ``keyring`` package, (3) a 0600 file under the app home. A missing secret
is generated once and persisted to the best available backend, so the install is
reproducible without ever committing a credential (per AGENTS.md: never store
secrets in the repo — only record that they exist and where they're retrieved).

The hosted/Docker profile keeps using Vault; this module is only wired into the
native launcher.
"""

from __future__ import annotations

import base64
import os
import secrets as _secrets
import stat
from pathlib import Path

from .common import default_app_home

_KEYRING_SERVICE = "lattix-xfrontier"


def generate_secret(nbytes: int = 48) -> str:
    """URL-safe high-entropy token (no padding)."""
    return base64.urlsafe_b64encode(_secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def _secrets_dir(app_home: Path | None = None) -> Path:
    base = (app_home or default_app_home()) / ".secrets"
    base.mkdir(parents=True, exist_ok=True)
    # Best-effort tighten dir perms on POSIX; no-op on Windows.
    if os.name != "nt":
        try:
            base.chmod(stat.S_IRWXU)  # 0700
        except OSError:
            pass
    return base


def _file_path(name: str, app_home: Path | None = None) -> Path:
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in name)
    return _secrets_dir(app_home) / f"{safe}.secret"


def _keyring_get(name: str) -> str | None:
    try:
        import keyring  # type: ignore
    except Exception:  # noqa: BLE001 - keyring is optional
        return None
    try:
        value = keyring.get_password(_KEYRING_SERVICE, name)
    except Exception:  # noqa: BLE001 - backend may be locked/unavailable
        return None
    return value or None


def _keyring_set(name: str, value: str) -> bool:
    try:
        import keyring  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, name, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def get_secret(name: str, *, app_home: Path | None = None) -> str | None:
    """Return a secret from env → keyring → file, or None if absent everywhere."""
    env_value = str(os.getenv(name) or "").strip()
    if env_value:
        return env_value
    ring = _keyring_get(name)
    if ring:
        return ring
    path = _file_path(name, app_home)
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def set_secret(name: str, value: str, *, app_home: Path | None = None) -> str:
    """Persist a secret to the keychain if available, else a 0600 file. Returns where."""
    if _keyring_set(name, value):
        return "keyring"
    path = _file_path(name, app_home)
    path.write_text(value, encoding="utf-8")
    if os.name != "nt":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    return f"file:{path}"


def ensure_secret(name: str, *, app_home: Path | None = None, nbytes: int = 48) -> str:
    """Return the existing secret or generate+persist a new one."""
    existing = get_secret(name, app_home=app_home)
    if existing:
        return existing
    value = generate_secret(nbytes)
    set_secret(name, value, app_home=app_home)
    return value
