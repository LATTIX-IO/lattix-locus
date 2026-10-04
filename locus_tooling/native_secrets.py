"""Native (Dockerless) secret resolution — replaces Vault for the single-user
desktop install (LOCUS-315, docs/product/13-security-architecture.md §7).

Resolution order for a secret:

1. Environment variable (operator-supplied; reported as ``env_only``).
2. OS keychain via ``keyring`` (service ``lattix-locus``): Windows Credential
   Manager, macOS Keychain, Linux Secret Service.
3. Platform fallback, used only when the keychain is unusable:

   * Windows — a DPAPI-encrypted file (user scope) under ``<app_home>/.secrets``.
   * macOS — none. Fail closed.
   * Linux — none by default. Fail closed, unless the operator opts in with
     ``LOCUS_SECRETS_ALLOW_FILE=1``, which stores a 0600 plaintext file. That
     mode is accepted security debt (LOCUS-317) and is reported as degraded.

Plaintext is never written by default on any OS. Plaintext ``<NAME>.secret``
files written by earlier builds are migrated into the protected store on first
resolution (write, verify read-back, delete) — idempotently.

A missing secret is generated once and persisted, so the install is
reproducible without ever committing a credential. Secret values are never
logged. The hosted/Docker profile keeps using Vault; this module is only wired
into the native launcher (and read by the posture report).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets as _secrets
import stat
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from .common import default_app_home

logger = logging.getLogger(__name__)

SecretStorageMode = Literal[
    "keychain", "dpapi_file", "plaintext_file_opt_in", "env_only", "unavailable"
]
SECRET_STORAGE_MODES: tuple[SecretStorageMode, ...] = (
    "keychain",
    "dpapi_file",
    "plaintext_file_opt_in",
    "env_only",
    "unavailable",
)
# Weakest first: a mixed resolution reports the weakest storage in use.
_MODE_WEAKNESS: tuple[SecretStorageMode, ...] = (
    "unavailable",
    "plaintext_file_opt_in",
    "dpapi_file",
    "env_only",
    "keychain",
)

KEYRING_SERVICE = "lattix-locus"
ALLOW_FILE_ENV = "LOCUS_SECRETS_ALLOW_FILE"
# Exported by the native launcher to the processes it supervises, so the backend
# posture report can state where the launcher stored the secrets it injected.
STORAGE_MODE_ENV = "LOCUS_SECRET_STORAGE_MODE"
_DPAPI_ENTROPY_PREFIX = b"lattix-locus/secret/"

Platform = Literal["windows", "macos", "linux"]

# name -> mode, for secrets resolved in this process.
_RESOLVED: dict[str, SecretStorageMode] = {}


class SecretStorageUnavailable(RuntimeError):
    """No secure secret store is usable; the caller must fail closed."""


def generate_secret(nbytes: int = 48) -> str:
    """URL-safe high-entropy token (no padding)."""
    return base64.urlsafe_b64encode(_secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


# --- platform / policy -----------------------------------------------------


def _platform() -> Platform:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _file_opt_in() -> bool:
    return str(os.getenv(ALLOW_FILE_ENV) or "").strip().lower() in {"1", "true", "yes"}


def _unavailable_message(name: str, platform: Platform) -> str:
    if platform == "macos":
        return (
            f"Cannot store secret {name}: no usable macOS Keychain backend. Locus does not "
            "store secrets outside the Keychain on macOS. Unlock the login keychain, or "
            f"supply {name} through the environment."
        )
    if platform == "windows":
        return f"Cannot store secret {name}: neither Credential Manager nor DPAPI is usable."
    return (
        f"Cannot store secret {name}: no usable Secret Service backend (GNOME Keyring, "
        "KWallet or another org.freedesktop.secrets provider over D-Bus). Start a Secret "
        f"Service provider, supply {name} through the environment, or set "
        f"{ALLOW_FILE_ENV}=1 to accept a 0600 plaintext file under <app_home>/.secrets "
        "(tracked security debt LOCUS-317; Posture reports secret storage as degraded)."
    )


# --- file layout -------------------------------------------------------------


def _secrets_dir(app_home: Path | None = None) -> Path:
    base = (app_home or default_app_home()) / ".secrets"
    base.mkdir(parents=True, exist_ok=True)
    # Tighten dir perms on POSIX; no-op on Windows (DPAPI protects the content).
    if os.name != "nt":
        try:
            base.chmod(stat.S_IRWXU)  # 0700
        except OSError:
            pass
    return base


def _safe_name(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in name)


def _plaintext_path(name: str, app_home: Path | None = None) -> Path:
    """Legacy layout, and the Linux opt-in store: ``<app_home>/.secrets/<NAME>.secret``."""
    return _secrets_dir(app_home) / f"{_safe_name(name)}.secret"


def _dpapi_path(name: str, app_home: Path | None = None) -> Path:
    return _secrets_dir(app_home) / f"{_safe_name(name)}.dpapi"


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None


# --- OS keychain (keyring) ---------------------------------------------------


def _load_keyring() -> Any | None:
    try:
        import keyring
    except Exception:  # noqa: BLE001 - absent/broken install => no keychain
        return None
    return keyring


def _is_secure_backend(backend: Any) -> bool:
    """Reject keyring's fail/null backends and keyrings.alt file backends."""
    module = type(backend).__module__
    if module.startswith("keyrings.alt") or module in {
        "keyring.backends.fail",
        "keyring.backends.null",
    }:
        return False
    try:
        if float(backend.priority) <= 0:
            return False
    except Exception:  # noqa: BLE001 - a backend that cannot rank itself is unusable
        return False
    if module == "keyring.backends.chainer":
        chained = list(getattr(backend, "backends", []) or [])
        return bool(chained) and all(_is_secure_backend(item) for item in chained)
    return True


def _keychain_backend() -> Any | None:
    """The active keyring backend, or None when no secure OS keychain is usable."""
    keyring = _load_keyring()
    if keyring is None:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - backend discovery failed => unusable
        return None
    return backend if _is_secure_backend(backend) else None


def _keychain_get(backend: Any, name: str) -> str | None:
    try:
        value = backend.get_password(KEYRING_SERVICE, name)
    except Exception as exc:  # noqa: BLE001 - locked/unavailable keychain
        logger.warning("keychain read failed for %s: %s", name, type(exc).__name__)
        return None
    return value or None


def _keychain_put(backend: Any, name: str, value: str) -> bool:
    """Write to the keychain and verify read-back. False on any failure."""
    try:
        backend.set_password(KEYRING_SERVICE, name, value)
    except Exception as exc:  # noqa: BLE001 - locked/unavailable keychain
        logger.warning("keychain write failed for %s: %s", name, type(exc).__name__)
        return False
    return _keychain_get(backend, name) == value


# --- Windows DPAPI (user scope) ------------------------------------------------


def _dpapi_entropy(name: str) -> bytes:
    return _DPAPI_ENTROPY_PREFIX + name.encode("utf-8")


def _dpapi_call(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData via ctypes (Windows only)."""
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(raw: bytes) -> tuple[_Blob, Any]:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_blob, _in_buf = _blob(data)
    entropy_blob, _entropy_buf = _blob(entropy)
    out_blob = _Blob()
    cryptprotect_ui_forbidden = 0x1  # never prompt; user scope (no LOCAL_MACHINE flag)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        cryptprotect_ui_forbidden,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _dpapi_protect(plaintext: bytes, entropy: bytes) -> bytes:
    return _dpapi_call(plaintext, entropy, protect=True)


def _dpapi_unprotect(ciphertext: bytes, entropy: bytes) -> bytes:
    return _dpapi_call(ciphertext, entropy, protect=False)


def _dpapi_read(name: str, app_home: Path | None) -> str | None:
    path = _dpapi_path(name, app_home)
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        value = _dpapi_unprotect(blob, _dpapi_entropy(name)).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - wrong user/corrupt file: fail closed
        raise SecretStorageUnavailable(
            f"Cannot decrypt {path} for {name} ({type(exc).__name__}); it was written by a "
            "different Windows user or is corrupt."
        ) from exc
    return value or None


def _dpapi_write(name: str, value: str, app_home: Path | None) -> None:
    path = _dpapi_path(name, app_home)
    try:
        blob = _dpapi_protect(value.encode("utf-8"), _dpapi_entropy(name))
    except Exception as exc:  # noqa: BLE001 - never fall through to plaintext
        raise SecretStorageUnavailable(
            f"Cannot store secret {name}: Credential Manager and DPAPI both failed "
            f"({type(exc).__name__})."
        ) from exc
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)
    if _dpapi_read(name, app_home) != value:
        raise SecretStorageUnavailable(f"DPAPI read-back verification failed for {name}")


# --- Linux opt-in plaintext (LOCUS-317) ----------------------------------------


def _plaintext_write(name: str, value: str, app_home: Path | None) -> None:
    path = _plaintext_path(name, app_home)
    # Create 0600 up front so the file is never briefly group/world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    # The secret's name is not logged: it is metadata an attacker reading logs
    # could use to target the file; Posture lists which entries are degraded.
    logger.warning(
        "Stored a secret as a 0600 PLAINTEXT file because %s=1 and no Secret Service "
        "is available (accepted security debt LOCUS-317; Posture reports degraded).",
        ALLOW_FILE_ENV,
    )


# --- store selection -------------------------------------------------------------


def _read_protected(
    name: str, app_home: Path | None
) -> tuple[str | None, SecretStorageMode | None]:
    backend = _keychain_backend()
    if backend is not None:
        value = _keychain_get(backend, name)
        if value:
            return value, "keychain"
    if _platform() == "windows":
        value = _dpapi_read(name, app_home)
        if value:
            return value, "dpapi_file"
    return None, None


def _write_protected(name: str, value: str, app_home: Path | None) -> SecretStorageMode | None:
    """Persist to keychain, else (Windows) DPAPI file. None if neither applies."""
    backend = _keychain_backend()
    if backend is not None and _keychain_put(backend, name, value):
        return "keychain"
    if _platform() == "windows":
        _dpapi_write(name, value, app_home)
        return "dpapi_file"
    return None


def _plaintext_opt_in_active() -> bool:
    return _platform() == "linux" and _file_opt_in()


def _fail_closed(name: str, prefix: str = "") -> SecretStorageUnavailable:
    _RESOLVED[name] = "unavailable"
    return SecretStorageUnavailable(prefix + _unavailable_message(name, _platform()))


def _migrate_legacy(
    name: str, legacy: Path, app_home: Path | None, current: str | None
) -> str | None:
    """Move a legacy plaintext secret into the protected store; delete the file.

    Returns the migrated value, or None when nothing was migrated (no usable
    legacy value, the protected store already held one, or — Linux opt-in with
    no Secret Service — the legacy file *is* the store). Raises when no store is
    usable at all.
    """
    legacy_value = _read_text(legacy)
    if current is not None:
        if legacy_value is None or legacy_value == current:
            legacy.unlink(missing_ok=True)
            logger.info("Removed redundant legacy plaintext secret file for %s", name)
        else:
            # Earlier builds resolved keychain before file, so this file was never
            # used — but it may still be someone's credential: keep it and say so.
            logger.warning(
                "Legacy plaintext secret file for %s differs from the stored value and "
                "was left in place at %s; delete it once confirmed unused.",
                name,
                legacy,
            )
        return None
    if legacy_value is None:
        return None
    mode = _write_protected(name, legacy_value, app_home)
    if mode is None:
        if _plaintext_opt_in_active():
            return None
        raise _fail_closed(
            name, f"A legacy plaintext secret file exists at {legacy} but cannot be migrated. "
        )
    stored, stored_mode = _read_protected(name, app_home)
    if stored != legacy_value or stored_mode != mode:
        raise SecretStorageUnavailable(
            f"Migration of {name} failed read-back verification; the legacy file was kept."
        )
    legacy.unlink()
    logger.info("Migrated legacy plaintext secret %s to %s and deleted the file", name, mode)
    _RESOLVED[name] = mode
    return legacy_value


# --- public API ------------------------------------------------------------------


def get_secret(name: str, *, app_home: Path | None = None) -> str | None:
    """Return a secret from env → keychain → platform fallback, or None if absent.

    Raises :class:`SecretStorageUnavailable` when the secret is not in the
    environment and no secure store is usable on this platform (fail closed).
    """
    env_value = str(os.getenv(name) or "").strip()
    if env_value:
        _RESOLVED[name] = "env_only"
        return env_value

    value, mode = _read_protected(name, app_home)
    legacy = _plaintext_path(name, app_home)
    if legacy.exists():
        migrated = _migrate_legacy(name, legacy, app_home, value)
        if migrated is not None:
            return migrated
    if value is not None and mode is not None:
        _RESOLVED[name] = mode
        return value

    if _keychain_backend() is None and _platform() != "windows":
        if not _plaintext_opt_in_active():
            raise _fail_closed(name)
        text = _read_text(legacy)
        if text:
            logger.warning("Read %s from a PLAINTEXT file (%s=1, LOCUS-317).", name, ALLOW_FILE_ENV)
            _RESOLVED[name] = "plaintext_file_opt_in"
            return text
    return None


def peek_secrets(names: Iterable[str], *, app_home: Path | None = None) -> dict[str, str]:
    """Read-only lookup of several secrets: env → keychain → DPAPI or plaintext file.

    For scanners that must know a value to refuse it (LOCUS-380, the RSI
    candidate's model requests). Unlike :func:`get_secret` it never migrates,
    creates directories, writes, raises or records the resolution for the
    posture report. Absent or unreadable secrets are left out. Values are never
    logged."""
    found: dict[str, str] = {}
    pending = [n for n in dict.fromkeys(names) if n]
    for name in pending:
        env_value = str(os.getenv(name) or "").strip()
        if env_value:
            found[name] = env_value
    pending = [n for n in pending if n not in found]
    if not pending:
        return found
    backend = _keychain_backend()
    secrets_dir = (app_home or default_app_home()) / ".secrets"
    for name in pending:
        value: str | None = None
        if backend is not None:
            value = _keychain_get(backend, name)
        if not value and _platform() == "windows":
            path = secrets_dir / f"{_safe_name(name)}.dpapi"
            try:
                if path.is_file():
                    value = _dpapi_unprotect(path.read_bytes(), _dpapi_entropy(name)).decode(
                        "utf-8"
                    )
            except Exception:  # noqa: BLE001 - wrong user / corrupt blob: not readable
                value = None
        if not value:
            try:
                value = _read_text(secrets_dir / f"{_safe_name(name)}.secret")
            except OSError:
                value = None
        if value:
            found[name] = value
    return found


def set_secret(name: str, value: str, *, app_home: Path | None = None) -> SecretStorageMode:
    """Persist a secret to the strongest usable store. Returns the storage mode.

    Raises :class:`SecretStorageUnavailable` rather than writing plaintext,
    except for the explicit Linux ``LOCUS_SECRETS_ALLOW_FILE=1`` opt-in.
    """
    mode = _write_protected(name, value, app_home)
    if mode is None:
        if not _plaintext_opt_in_active():
            raise _fail_closed(name)
        _plaintext_write(name, value, app_home)
        mode = "plaintext_file_opt_in"
    _RESOLVED[name] = mode
    return mode


def delete_secret(name: str, *, app_home: Path | None = None) -> None:
    """Remove a stored secret from every protected store (keychain, DPAPI, opt-in file).

    Idempotent: a secret that is not stored is not an error. Environment
    variables are the operator's and are never touched. Raises
    :class:`SecretStorageUnavailable` only when a stored copy exists but cannot
    be removed (the caller must not report it as cleared).
    """
    backend = _keychain_backend()
    if backend is not None and _keychain_get(backend, name) is not None:
        try:
            delete = getattr(backend, "delete_password", None)
            if callable(delete):
                delete(KEYRING_SERVICE, name)
            else:  # pragma: no cover - every keyring backend implements delete
                backend.set_password(KEYRING_SERVICE, name, "")
        except Exception as exc:  # noqa: BLE001 - locked keychain: fail loudly
            raise SecretStorageUnavailable(
                f"Cannot remove secret {name} from the keychain ({type(exc).__name__})."
            ) from exc
        if _keychain_get(backend, name) is not None:
            raise SecretStorageUnavailable(f"Secret {name} is still present in the keychain.")
    # File stores: look without creating the secrets directory.
    secrets_dir = (app_home or default_app_home()) / ".secrets"
    for suffix in (".dpapi", ".secret"):
        (secrets_dir / f"{_safe_name(name)}{suffix}").unlink(missing_ok=True)
    _RESOLVED.pop(name, None)


def ensure_secret(name: str, *, app_home: Path | None = None, nbytes: int = 48) -> str:
    """Return the existing secret or generate+persist a new one (fail closed)."""
    existing = get_secret(name, app_home=app_home)
    if existing:
        return existing
    value = generate_secret(nbytes)
    set_secret(name, value, app_home=app_home)
    return value


def _weakest(modes: Iterable[SecretStorageMode]) -> SecretStorageMode | None:
    present = set(modes)
    for mode in _MODE_WEAKNESS:
        if mode in present:
            return mode
    return None


def secret_storage_mode(names: Iterable[str] | None = None) -> SecretStorageMode:
    """Where this process's native secrets live, for the posture report.

    With ``names``, reports the weakest storage among those secrets as resolved
    in this process. Without, uses every secret resolved in this process; if
    none were, falls back to the mode the native launcher exported
    (``LOCUS_SECRET_STORAGE_MODE``) — a ``keychain`` claim is only accepted when
    a secure keychain backend is usable here — and otherwise ``env_only``.
    """
    if names is not None:
        recorded = [_RESOLVED[name] for name in names if name in _RESOLVED]
    else:
        recorded = list(_RESOLVED.values())
    weakest = _weakest(recorded)
    if weakest is not None:
        return weakest
    declared = str(os.getenv(STORAGE_MODE_ENV) or "").strip()
    for mode in SECRET_STORAGE_MODES:
        if declared == mode:
            if mode == "keychain" and _keychain_backend() is None:
                return "env_only"
            return mode
    return "env_only"
