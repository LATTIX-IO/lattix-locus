"""Unit tests for MCP OAuth 2.0 Authorization Code flow with PKCE."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.app.main import (
    _build_oauth_authorize_url,
    _exchange_oauth_code_for_tokens,
    _generate_oauth_state,
    _generate_pkce_pair,
    _is_token_expired,
    _refresh_oauth_token,
    IntegrationDefinition,
)
from frontier_runtime.security import VaultClient


UTC = timezone.utc


class TestPKCEGeneration:
    """Tests for PKCE code verifier and challenge generation."""

    def test_generate_pkce_pair_returns_verifier_and_challenge(self):
        verifier, challenge = _generate_pkce_pair()
        assert isinstance(verifier, str)
        assert len(verifier) >= 32
        assert isinstance(challenge, str)
        assert len(challenge) == 43

    def test_generate_pkce_pair_unique_each_call(self):
        verifier1, challenge1 = _generate_pkce_pair()
        verifier2, challenge2 = _generate_pkce_pair()
        assert verifier1 != verifier2
        assert challenge1 != challenge2


class TestOAuthStateGeneration:
    """Tests for OAuth state parameter generation."""

    def test_generate_oauth_state_returns_secure_random(self):
        state = _generate_oauth_state()
        assert isinstance(state, str)
        assert len(state) >= 32

    def test_generate_oauth_state_unique_each_call(self):
        state1 = _generate_oauth_state()
        state2 = _generate_oauth_state()
        assert state1 != state2


class TestOAuthAuthorizeURL:
    """Tests for building OAuth authorization URLs."""

    def test_build_oauth_authorize_url_basic(self):
        integration = IntegrationDefinition(
            id="test-1",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "authorization_url": "https://auth.example.com/authorize",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client-id",
                    "scopes": ["read", "write"],
                    "redirect_uri": "http://localhost:3000/callback",
                }
            },
        )

        url = _build_oauth_authorize_url(integration, "http://localhost:3000/callback", "test-state", "test-challenge")

        assert "https://auth.example.com/authorize" in url
        assert "response_type=code" in url
        assert "client_id=test-client-id" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fcallback" in url
        assert "state=test-state" in url
        assert "code_challenge=test-challenge" in url
        assert "code_challenge_method=S256" in url
        assert "scope=read+write" in url

    def test_build_oauth_authorize_url_missing_auth_url_raises(self):
        integration = IntegrationDefinition(
            id="test-2",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={"auth": {"grant_type": "authorization_code"}},
        )

        with pytest.raises(ValueError, match="OAuth authorization URL not configured"):
            _build_oauth_authorize_url(integration, "http://localhost:3000/callback", "state", "challenge")

    def test_build_oauth_authorize_url_missing_client_id_raises(self):
        integration = IntegrationDefinition(
            id="test-3",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "authorization_url": "https://auth.example.com/authorize",
                }
            },
        )

        with pytest.raises(ValueError, match="OAuth client_id not configured"):
            _build_oauth_authorize_url(integration, "http://localhost:3000/callback", "state", "challenge")


class TestTokenExpiration:
    """Tests for token expiration checking."""

    def test_is_token_expired_returns_true_for_none(self):
        assert _is_token_expired(None) is True

    def test_is_token_expired_returns_true_for_empty_string(self):
        assert _is_token_expired("") is True

    def test_is_token_expired_returns_true_for_past_timestamp(self):
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert _is_token_expired(past) is True

    def test_is_token_expired_returns_true_for_near_expiry(self):
        near_future = (datetime.now(UTC) + timedelta(minutes=3)).isoformat()
        assert _is_token_expired(near_future) is True

    def test_is_token_expired_returns_false_for_valid_future_timestamp(self):
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        assert _is_token_expired(future) is False

    def test_is_token_expired_handles_z_suffix(self):
        future = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        assert _is_token_expired(future) is False


class TestOAuthTokenExchange:
    """Tests for OAuth token exchange (mocked HTTP)."""

    @pytest.mark.asyncio
    async def test_exchange_oauth_code_for_tokens_success(self):
        integration = IntegrationDefinition(
            id="test-4",
            name="Test MCP",
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

        mock_response = {
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-456",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = await _exchange_oauth_code_for_tokens(
                integration, "auth-code-123", "code-verifier-123", "http://localhost:3000/callback"
            )

            assert result["access_token"] == "access-token-123"
            assert result["refresh_token"] == "refresh-token-456"
            assert result["expires_in"] == 3600

    @pytest.mark.asyncio
    async def test_exchange_oauth_code_for_tokens_error(self):
        integration = IntegrationDefinition(
            id="test-5",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                }
            },
        )

        mock_response = {"error": "invalid_grant", "error_description": "Invalid authorization code"}

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            with pytest.raises(ValueError, match="Token exchange failed"):
                await _exchange_oauth_code_for_tokens(
                    integration, "bad-code", "code-verifier", "http://localhost:3000/callback"
                )


class TestOAuthTokenRefresh:
    """Tests for OAuth token refresh (mocked HTTP)."""

    @pytest.mark.asyncio
    async def test_refresh_oauth_token_success(self):
        integration = IntegrationDefinition(
            id="test-6",
            name="Test MCP",
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
                "tokens": {"refresh_token": "refresh-token-123"},
            },
        )

        mock_response = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = await _refresh_oauth_token(integration)

            assert result is not None
            assert result["access_token"] == "new-access-token"
            assert result["refresh_token"] == "new-refresh-token"

    @pytest.mark.asyncio
    async def test_refresh_oauth_token_no_refresh_token_returns_none(self):
        integration = IntegrationDefinition(
            id="test-7",
            name="Test MCP",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={"auth": {"grant_type": "authorization_code"}},
        )

        result = await _refresh_oauth_token(integration)
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_oauth_token_no_token_url_returns_none(self):
        integration = IntegrationDefinition(
            id="test-8",
            name="Test MCP",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {"grant_type": "authorization_code"},
                "tokens": {"refresh_token": "refresh-token"},
            },
        )

        result = await _refresh_oauth_token(integration)
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_oauth_token_http_error_returns_none(self):
        integration = IntegrationDefinition(
            id="test-9",
            name="Test MCP",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                },
                "tokens": {"refresh_token": "refresh-token"},
            },
        )

        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            result = await _refresh_oauth_token(integration)
            assert result is None


class TestIntegrationDefinitionOAuth:
    """Tests for IntegrationDefinition with OAuth metadata."""

    def test_integration_with_oauth_metadata(self):
        integration = IntegrationDefinition(
            id="test-oauth-1",
            name="OAuth MCP",
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
                    "client_id": "client-123",
                    "scopes": ["read", "write"],
                    "redirect_uri": "http://localhost:3000/callback",
                },
            },
        )

        auth = integration.metadata_json.get("auth", {})
        assert auth["grant_type"] == "authorization_code"
        assert auth["client_id"] == "client-123"
        assert "read" in auth["scopes"]
        assert auth["redirect_uri"] == "http://localhost:3000/callback"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])