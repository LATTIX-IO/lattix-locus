"""Shared pytest fixtures for Locus tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from locus_runtime.events import reset_event_bus
from locus_runtime.orchestrator import reset_approval_store
from locus_runtime.persistence import reset_shared_state_backend
from locus_runtime.security import reset_token_caches


def _backend_main_module():
    return importlib.import_module("app.main")


@pytest.fixture(autouse=True)
def security_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
    monkeypatch.setenv("LOCUS_API_BEARER_TOKEN", "unit-test-bearer")
    monkeypatch.setenv("FEDERATION_ENABLED", "true")
    monkeypatch.setenv("FEDERATION_CLUSTER_NAME", "cluster-a")
    monkeypatch.setenv("FEDERATION_REGION", "us-east")
    monkeypatch.setenv("FEDERATION_PEERS", "https://peer-a.example.com,https://peer-b.example.com")
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "locus-state.json"))
    backend_store = _backend_main_module().store
    previous_authn = backend_store.platform_settings.require_authenticated_requests
    backend_store.platform_settings.require_authenticated_requests = True
    reset_shared_state_backend()
    reset_approval_store()
    reset_event_bus()
    reset_token_caches()
    yield
    backend_store.platform_settings.require_authenticated_requests = previous_authn
    reset_shared_state_backend()
    reset_approval_store()
    reset_event_bus()
    reset_token_caches()


@pytest.fixture()
def test_client() -> TestClient:
    return TestClient(_backend_main_module().app)


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer unit-test-bearer",
        "x-locus-actor": "test-admin",
    }


class InMemoryKeychain:
    """Stand-in for a keyring backend: tests never touch the real OS keychain."""

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
    from locus_tooling import native_secrets

    keychain = InMemoryKeychain()
    monkeypatch.setattr(native_secrets, "_keychain_backend", lambda: keychain)
    monkeypatch.setattr(native_secrets, "_RESOLVED", {})
    return keychain


@pytest.fixture(autouse=True)
def permissive_gateway():
    """Install an allow-all gateway double so suites that predate the gateway PEP
    (LOCUS-332) keep testing their own behaviour. Gateway tests install their own."""
    from tests.gateway_support import AllowAllAuthorizer, installed

    with installed(AllowAllAuthorizer()) as authorizer:
        yield authorizer
