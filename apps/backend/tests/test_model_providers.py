"""Focused tests for multi-provider model routing and local-model management."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import app

client = TestClient(app)

READ_HEADERS = {"x-frontier-actor": "tester"}
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-frontier-actor": "frontier-admin"}


def test_resolve_chat_provider_prefixes() -> None:
    assert main_module._resolve_chat_provider("gpt-5.2") == ("openai", "gpt-5.2")
    assert main_module._resolve_chat_provider("") == ("openai", "")
    assert main_module._resolve_chat_provider("nim/meta/llama-3.3-70b-instruct") == (
        "nim",
        "meta/llama-3.3-70b-instruct",
    )
    assert main_module._resolve_chat_provider("ollama/llama3.2:3b") == ("ollama", "llama3.2:3b")
    assert main_module._resolve_chat_provider("OLLAMA/qwen2.5:0.5b") == ("ollama", "qwen2.5:0.5b")
    assert main_module._resolve_chat_provider("anthropic/claude-sonnet-4-6") == (
        "anthropic",
        "claude-sonnet-4-6",
    )
    assert main_module._resolve_chat_provider("azure/my-deployment") == ("azure", "my-deployment")
    assert main_module._resolve_chat_provider("google/gemini-2.5-pro") == (
        "google",
        "gemini-2.5-pro",
    )
    assert main_module._resolve_chat_provider("mistral/mistral-large-latest")[0] == "mistral"
    assert main_module._resolve_chat_provider("xai/grok-4")[0] == "xai"


def test_unified_provider_map_resolution_and_masking(monkeypatch) -> None:
    # The host may export provider env vars (e.g. Claude Code's own
    # ANTHROPIC_BASE_URL); remove them so the registry fallback is exercised.
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = main_module.store.platform_settings
    original = dict(settings.ai_providers)
    try:
        settings.ai_providers = {
            "anthropic": {
                "api_key": "sk-ant-test-000111",
                "default_model": "claude-opus-4-8",
            },
            "azure": {
                "api_key": "azure-test-key",
                "base_url": "https://myresource.openai.azure.com/openai/v1",
            },
        }
        assert main_module._provider_api_key("anthropic") == "sk-ant-test-000111"
        assert main_module._provider_default_model("anthropic") == "claude-opus-4-8"
        assert main_module._provider_configured("anthropic") is True
        assert (
            main_module._provider_base_url("azure")
            == "https://myresource.openai.azure.com/openai/v1"
        )
        # Anthropic base URL falls back to the registry default.
        assert main_module._provider_base_url("anthropic") == "https://api.anthropic.com/v1"

        response = client.get("/platform/settings", headers=ADMIN_HEADERS)
        body = response.json()
        assert body["ai_providers"]["anthropic"]["api_key"] == ""
        assert body["ai_providers"]["anthropic"]["api_key_configured"] is True
        assert "sk-ant-test" not in response.text
    finally:
        settings.ai_providers = original


def test_ai_providers_save_merges_and_respects_secret_semantics() -> None:
    settings = main_module.store.platform_settings
    original = dict(settings.ai_providers)
    try:
        first = client.post(
            "/platform/settings",
            json={
                "ai_providers": {
                    "google": {"api_key": "google-key-1", "default_model": "gemini-2.5-pro"}
                },
                "confirm_security_change": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert first.status_code == 200
        # Settings saves replace the model instance; always re-read the store.
        assert (
            main_module.store.platform_settings.ai_providers["google"]["api_key"] == "google-key-1"
        )

        # Blank key keeps the stored value; other fields update.
        client.post(
            "/platform/settings",
            json={
                "ai_providers": {"google": {"api_key": "", "default_model": "gemini-2.5-flash"}},
                "confirm_security_change": True,
            },
            headers=ADMIN_HEADERS,
        )
        current = main_module.store.platform_settings.ai_providers["google"]
        assert current["api_key"] == "google-key-1"
        assert current["default_model"] == "gemini-2.5-flash"

        # Clear sentinel removes the key; unknown providers are ignored.
        client.post(
            "/platform/settings",
            json={
                "ai_providers": {
                    "google": {"api_key": "__clear__"},
                    "not-a-provider": {"api_key": "x"},
                },
                "confirm_security_change": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert main_module.store.platform_settings.ai_providers["google"]["api_key"] == ""
        assert "not-a-provider" not in main_module.store.platform_settings.ai_providers
    finally:
        main_module.store.platform_settings.ai_providers = original
        main_module._apply_provider_settings_side_effects()


def test_models_overview_reports_providers_and_catalog() -> None:
    response = client.get("/models/overview", headers=READ_HEADERS)
    assert response.status_code == 200
    body = response.json()

    assert set(body["providers"].keys()) == {"openai", "nim", "ollama"}
    assert body["providers"]["nim"]["base_url"].startswith("http")
    assert isinstance(body["providers"]["ollama"]["available"], bool)

    catalog = body["catalog"]
    assert len(catalog) >= 5
    sample = catalog[0]
    assert sample["reference"] == f"ollama/{sample['id']}"
    assert "size_gb" in sample and "min_ram_gb" in sample
    assert isinstance(sample["installed"], bool)


def test_pull_rejects_models_not_on_the_allowlist() -> None:
    response = client.post(
        "/models/local/pull",
        json={"model": "evil/not-on-catalog:latest"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 400
    assert "catalog" in response.json()["detail"].lower()


def test_pull_requires_reachable_local_runtime(monkeypatch) -> None:
    monkeypatch.setattr(main_module.local_models, "ollama_available", lambda: False)
    response = client.post(
        "/models/local/pull",
        json={"model": "qwen2.5:0.5b"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 503


def test_provider_settings_override_environment_resolution() -> None:
    settings = main_module.store.platform_settings
    originals = (
        settings.openai_model,
        settings.nim_base_url,
        settings.nim_api_key,
        settings.ollama_default_model,
    )
    try:
        settings.openai_model = "gpt-custom"
        settings.nim_base_url = "https://nim.internal.example/v1/"
        settings.nim_api_key = "nvapi-test-value-123456"
        settings.ollama_default_model = "llama3.2:1b"

        assert main_module._default_openai_model() == "gpt-custom"
        assert main_module._nim_base_url() == "https://nim.internal.example/v1"
        assert main_module._nim_api_key() == "nvapi-test-value-123456"
        assert main_module._default_ollama_model() == "llama3.2:1b"
    finally:
        (
            settings.openai_model,
            settings.nim_base_url,
            settings.nim_api_key,
            settings.ollama_default_model,
        ) = originals


def test_platform_settings_read_masks_provider_secrets() -> None:
    settings = main_module.store.platform_settings
    original = settings.nim_api_key
    try:
        settings.nim_api_key = "nvapi-super-secret-987654"
        response = client.get("/platform/settings", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["nim_api_key"] == ""
        assert body["nim_api_key_configured"] is True
        assert "nvapi-super-secret" not in response.text
    finally:
        settings.nim_api_key = original


def test_platform_settings_save_secret_semantics() -> None:
    settings = main_module.store.platform_settings
    original = settings.nim_api_key
    try:
        # Set a key.
        saved = client.post(
            "/platform/settings",
            json={"nim_api_key": "nvapi-first-value-111111", "confirm_security_change": True},
            headers=ADMIN_HEADERS,
        )
        assert saved.status_code == 200
        assert main_module.store.platform_settings.nim_api_key == "nvapi-first-value-111111"

        # Empty submission leaves it unchanged.
        client.post(
            "/platform/settings",
            json={"nim_api_key": "", "confirm_security_change": True},
            headers=ADMIN_HEADERS,
        )
        assert main_module.store.platform_settings.nim_api_key == "nvapi-first-value-111111"

        # The clear sentinel removes it.
        client.post(
            "/platform/settings",
            json={"nim_api_key": "__clear__", "confirm_security_change": True},
            headers=ADMIN_HEADERS,
        )
        assert main_module.store.platform_settings.nim_api_key == ""
    finally:
        main_module.store.platform_settings.nim_api_key = original
        main_module._apply_provider_settings_side_effects()


def test_settings_save_invalidates_provider_client_cache() -> None:
    main_module._PROVIDER_CLIENTS["nim"] = object()
    try:
        response = client.post(
            "/platform/settings",
            json={
                "nim_default_model": "meta/llama-3.1-8b-instruct",
                "confirm_security_change": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        assert "nim" not in main_module._PROVIDER_CLIENTS
    finally:
        main_module._PROVIDER_CLIENTS.pop("nim", None)
        main_module.store.platform_settings.nim_default_model = ""


def test_settings_save_rejects_invalid_types_with_400_not_500() -> None:
    # Regression: the settings page sent a non-list value for the hostname list
    # and the API crashed with a 500, silently losing the rest of the save.
    # (A Boolean is now an accepted on/off toggle; see
    # test_save_platform_settings_accepts_boolean_local_hostname_toggle.)
    response = client.post(
        "/platform/settings",
        json={"allow_local_network_hostnames": 12345},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Invalid platform settings payload"
    assert any("allow_local_network_hostnames" in e["field"] for e in detail["errors"])


def test_provider_models_listing_endpoint() -> None:
    unknown = client.get("/models/providers/not-real/models", headers=READ_HEADERS)
    assert unknown.status_code == 404

    unconfigured = client.get("/models/providers/anthropic/models", headers=READ_HEADERS)
    assert unconfigured.status_code == 200
    body = unconfigured.json()
    assert body["configured"] in {True, False}
    assert isinstance(body["models"], list)

    ollama = client.get("/models/providers/ollama/models", headers=READ_HEADERS)
    assert ollama.status_code == 200
    assert isinstance(ollama.json()["models"], list)


def test_pull_requires_authentication_when_auth_enforced() -> None:
    original = main_module.store.platform_settings.require_authenticated_requests
    try:
        main_module.store.platform_settings.require_authenticated_requests = True
        response = client.post(
            "/models/local/pull",
            json={"model": "qwen2.5:0.5b"},
            headers={"x-frontier-actor": "tester"},
        )
        assert response.status_code in {401, 403}
    finally:
        main_module.store.platform_settings.require_authenticated_requests = original
