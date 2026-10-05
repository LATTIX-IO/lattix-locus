from __future__ import annotations

from typing import Any

import pytest

from app.linear_mcp_oauth import (
    EXPECTED_ISSUER,
    EXPECTED_RESOURCE,
    OAUTH_METADATA_URL,
    LinearOAuthSetupError,
    register_public_client,
)


class _Response:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.body


def _metadata() -> dict[str, Any]:
    return {
        "issuer": EXPECTED_ISSUER,
        "authorization_endpoint": "https://mcp.linear.app/authorize",
        "token_endpoint": "https://mcp.linear.app/token",
        "registration_endpoint": "https://mcp.linear.app/register",
        "resource": EXPECTED_RESOURCE,
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def test_register_public_client_uses_discovered_linear_endpoints_and_pkce() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    redirect_uri = "https://locus.example.test/integrations/linear/oauth/callback"

    def get(url: str, **kwargs: Any) -> _Response:
        calls.append(("get", url, kwargs))
        return _Response(_metadata())

    def post(url: str, **kwargs: Any) -> _Response:
        calls.append(("post", url, kwargs))
        return _Response(
            {
                "client_id": "linear-public-client",
                "redirect_uris": [redirect_uri],
                "token_endpoint_auth_method": "none",
            }
        )

    client = register_public_client(redirect_uri, get=get, post=post)

    assert client == {
        "authorize_url": "https://mcp.linear.app/authorize",
        "token_url": "https://mcp.linear.app/token",
        "resource": EXPECTED_RESOURCE,
        "client_id": "linear-public-client",
    }
    assert calls[0][1] == OAUTH_METADATA_URL
    registration = calls[1][2]["json"]
    assert registration["redirect_uris"] == [redirect_uri]
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["grant_types"] == ["authorization_code", "refresh_token"]


def test_registration_rejects_untrusted_discovered_endpoints_before_post() -> None:
    posted: list[str] = []

    def post(url: str, **_kwargs: Any) -> _Response:
        posted.append(url)
        return _Response({"client_id": "should-not-be-used"})

    metadata = _metadata()
    metadata["token_endpoint"] = "https://attacker.example/token"

    with pytest.raises(LinearOAuthSetupError, match="invalid token endpoint"):
        register_public_client(
            "https://locus.example.test/callback",
            get=lambda _url, **_kwargs: _Response(metadata),
            post=post,
        )

    assert posted == []


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://locus.example.test/callback",
        "https://user:pass@locus.example.test/callback",
    ],
)
def test_registration_rejects_insecure_or_credentialed_callback(redirect_uri: str) -> None:
    posted: list[str] = []

    with pytest.raises(LinearOAuthSetupError, match="callback URL is invalid"):
        register_public_client(
            redirect_uri,
            get=lambda _url, **_kwargs: _Response(_metadata()),
            post=lambda url, **_kwargs: posted.append(url),
        )

    assert posted == []
