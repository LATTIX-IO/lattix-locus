"""Seed Vault with development placeholders."""

from __future__ import annotations

import os
from typing import Any

import hvac


def _seed_payload() -> dict[str, Any]:
    return {
        "status": "seeded",
        "placeholder": "replace-me",
    }


def main() -> None:
    url = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
    token = str(os.getenv("VAULT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Vault is not authenticated. Set VAULT_ADDR and VAULT_TOKEN.")
    client = hvac.Client(url=url, token=token)
    if not client.is_authenticated():
        raise SystemExit("Vault is not authenticated. Set VAULT_ADDR and VAULT_TOKEN.")
    client.secrets.kv.v2.create_or_update_secret(
        path="dev/locus",
        secret=_seed_payload(),
        mount_point="secret",
    )
    print({"available": True, "seeded": "secret/data/dev/locus", "vault_addr": url})  # noqa: T201


if __name__ == "__main__":
    main()
