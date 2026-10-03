"""Backend test fixtures."""

from __future__ import annotations

import pytest


class InMemoryKeychain:
    """Stand-in keyring backend: backend tests never touch the real OS keychain."""

    priority = 5

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def in_memory_keychain(monkeypatch: pytest.MonkeyPatch) -> InMemoryKeychain:
    """Provider keys resolve env -> keychain -> DPAPI (LOCUS-336): keep all three
    hermetic -- an in-memory keychain, no real DPAPI files, no host key env vars."""
    from locus_runtime.model_client import PROVIDERS
    from locus_tooling import native_secrets

    keychain = InMemoryKeychain()
    monkeypatch.setattr(native_secrets, "_keychain_backend", lambda: keychain)
    monkeypatch.setattr(native_secrets, "_RESOLVED", {})
    monkeypatch.setattr(native_secrets, "_dpapi_read", lambda name, app_home: None)
    for spec in PROVIDERS.values():
        for name in spec.key_env:
            monkeypatch.delenv(name, raising=False)
    return keychain


@pytest.fixture(autouse=True)
def permissive_gateway():
    """Allow-all gateway double for backend suites (LOCUS-332); see tests/gateway_support.py."""
    from tests.gateway_support import AllowAllAuthorizer, installed

    with installed(AllowAllAuthorizer()) as authorizer:
        yield authorizer
