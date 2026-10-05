"""Pinned OAuth discovery and dynamic client registration for Linear MCP."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

OAUTH_METADATA_URL = "https://mcp.linear.app/.well-known/oauth-authorization-server"
EXPECTED_ISSUER = "https://mcp.linear.app"
EXPECTED_RESOURCE = "https://mcp.linear.app/mcp"


class LinearOAuthSetupError(RuntimeError):
    """Linear's MCP OAuth metadata or registration response is unusable."""


def _linear_https_endpoint(value: Any, field: str) -> str:
    endpoint = str(value or "").strip()
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "mcp.linear.app"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise LinearOAuthSetupError(f"Linear OAuth metadata has an invalid {field}")
    return endpoint


def register_public_client(
    redirect_uri: str,
    *,
    get: Callable[..., Any] | None = None,
    post: Callable[..., Any] | None = None,
) -> dict[str, str]:
    """Discover Linear's fixed auth server and register a public PKCE client."""
    try:
        metadata_response = (get or httpx.get)(
            OAUTH_METADATA_URL,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
        )
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
    except Exception as exc:  # noqa: BLE001 - normalize provider/network failure
        raise LinearOAuthSetupError("Could not discover Linear MCP OAuth metadata") from exc
    if not isinstance(metadata, Mapping) or metadata.get("issuer") != EXPECTED_ISSUER:
        raise LinearOAuthSetupError("Linear MCP OAuth issuer did not match the pinned issuer")

    try:
        authorize_url = _linear_https_endpoint(
            metadata.get("authorization_endpoint"), "authorization endpoint"
        )
        token_url = _linear_https_endpoint(metadata.get("token_endpoint"), "token endpoint")
        registration_url = _linear_https_endpoint(
            metadata.get("registration_endpoint"), "registration endpoint"
        )
        resource = _linear_https_endpoint(metadata.get("resource"), "resource")
    except ValueError as exc:
        raise LinearOAuthSetupError("Linear MCP OAuth metadata was invalid") from exc

    if resource != EXPECTED_RESOURCE:
        raise LinearOAuthSetupError("Linear MCP OAuth resource did not match the pinned resource")
    challenge_methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(challenge_methods, list) or "S256" not in challenge_methods:
        raise LinearOAuthSetupError("Linear MCP OAuth server does not advertise PKCE S256")
    auth_methods = metadata.get("token_endpoint_auth_methods_supported")
    if not isinstance(auth_methods, list) or "none" not in auth_methods:
        raise LinearOAuthSetupError("Linear MCP OAuth server does not support public clients")

    redirect = str(redirect_uri or "").strip()
    redirect_parsed = urlsplit(redirect)
    redirect_host = str(redirect_parsed.hostname or "").lower().rstrip(".")
    try:
        redirect_is_loopback = ipaddress.ip_address(redirect_host).is_loopback
    except ValueError:
        redirect_is_loopback = redirect_host == "localhost"
    if (
        redirect_parsed.scheme not in {"http", "https"}
        or not redirect_parsed.hostname
        or (redirect_parsed.scheme == "http" and not redirect_is_loopback)
        or redirect_parsed.username
        or redirect_parsed.password
        or redirect_parsed.fragment
    ):
        raise LinearOAuthSetupError("Locus OAuth callback URL is invalid")

    registration = {
        "client_name": "Locus",
        "application_type": "native",
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    try:
        registration_response = (post or httpx.post)(
            registration_url,
            json=registration,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
        )
        registration_response.raise_for_status()
        registered = registration_response.json()
    except Exception as exc:  # noqa: BLE001 - normalize provider/network failure
        raise LinearOAuthSetupError("Linear MCP OAuth client registration failed") from exc
    if not isinstance(registered, Mapping):
        raise LinearOAuthSetupError("Linear MCP OAuth registration returned an invalid response")

    client_id = str(registered.get("client_id") or "").strip()
    if not client_id or len(client_id) > 2048:
        raise LinearOAuthSetupError("Linear MCP OAuth registration returned no valid client id")
    registered_redirects = registered.get("redirect_uris")
    if isinstance(registered_redirects, list) and redirect not in registered_redirects:
        raise LinearOAuthSetupError("Linear MCP OAuth registration did not retain the callback URL")
    if str(registered.get("token_endpoint_auth_method") or "none") != "none":
        raise LinearOAuthSetupError("Linear MCP OAuth registration did not create a public client")

    return {
        "authorize_url": authorize_url,
        "token_url": token_url,
        "resource": resource,
        "client_id": client_id,
    }
