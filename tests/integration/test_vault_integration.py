import json

import httpx
import pytest

from locus_runtime.security import VaultClient


def test_vault_client_fails_closed_without_configuration() -> None:
    client = VaultClient(addr="", token="")

    with pytest.raises(RuntimeError, match="Vault client is not configured"):
        client.read_secret("demo/path")


def test_vault_client_reads_kv_v2_secret(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = json.dumps({"data": {"data": {"value": "super-secret", "enabled": True}}})

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return json.loads(self.text)

    def _fake_request(method, url, headers=None, json=None, timeout=0, follow_redirects=False):
        captured["method"] = method
        captured["url"] = url
        captured["token"] = headers.get("X-Vault-Token") if headers else None
        captured["accept"] = headers.get("Accept") if headers else None
        captured["json"] = json
        captured["timeout"] = timeout
        captured["follow_redirects"] = follow_redirects
        return _FakeResponse()

    monkeypatch.setattr("locus_runtime.security.httpx.request", _fake_request)

    client = VaultClient(addr="http://vault:8200", token="vault-token", timeout_seconds=7)
    secret = client.read_secret("secret/data/demo/path")

    assert secret == {"value": "super-secret", "enabled": True}
    assert captured["method"] == "GET"
    assert captured["url"] == "http://vault:8200/v1/secret/data/demo/path"
    assert captured["token"] == "vault-token"
    assert captured["accept"] == "application/json"
    assert captured["json"] is None
    assert captured["timeout"] == 7


def test_vault_client_writes_kv_v2_secret(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = json.dumps({"data": {"written": True}})

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return json.loads(self.text)

    def _fake_request(method, url, headers=None, json=None, timeout=0, follow_redirects=False):
        captured["method"] = method
        captured["url"] = url
        captured["token"] = headers.get("X-Vault-Token") if headers else None
        captured["content_type"] = headers.get("Content-Type") if headers else None
        captured["accept"] = headers.get("Accept") if headers else None
        captured["json"] = json
        captured["timeout"] = timeout
        captured["follow_redirects"] = follow_redirects
        return _FakeResponse()

    monkeypatch.setattr("locus_runtime.security.httpx.request", _fake_request)

    client = VaultClient(addr="http://vault:8200", token="vault-token", timeout_seconds=9)
    response = client.write_secret(
        "secret/data/demo/path", {"value": "super-secret", "enabled": True}
    )

    assert response == {"data": {"written": True}}
    assert captured["method"] == "POST"
    assert captured["url"] == "http://vault:8200/v1/secret/data/demo/path"
    assert captured["token"] == "vault-token"
    assert captured["content_type"] == "application/json"
    assert captured["accept"] == "application/json"
    assert captured["json"] == {"data": {"value": "super-secret", "enabled": True}}
    assert captured["timeout"] == 9


def test_vault_client_sanitizes_http_error_details(monkeypatch) -> None:
    response = httpx.Response(403, text="permission denied: secret/data/prod/root-token")

    def _fake_request(method, url, headers=None, json=None, timeout=0, follow_redirects=False):
        raise httpx.HTTPStatusError("denied", request=httpx.Request(method, url), response=response)

    monkeypatch.setattr("locus_runtime.security.httpx.request", _fake_request)

    client = VaultClient(addr="http://vault:8200", token="vault-token", timeout_seconds=5)
    with pytest.raises(RuntimeError) as exc:
        client.read_secret("secret/data/demo/path")

    message = str(exc.value)
    assert "403" in message
    assert "permission denied" not in message
