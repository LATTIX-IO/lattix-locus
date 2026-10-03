"""LOCUS-315: native secrets resolve env -> OS keychain -> platform fallback, never
write plaintext by default, and migrate legacy plaintext files.

The real OS keychain is never touched: tests/conftest.py installs an in-memory
keychain, and individual tests replace it with an unusable one where needed.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from locus_tooling import native_secrets as ns  # noqa: E402

NAME = "LOCUS_TEST_SECRET"


class _BrokenKeychain:
    """A keyring backend whose writes fail (locked / no Credential Manager)."""

    priority = 5

    def get_password(self, service: str, username: str) -> str | None:
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("keychain locked")


def _fake_dpapi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reversible stand-in for DPAPI so Windows-fallback logic runs on any OS."""
    monkeypatch.setattr(ns, "_dpapi_protect", lambda data, entropy: b"DPAPI:" + data[::-1])
    monkeypatch.setattr(
        ns,
        "_dpapi_unprotect",
        lambda blob, entropy: blob.removeprefix(b"DPAPI:")[::-1],
    )


def _no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "_keychain_backend", lambda: None)


def _legacy(tmp_path: Path, value: str) -> Path:
    path = tmp_path / ".secrets" / f"{NAME}.secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _files_containing(root: Path, value: str) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and value.encode() in p.read_bytes()]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (NAME, ns.ALLOW_FILE_ENV, ns.STORAGE_MODE_ENV):
        monkeypatch.delenv(var, raising=False)


# --- resolution order ----------------------------------------------------------


def test_env_wins_over_keychain(monkeypatch, tmp_path, in_memory_keychain):
    in_memory_keychain.set_password(ns.KEYRING_SERVICE, NAME, "from-keychain")
    monkeypatch.setenv(NAME, "from-env")
    assert ns.get_secret(NAME, app_home=tmp_path) == "from-env"
    assert ns.secret_storage_mode([NAME]) == "env_only"


def test_keychain_used_when_env_absent(tmp_path, in_memory_keychain):
    in_memory_keychain.set_password(ns.KEYRING_SERVICE, NAME, "from-keychain")
    assert ns.get_secret(NAME, app_home=tmp_path) == "from-keychain"
    assert ns.secret_storage_mode([NAME]) == "keychain"


def test_ensure_secret_persists_to_keychain_only(tmp_path, in_memory_keychain):
    value = ns.ensure_secret(NAME, app_home=tmp_path)
    assert in_memory_keychain.store[(ns.KEYRING_SERVICE, NAME)] == value
    assert ns.ensure_secret(NAME, app_home=tmp_path) == value
    assert _files_containing(tmp_path, value) == []


def test_keychain_preferred_over_dpapi_file(monkeypatch, tmp_path, in_memory_keychain):
    monkeypatch.setattr(ns, "_platform", lambda: "windows")
    _fake_dpapi(monkeypatch)
    ns._dpapi_write(NAME, "from-dpapi", tmp_path)
    in_memory_keychain.set_password(ns.KEYRING_SERVICE, NAME, "from-keychain")
    assert ns.get_secret(NAME, app_home=tmp_path) == "from-keychain"


# --- platform fallbacks ------------------------------------------------------------


def test_windows_falls_back_to_dpapi_file_when_credential_manager_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_platform", lambda: "windows")
    monkeypatch.setattr(ns, "_keychain_backend", lambda: _BrokenKeychain())
    _fake_dpapi(monkeypatch)
    value = ns.ensure_secret(NAME, app_home=tmp_path)
    assert ns.secret_storage_mode([NAME]) == "dpapi_file"
    assert (tmp_path / ".secrets" / f"{NAME}.dpapi").exists()
    assert _files_containing(tmp_path, value) == []
    assert ns.ensure_secret(NAME, app_home=tmp_path) == value


def test_macos_fails_closed_without_keychain(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_platform", lambda: "macos")
    _no_keychain(monkeypatch)
    monkeypatch.setenv(ns.ALLOW_FILE_ENV, "1")  # the opt-in is Linux-only
    with pytest.raises(ns.SecretStorageUnavailable, match="Keychain"):
        ns.ensure_secret(NAME, app_home=tmp_path)
    assert ns.secret_storage_mode([NAME]) == "unavailable"
    assert not list(tmp_path.rglob("*.secret"))


def test_linux_fails_closed_without_secret_service(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_platform", lambda: "linux")
    _no_keychain(monkeypatch)
    with pytest.raises(ns.SecretStorageUnavailable) as excinfo:
        ns.ensure_secret(NAME, app_home=tmp_path)
    assert ns.ALLOW_FILE_ENV in str(excinfo.value)
    assert "LOCUS-317" in str(excinfo.value)
    assert not list(tmp_path.rglob("*.secret"))


def test_linux_keychain_write_failure_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_platform", lambda: "linux")
    monkeypatch.setattr(ns, "_keychain_backend", lambda: _BrokenKeychain())
    with pytest.raises(ns.SecretStorageUnavailable):
        ns.ensure_secret(NAME, app_home=tmp_path)
    assert not list(tmp_path.rglob("*.secret"))


def test_linux_opt_in_writes_0600_plaintext_and_warns(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ns, "_platform", lambda: "linux")
    _no_keychain(monkeypatch)
    monkeypatch.setenv(ns.ALLOW_FILE_ENV, "1")
    with caplog.at_level(logging.WARNING, logger=ns.__name__):
        value = ns.ensure_secret(NAME, app_home=tmp_path)
    path = tmp_path / ".secrets" / f"{NAME}.secret"
    assert path.read_text(encoding="utf-8") == value
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "LOCUS-317" in caplog.text
    assert value not in caplog.text
    assert ns.secret_storage_mode([NAME]) == "plaintext_file_opt_in"
    # Idempotent: the opt-in file is the store and is read back, not regenerated.
    assert ns.ensure_secret(NAME, app_home=tmp_path) == value


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_no_plaintext_file_written_by_default(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(ns, "_platform", lambda: platform)
    _no_keychain(monkeypatch)
    _fake_dpapi(monkeypatch)
    try:
        value: str | None = ns.ensure_secret(NAME, app_home=tmp_path)
    except ns.SecretStorageUnavailable:
        value = None
    assert (value is not None) == (platform == "windows")
    assert not list(tmp_path.rglob("*.secret"))
    if value is not None:
        assert _files_containing(tmp_path, value) == []


# --- legacy plaintext migration ---------------------------------------------------


def test_legacy_file_migrates_to_keychain_and_is_deleted(tmp_path, in_memory_keychain):
    legacy = _legacy(tmp_path, "legacy-value")
    assert ns.get_secret(NAME, app_home=tmp_path) == "legacy-value"
    assert in_memory_keychain.store[(ns.KEYRING_SERVICE, NAME)] == "legacy-value"
    assert not legacy.exists()
    assert ns.secret_storage_mode([NAME]) == "keychain"
    # Idempotent: a second resolution reads the keychain; nothing to migrate.
    assert ns.get_secret(NAME, app_home=tmp_path) == "legacy-value"


def test_legacy_file_migrates_to_dpapi_on_windows_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_platform", lambda: "windows")
    monkeypatch.setattr(ns, "_keychain_backend", lambda: _BrokenKeychain())
    _fake_dpapi(monkeypatch)
    legacy = _legacy(tmp_path, "legacy-value")
    assert ns.get_secret(NAME, app_home=tmp_path) == "legacy-value"
    assert not legacy.exists()
    assert ns._dpapi_read(NAME, tmp_path) == "legacy-value"
    assert _files_containing(tmp_path, "legacy-value") == []


def test_legacy_file_kept_when_read_back_does_not_match(monkeypatch, tmp_path):
    class _LossyKeychain(_BrokenKeychain):
        def set_password(self, service: str, username: str, password: str) -> None:
            self.saved = password[:-1]

        def get_password(self, service: str, username: str) -> str | None:
            return getattr(self, "saved", None)

    monkeypatch.setattr(ns, "_platform", lambda: "macos")
    lossy = _LossyKeychain()
    monkeypatch.setattr(ns, "_keychain_backend", lambda: lossy)
    legacy = _legacy(tmp_path, "legacy-value")
    with pytest.raises(ns.SecretStorageUnavailable):
        ns.get_secret(NAME, app_home=tmp_path)
    assert legacy.exists()


def test_legacy_file_matching_keychain_is_removed(tmp_path, in_memory_keychain):
    in_memory_keychain.set_password(ns.KEYRING_SERVICE, NAME, "same")
    legacy = _legacy(tmp_path, "same")
    assert ns.get_secret(NAME, app_home=tmp_path) == "same"
    assert not legacy.exists()


def test_legacy_file_differing_from_keychain_is_kept(tmp_path, in_memory_keychain):
    in_memory_keychain.set_password(ns.KEYRING_SERVICE, NAME, "current")
    legacy = _legacy(tmp_path, "stale")
    assert ns.get_secret(NAME, app_home=tmp_path) == "current"
    assert legacy.exists()


def test_legacy_file_without_secure_store_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_platform", lambda: "linux")
    _no_keychain(monkeypatch)
    legacy = _legacy(tmp_path, "legacy-value")
    with pytest.raises(ns.SecretStorageUnavailable, match="legacy"):
        ns.get_secret(NAME, app_home=tmp_path)
    assert legacy.exists()


# --- keyring backend vetting -------------------------------------------------------


def _backend(module: str, priority: float = 5, **attrs: object) -> object:
    cls = type("Backend", (), {"priority": priority, "__module__": module, **attrs})
    return cls()


@pytest.mark.parametrize(
    ("backend", "secure"),
    [
        (_backend("keyring.backends.Windows"), True),
        (_backend("keyring.backends.SecretService"), True),
        (_backend("keyring.backends.fail", priority=0), False),
        (_backend("keyring.backends.null", priority=-1), False),
        (_backend("keyrings.alt.file"), False),
        (_backend("keyring.backends.chainer", backends=[]), False),
        (
            _backend("keyring.backends.chainer", backends=[_backend("keyring.backends.libsecret")]),
            True,
        ),
        (
            _backend("keyring.backends.chainer", backends=[_backend("keyrings.alt.file")]),
            False,
        ),
    ],
)
def test_backend_vetting_rejects_insecure_keyrings(backend, secure):
    assert ns._is_secure_backend(backend) is secure


# --- posture reporting ---------------------------------------------------------------


def test_storage_mode_reports_weakest(tmp_path, monkeypatch, in_memory_keychain):
    in_memory_keychain.set_password(ns.KEYRING_SERVICE, "A", "a")
    monkeypatch.setenv("B", "b")
    ns.get_secret("A", app_home=tmp_path)
    ns.get_secret("B", app_home=tmp_path)
    assert ns.secret_storage_mode(["A"]) == "keychain"
    assert ns.secret_storage_mode(["A", "B"]) == "env_only"


def test_storage_mode_uses_launcher_declaration(monkeypatch):
    assert ns.secret_storage_mode() == "env_only"
    monkeypatch.setenv(ns.STORAGE_MODE_ENV, "plaintext_file_opt_in")
    assert ns.secret_storage_mode() == "plaintext_file_opt_in"
    monkeypatch.setenv(ns.STORAGE_MODE_ENV, "not-a-mode")
    assert ns.secret_storage_mode() == "env_only"


# --- real DPAPI (Windows only; no keychain involved) ------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_round_trip_on_windows(tmp_path):
    ns._dpapi_write(NAME, "round-trip-value", tmp_path)
    blob = (tmp_path / ".secrets" / f"{NAME}.dpapi").read_bytes()
    assert b"round-trip-value" not in blob
    assert ns._dpapi_read(NAME, tmp_path) == "round-trip-value"
    # Entropy binds the blob to the secret name.
    with pytest.raises(OSError):
        ns._dpapi_unprotect(blob, ns._dpapi_entropy("OTHER_NAME"))
