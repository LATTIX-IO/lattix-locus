"""Integration tests for MCP OAuth 2.0 Authorization Code flow."""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from apps.backend.app.main import app, store

UTC = timezone.utc


@pytest.fixture(autouse=True)
def reset_store():
    """Reset the in-memory store before each test."""
    store.integrations.clear()
    store.platform_settings = store.platform_settings.__class__()
    yield
    store.integrations.clear()


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Mock authentication headers."""
    return {"Authorization": "Bearer test-token"}


class TestMCPOAuthIntegration:
    """Integration tests for MCP OAuth endpoints."""

    def test_oauth_authorize_requires_authentication(self, client):
        """OAuth authorize endpoint requires authentication."""
        from fastapi import HTTPException
        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.side_effect = HTTPException(status_code=401, detail="Unauthorized")
            response = client.get("/integrations/test-id/oauth/authorize")
        assert response.status_code == 401

    def test_oauth_authorize_integration_not_found(self, client, auth_headers):
        """OAuth authorize returns 404 for non-existent integration."""
        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.return_value = "test-user"
            response = client.get("/integrations/non-existent/oauth/authorize", headers=auth_headers)
        assert response.status_code == 404

    def test_oauth_authorize_wrong_grant_type(self, client, auth_headers):
        """OAuth authorize fails for non-authorization_code grant type."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-1",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "client_credentials",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                }
            },
        )
        store.integrations["test-oauth-1"] = integration

        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.return_value = "test-user"
            response = client.get("/integrations/test-oauth-1/oauth/authorize", headers=auth_headers)

        assert response.status_code == 400
        assert "not configured for OAuth authorization code flow" in response.json()["detail"]

    def test_oauth_authorize_missing_redirect_uri(self, client, auth_headers):
        """OAuth authorize fails when redirect_uri is missing."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-2",
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
                    "client_id": "test-client",
                }
            },
        )
        store.integrations["test-oauth-2"] = integration

        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.return_value = "test-user"
            response = client.get("/integrations/test-oauth-2/oauth/authorize", headers=auth_headers)

        assert response.status_code == 400
        assert "redirect_uri not configured" in response.json()["detail"]

    def test_oauth_authorize_success(self, client, auth_headers):
        """OAuth authorize returns authorize URL and state."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-3",
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
                    "client_id": "test-client",
                    "scopes": ["read", "write"],
                    "redirect_uri": "http://localhost:3000/callback",
                }
            },
        )
        store.integrations["test-oauth-3"] = integration

        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.return_value = "test-user"
            response = client.get("/integrations/test-oauth-3/oauth/authorize", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "authorize_url" in data
        assert "state" in data
        assert "https://auth.example.com/authorize" in data["authorize_url"]
        assert "response_type=code" in data["authorize_url"]
        assert "client_id=test-client" in data["authorize_url"]
        assert "code_challenge=" in data["authorize_url"]
        assert "code_challenge_method=S256" in data["authorize_url"]
        assert len(data["state"]) >= 32

        # Verify oauth_state was stored
        stored = store.integrations["test-oauth-3"].metadata_json.get("oauth_state")
        assert stored is not None
        assert stored["state"] == data["state"]
        assert "code_verifier" in stored
        assert stored["integration_id"] == "test-oauth-3"

    def test_oauth_callback_invalid_state(self, client):
        """OAuth callback rejects invalid state."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-4",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "oauth_state": {"state": "valid-state", "code_verifier": "verifier", "integration_id": "test-oauth-4"},
            },
        )
        store.integrations["test-oauth-4"] = integration

        response = client.get("/integrations/test-oauth-4/oauth/callback?code=auth-code&state=invalid-state")
        assert response.status_code == 400
        assert "Invalid OAuth state parameter" in response.json()["detail"]

    def test_oauth_callback_missing_code(self, client):
        """OAuth callback rejects missing authorization code."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-5",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "oauth_state": {"state": "valid-state", "code_verifier": "verifier", "integration_id": "test-oauth-5"},
            },
        )
        store.integrations["test-oauth-5"] = integration

        response = client.get("/integrations/test-oauth-5/oauth/callback?state=valid-state")
        assert response.status_code == 400
        assert "Missing authorization code" in response.json()["detail"]

    def test_oauth_callback_error_from_provider(self, client):
        """OAuth callback handles error from OAuth provider."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-6",
            name="Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "oauth_state": {"state": "valid-state", "code_verifier": "verifier", "integration_id": "test-oauth-6"},
            },
        )
        store.integrations["test-oauth-6"] = integration

        response = client.get("/integrations/test-oauth-6/oauth/callback?state=valid-state&error=access_denied")
        assert response.status_code == 400
        assert "OAuth authorization failed: access_denied" in response.json()["detail"]

    @patch("urllib.request.urlopen")
    def test_oauth_callback_success(self, mock_urlopen, client):
        """OAuth callback successfully exchanges code for tokens."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-7",
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
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "oauth_state": {
                    "state": "valid-state",
                    "code_verifier": "verifier-123",
                    "integration_id": "test-oauth-7",
                },
            },
        )
        store.integrations["test-oauth-7"] = integration

        mock_response = {
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-456",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        response = client.get(
            "/integrations/test-oauth-7/oauth/callback?code=auth-code-123&state=valid-state"
        )

        assert response.status_code == 200
        assert "MCP Server Connected" in response.text
        assert "Test MCP" in response.text

        # Verify tokens were stored
        updated = store.integrations["test-oauth-7"]
        tokens = updated.metadata_json.get("tokens", {})
        assert tokens["access_token"] == "access-token-123"
        assert tokens["refresh_token"] == "refresh-token-456"
        assert "expires_at" in tokens
        assert updated.status == "configured"

    def test_oauth_disconnect_requires_authentication(self, client):
        """OAuth disconnect requires authentication."""
        from fastapi import HTTPException
        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.side_effect = HTTPException(status_code=401, detail="Unauthorized")
            response = client.post("/integrations/test-id/oauth/disconnect")
        assert response.status_code == 401

    def test_oauth_disconnect_success(self, client, auth_headers):
        """OAuth disconnect clears tokens and resets status."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="test-oauth-8",
            name="Test MCP",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {"grant_type": "authorization_code"},
                "tokens": {"access_token": "old-token", "refresh_token": "old-refresh"},
            },
        )
        store.integrations["test-oauth-8"] = integration

        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            with patch("apps.backend.app.main._enforce_emergency_write_policy"):
                mock_auth.return_value = "test-user"
                response = client.post("/integrations/test-oauth-8/oauth/disconnect", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["id"] == "test-oauth-8"

        updated = store.integrations["test-oauth-8"]
        assert "tokens" not in updated.metadata_json
        assert updated.status == "draft"


class TestMCPCredentialSharing:
    """Tests for MCP credential sharing across agents."""

    @patch("urllib.request.urlopen")
    def test_mcp_tools_available_after_oauth_connect(self, mock_urlopen, client, auth_headers):
        """MCP tools become available after OAuth connection - verified via token resolution."""
        from apps.backend.app.main import IntegrationDefinition, store, _resolve_integration_bearer
        from datetime import datetime, timezone

        UTC = timezone.utc

        # Create OAuth-connected MCP integration
        integration = IntegrationDefinition(
            id="mcp-github-1",
            name="GitHub MCP",
            type="custom",
            status="configured",
            base_url="https://api.githubcopilot.com/mcp/",
            auth_type="oauth2",
            metadata_json={
                "protocol": "mcp",
                "transport": "http",
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://github.com/login/oauth/access_token",
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "tokens": {
                    "access_token": "gh-oauth-token-123",
                    "token_type": "Bearer",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            },
        )
        store.integrations["mcp-github-1"] = integration

        # Verify token can be resolved (this is what _gather_mcp_run_tools uses)
        token = _resolve_integration_bearer(integration)
        assert token == "gh-oauth-token-123"

    def test_mcp_tools_not_available_for_draft_integration(self):
        """MCP tools not exposed for draft (unconfigured) integrations."""
        from apps.backend.app.main import IntegrationDefinition, store, _resolve_integration_bearer

        integration = IntegrationDefinition(
            id="mcp-draft-1",
            name="Draft MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={"protocol": "mcp", "transport": "http"},
        )
        store.integrations["mcp-draft-1"] = integration

        # Draft integrations without tokens should not resolve bearer
        token = _resolve_integration_bearer(integration)
        assert token == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])