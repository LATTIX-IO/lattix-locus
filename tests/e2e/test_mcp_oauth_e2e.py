"""End-to-end tests for MCP OAuth 2.0 Authorization Code flow."""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

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


class TestMCPOAuthE2E:
    """End-to-end tests for complete MCP OAuth flow."""

    @patch("urllib.request.urlopen")
    def test_full_oauth_flow_install_and_connect(self, mock_urlopen, client, auth_headers):
        """Test complete flow: install from catalog -> configure OAuth -> connect -> use tools."""
        from apps.backend.app.main import store

        # Step 1: Install GitHub MCP from catalog
        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.return_value = "test-user"
            with patch("apps.backend.app.main._enforce_emergency_write_policy"):
                response = client.post(
                    "/integrations/catalog/mcp-github/install",
                    headers=auth_headers,
                )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        integration_id = data["id"]

        # Verify integration was created with OAuth config
        integration = store.integrations[integration_id]
        assert integration.name == "GitHub MCP"
        assert integration.auth_type == "oauth2"
        auth_config = integration.metadata_json.get("auth", {})
        assert auth_config["grant_type"] == "authorization_code"
        assert auth_config["authorization_url"] == "https://github.com/login/oauth/authorize"
        assert auth_config["token_url"] == "https://github.com/login/oauth/access_token"
        assert "redirect_uri" in auth_config

        # Step 2: Initiate OAuth authorization
        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            mock_auth.return_value = "test-user"
            response = client.get(f"/integrations/{integration_id}/oauth/authorize", headers=auth_headers)

        assert response.status_code == 200
        oauth_data = response.json()
        assert "authorize_url" in oauth_data
        assert "state" in oauth_data
        authorize_url = oauth_data["authorize_url"]
        state = oauth_data["state"]

        # Verify authorize URL contains required parameters
        assert "https://github.com/login/oauth/authorize" in authorize_url
        assert "response_type=code" in authorize_url
        assert "client_id=" in authorize_url
        assert "redirect_uri=" in authorize_url
        assert f"state={state}" in authorize_url
        assert "code_challenge=" in authorize_url
        assert "code_challenge_method=S256" in authorize_url

        # Verify PKCE state was stored
        stored_state = integration.metadata_json.get("oauth_state")
        assert stored_state is not None
        assert stored_state["state"] == state
        assert "code_verifier" in stored_state

        # Step 3: Simulate OAuth callback with authorization code
        mock_token_response = {
            "access_token": "gho_github_oauth_token_1234567890",
            "refresh_token": "ghr_refresh_token_1234567890",
            "expires_in": 28800,
            "token_type": "Bearer",
            "scope": "read:user repo workflow",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_token_response).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        callback_url = f"/integrations/{integration_id}/oauth/callback?code=auth_code_from_github&state={state}"
        response = client.get(callback_url)

        assert response.status_code == 200
        assert "MCP Server Connected" in response.text
        assert "GitHub MCP" in response.text

        # Step 4: Verify integration is now configured with tokens
        updated = store.integrations[integration_id]
        assert updated.status == "configured"
        tokens = updated.metadata_json.get("tokens", {})
        assert tokens["access_token"] == "gho_github_oauth_token_1234567890"
        assert tokens["refresh_token"] == "ghr_refresh_token_1234567890"
        assert "expires_at" in tokens
        assert "oauth_state" not in updated.metadata_json

        # Step 5: Verify token can be resolved for agent use
        from apps.backend.app.main import _resolve_integration_bearer
        token = _resolve_integration_bearer(updated)
        assert token == "gho_github_oauth_token_1234567890"

        # Step 6: Disconnect OAuth
        with patch("apps.backend.app.main._enforce_builder_access") as mock_auth:
            with patch("apps.backend.app.main._enforce_emergency_write_policy"):
                mock_auth.return_value = "test-user"
                response = client.post(f"/integrations/{integration_id}/oauth/disconnect", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

        # Step 7: Verify integration is back to draft
        disconnected = store.integrations[integration_id]
        assert disconnected.status == "draft"
        assert "tokens" not in disconnected.metadata_json

    @patch("urllib.request.urlopen")
    def test_multiple_agents_share_mcp_credentials(self, mock_urlopen, client, auth_headers):
        """Test that multiple agents can use the same connected MCP integration."""
        from apps.backend.app.main import IntegrationDefinition, store, _resolve_integration_bearer

        # Create a pre-connected MCP integration
        integration = IntegrationDefinition(
            id="shared-mcp-1",
            name="Shared MCP",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "protocol": "mcp",
                "transport": "http",
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "tokens": {
                    "access_token": "shared-access-token",
                    "refresh_token": "shared-refresh-token",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "token_type": "Bearer",
                },
            },
        )
        store.integrations["shared-mcp-1"] = integration

        # Verify token can be resolved for multiple agents
        for _ in range(3):
            token = _resolve_integration_bearer(integration)
            assert token == "shared-access-token"

    @patch("urllib.request.urlopen")
    def test_token_refresh_on_expiry(self, mock_urlopen, client, auth_headers):
        """Test automatic token refresh when access token expires."""
        from apps.backend.app.main import IntegrationDefinition, store, _resolve_integration_bearer

        # Create integration with expired access token but valid refresh token
        integration = IntegrationDefinition(
            id="refresh-test-1",
            name="Refresh Test MCP",
            type="custom",
            status="configured",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "protocol": "mcp",
                "transport": "http",
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "tokens": {
                    "access_token": "expired-token",
                    "refresh_token": "valid-refresh-token",
                    "expires_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),  # Expired
                    "token_type": "Bearer",
                },
            },
        )
        store.integrations["refresh-test-1"] = integration

        # Mock token refresh response
        mock_refresh_response = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_refresh_response).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # Call _resolve_integration_bearer which should trigger refresh
        token = _resolve_integration_bearer(integration)

        assert token == "new-access-token"

        # Verify tokens were updated
        updated = store.integrations["refresh-test-1"]
        tokens = updated.metadata_json.get("tokens", {})
        assert tokens["access_token"] == "new-access-token"
        assert tokens["refresh_token"] == "new-refresh-token"
        assert "expires_at" in tokens

    def test_oauth_csrf_protection_via_state(self, client):
        """Test that OAuth state parameter prevents CSRF attacks."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="csrf-test-1",
            name="CSRF Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "oauth_state": {"state": "secure-random-state-123", "code_verifier": "verifier", "integration_id": "csrf-test-1"},
            },
        )
        store.integrations["csrf-test-1"] = integration

        # Attacker tries to use a different state
        response = client.get("/integrations/csrf-test-1/oauth/callback?code=auth-code&state=attacker-state")
        assert response.status_code == 400
        assert "Invalid OAuth state parameter" in response.json()["detail"]

        # Valid state should work (if token exchange succeeds)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = {"access_token": "token", "expires_in": 3600, "token_type": "Bearer"}
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            response = client.get("/integrations/csrf-test-1/oauth/callback?code=auth-code&state=secure-random-state-123")
            # Will fail on token exchange but state validation passes
            assert response.status_code != 400 or "Invalid OAuth state parameter" not in response.json().get("detail", "")

    def test_pkce_code_verifier_used_in_token_exchange(self, client, auth_headers):
        """Test that PKCE code verifier is used in token exchange."""
        from apps.backend.app.main import IntegrationDefinition, store

        integration = IntegrationDefinition(
            id="pkce-test-1",
            name="PKCE Test MCP",
            type="custom",
            status="draft",
            base_url="https://mcp.example.com/mcp",
            auth_type="oauth2",
            metadata_json={
                "auth": {
                    "grant_type": "authorization_code",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "test-client",
                    "redirect_uri": "http://localhost:3000/callback",
                },
                "oauth_state": {
                    "state": "test-state",
                    "code_verifier": "specific-code-verifier-123",
                    "integration_id": "pkce-test-1",
                },
            },
        )
        store.integrations["pkce-test-1"] = integration

        captured_request = {}

        def capture_request(request, timeout=30):
            captured_request["data"] = request.data
            mock_response = {"access_token": "token", "expires_in": 3600, "token_type": "Bearer"}
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture_request):
            response = client.get("/integrations/pkce-test-1/oauth/callback?code=auth-code&state=test-state")

        # Verify code_verifier was sent in token request
        assert "code_verifier=specific-code-verifier-123" in captured_request["data"].decode()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])