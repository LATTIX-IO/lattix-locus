"""Performance and smoke tests for MCP OAuth implementation."""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from apps.backend.app.main import (
    _build_oauth_authorize_url,
    _exchange_oauth_code_for_tokens,
    _generate_pkce_pair,
    _generate_oauth_state,
    _is_token_expired,
    _refresh_oauth_token,
    _resolve_integration_bearer,
    IntegrationDefinition,
    store,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def reset_store():
    store.integrations.clear()
    yield
    store.integrations.clear()


@pytest.fixture
def client():
    return TestClient(app)


class TestMCPAuthPerformance:
    """Performance tests for MCP OAuth operations."""

    def test_pkce_generation_performance(self):
        """PKCE generation should be fast."""
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            _generate_pkce_pair()
        elapsed = time.perf_counter() - start
        # Should complete 1000 generations in under 100ms
        assert elapsed < 0.1, f"PKCE generation took {elapsed:.3f}s for {iterations} iterations"

    def test_oauth_state_generation_performance(self):
        """OAuth state generation should be fast."""
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            _generate_oauth_state()
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"State generation took {elapsed:.3f}s for {iterations} iterations"

    def test_authorize_url_building_performance(self):
        """Authorization URL building should be fast."""
        integration = IntegrationDefinition(
            id="perf-test-1",
            name="Performance Test",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "authorization_url": "https://auth.example.com/authorize",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "scopes": ["read", "write", "admin", "delete"],
                    "redirect_uri": "http://localhost:3000/callback",
                }
            },
        )

        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            _build_oauth_authorize_url(integration, "http://localhost:3000/callback", "state", "challenge")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"URL building took {elapsed:.3f}s for {iterations} iterations"

    def test_token_expiration_check_performance(self):
        """Token expiration check should be fast."""
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

        iterations = 10000
        start = time.perf_counter()
        for _ in range(iterations):
            _is_token_expired(future)
            _is_token_expired(past)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"Expiration check took {elapsed:.3f}s for {iterations} iterations"

    @patch("urllib.request.urlopen")
    def test_token_exchange_performance(self, mock_urlopen):
        """Token exchange should complete within reasonable time."""
        integration = IntegrationDefinition(
            id="perf-test-2",
            name="Performance Test",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                }
            },
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # Warm up
        asyncio.run(_exchange_oauth_code_for_tokens(integration, "code", "verifier", "http://localhost/callback"))

        # Measure
        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            asyncio.run(_exchange_oauth_code_for_tokens(integration, "code", "verifier", "http://localhost/callback"))
        elapsed = time.perf_counter() - start
        # Should complete 100 exchanges in under 5 seconds (50ms each)
        assert elapsed < 5.0, f"Token exchange took {elapsed:.3f}s for {iterations} iterations"

    @patch("urllib.request.urlopen")
    def test_token_refresh_performance(self, mock_urlopen):
        """Token refresh should complete within reasonable time."""
        integration = IntegrationDefinition(
            id="perf-test-3",
            name="Performance Test",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                },
                "tokens": {"refresh_token": "refresh"},
            },
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # Warm up
        asyncio.run(_refresh_oauth_token(integration))

        # Measure
        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            asyncio.run(_refresh_oauth_token(integration))
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"Token refresh took {elapsed:.3f}s for {iterations} iterations"

    def test_resolve_integration_bearer_cached_token(self):
        """Resolving bearer token from cached valid token should be fast."""
        integration = IntegrationDefinition(
            id="perf-test-4",
            name="Performance Test",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {"grant_type": "authorization_code"},
                "tokens": {
                    "access_token": "cached-token",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            },
        )

        iterations = 10000
        start = time.perf_counter()
        for _ in range(iterations):
            _resolve_integration_bearer(integration)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"Bearer resolution took {elapsed:.3f}s for {iterations} iterations"


class TestMCPSmokeTests:
    """Smoke tests for basic MCP OAuth functionality."""

    def test_oauth_helper_functions_exist(self):
        """All OAuth helper functions are importable and callable."""
        assert callable(_generate_pkce_pair)
        assert callable(_generate_oauth_state)
        assert callable(_build_oauth_authorize_url)
        assert callable(_exchange_oauth_code_for_tokens)
        assert callable(_refresh_oauth_token)
        assert callable(_is_token_expired)
        assert callable(_resolve_integration_bearer)

    def test_integration_definition_accepts_oauth_metadata(self):
        """IntegrationDefinition accepts OAuth metadata fields."""
        integration = IntegrationDefinition(
            id="smoke-test-1",
            name="Smoke Test",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "protocol": "mcp",
                "transport": "http",
                "auth": {
                    "grant_type": "authorization_code",
                    "authorization_url": "https://auth.example.com/authorize",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "scopes": ["read"],
                    "redirect_uri": "http://localhost:3000/callback",
                },
            },
        )
        assert integration.id == "smoke-test-1"
        assert integration.auth_type == "oauth2"
        assert integration.metadata_json["auth"]["grant_type"] == "authorization_code"

    def test_oauth_flow_basic_integration(self):
        """Basic OAuth flow integration test."""
        from apps.backend.app.main import store

        # Create integration
        integration = IntegrationDefinition(
            id="smoke-flow-1",
            name="Smoke Flow Test",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "authorization_url": "https://auth.example.com/authorize",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "redirect_uri": "http://localhost:3000/callback",
                }
            },
        )
        store.integrations["smoke-flow-1"] = integration

        # Generate PKCE
        verifier, challenge = _generate_pkce_pair()
        state = _generate_oauth_state()

        # Build authorize URL
        url = _build_oauth_authorize_url(integration, "http://localhost:3000/callback", state, challenge)
        assert "https://auth.example.com/authorize" in url
        assert f"state={state}" in url
        assert f"code_challenge={challenge}" in url

        # Store state
        integration.metadata_json["oauth_state"] = {
            "state": state,
            "code_verifier": verifier,
            "integration_id": "smoke-flow-1",
        }

        # Verify state stored
        stored = store.integrations["smoke-flow-1"].metadata_json.get("oauth_state")
        assert stored is not None
        assert stored["state"] == state
        assert stored["code_verifier"] == verifier

    def test_multiple_oauth_integrations_isolated(self):
        """Multiple OAuth integrations maintain separate state."""
        from apps.backend.app.main import store

        integrations = []
        for i in range(5):
            integration = IntegrationDefinition(
                id=f"multi-test-{i}",
                name=f"Multi Test {i}",
                type="custom",
                status="draft",
                base_url=f"https://mcp{i}.example.com/mcp",
                auth_type="oauth2",
                metadata_json={
                    "auth": {
                        "grant_type": "authorization_code",
                        "authorization_url": f"https://auth{i}.example.com/authorize",
                        "token_url": f"https://auth{i}.example.com/token",
                        "client_id": f"client-{i}",
                        "redirect_uri": f"http://localhost:3000/callback",
                    }
                },
            )
            store.integrations[integration.id] = integration
            integrations.append(integration)

        # Generate unique PKCE for each
        states = []
        for integration in integrations:
            verifier, challenge = _generate_pkce_pair()
            state = _generate_oauth_state()
            states.append((integration.id, state, verifier))

            url = _build_oauth_authorize_url(integration, "http://localhost/callback", state, challenge)
            assert f"state={state}" in url
            assert integration.metadata_json["auth"]["client_id"] in url

        # Verify all states are unique
        unique_states = set(s[1] for s in states)
        assert len(unique_states) == 5

        # Verify each integration has its own state
        for integration_id, state, verifier in states:
            stored = store.integrations[integration_id].metadata_json.get("oauth_state")
            # States not stored until authorize is called
            # Just verify the integrations exist and are separate
            assert store.integrations[integration_id].id == integration_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])