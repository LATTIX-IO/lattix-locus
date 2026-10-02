from __future__ import annotations

from pathlib import Path

from locus_tooling import common


def test_portal_urls_prefer_loopback_before_vanity_host(monkeypatch, tmp_path: Path) -> None:
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "LOCAL_GATEWAY_BIND_HOST=127.0.0.1",
                "LOCAL_GATEWAY_HTTP_PORT=80",
                "LOCAL_STACK_HOST=locus.localhost",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(common, "_detect_primary_ipv4", lambda: None)

    urls = common.portal_urls(root=tmp_path)

    assert urls == ["http://127.0.0.1", "http://locus.localhost"]
