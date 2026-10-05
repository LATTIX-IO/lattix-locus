from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_seed_vault_module():
    module_path = REPO_ROOT / "scripts" / "seed_vault.py"
    module_name = "test_seed_vault_module"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_vault_requires_explicit_token(monkeypatch) -> None:
    module = _load_seed_vault_module()
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.delenv("VAULT_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="Vault is not authenticated"):
        module.main()


def test_seed_vault_uses_configured_token_and_seed_payload(monkeypatch) -> None:
    module = _load_seed_vault_module()
    captured: dict[str, object] = {}

    class _FakeKvV2:
        def create_or_update_secret(self, *, path, secret, mount_point):
            captured["path"] = path
            captured["secret"] = secret
            captured["mount_point"] = mount_point

    class _FakeClient:
        def __init__(self, *, url, token):
            captured["url"] = url
            captured["token"] = token
            self.secrets = type("Secrets", (), {"kv": type("KV", (), {"v2": _FakeKvV2()})()})()

        def is_authenticated(self):
            return True

    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_TOKEN", "configured-token")
    monkeypatch.setattr(module.hvac, "Client", _FakeClient)

    module.main()

    assert captured["url"] == "http://vault:8200"
    assert captured["token"] == "configured-token"
    assert captured["path"] == "dev/locus"
    assert captured["mount_point"] == "secret"
    assert captured["secret"] == {"status": "seeded", "placeholder": "replace-me"}
