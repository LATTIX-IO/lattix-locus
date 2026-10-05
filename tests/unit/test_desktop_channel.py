"""LOCUS-349 (D-26): CI helpers for the desktop update channels.

Signatures are made with an ephemeral key generated inside the test (never a
real or committed key), in the same minisign format Tauri's signer writes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import desktop_channel as dc

REPO = "LATTIX-IO/lattix-locus"
TAURI_CONF = Path(__file__).resolve().parents[2] / "apps/desktop-tauri/src-tauri/tauri.conf.json"


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #
def test_the_build_version_no_longer_comes_from_the_run_number() -> None:
    # D-31: MAJOR.MINOR.PATCH with PATCH = build counter (locus_tooling/versioning.py);
    # the <base>-dev.<run_number> helper and its subcommand are gone.
    assert not hasattr(dc, "dev_version")
    with pytest.raises(SystemExit):
        dc.main(["version", "--tauri-conf", str(TAURI_CONF), "--run-number", "1"])


def test_patch_counter_versions_rank_above_every_pre_d31_build() -> None:
    # Installs on 0.1.0-dev.N (Dev) and 0.1.1 (the June Stable) must update forward.
    for old in ("0.1.0-dev.16", "0.1.0-dev.100", "0.1.0", "0.1.1"):
        assert dc.should_advance("0.2.0", old) is True, old
    ordered = ["0.2.0", "0.2.1", "0.2.9", "0.2.10", "0.2.99999", "0.3.0", "1.0.0"]
    for lower, higher in zip(ordered, ordered[1:], strict=False):
        assert dc.compare_semver(lower, higher) == -1, (lower, higher)
        assert dc.should_advance(lower, higher) is False


def test_semver_ordering_matches_the_updater() -> None:
    ordered = [
        "0.1.0-dev.2",
        "0.1.0-dev.9",
        "0.1.0-dev.10",
        "0.1.0-dev.100",
        "0.1.0",
        "0.1.1-dev.1",
        "0.2.0",
    ]
    for lower, higher in zip(ordered, ordered[1:], strict=False):
        assert dc.compare_semver(lower, higher) == -1, (lower, higher)
        assert dc.compare_semver(higher, lower) == 1
    assert dc.compare_semver("0.1.0-dev.3+build.1", "0.1.0-dev.3") == 0


def test_channel_pointer_only_moves_forward(tmp_path: Path) -> None:
    assert dc.should_advance("0.1.0-dev.5", None) is True
    assert dc.should_advance("0.1.0-dev.5", "0.1.0-dev.4") is True
    assert dc.should_advance("0.1.0-dev.5", "0.1.0-dev.5") is False
    assert dc.should_advance("0.1.0-dev.5", "0.1.0-dev.12") is False
    current = tmp_path / "latest.json"
    current.write_text(json.dumps({"version": "0.1.0-dev.12"}), encoding="utf-8")
    assert (
        dc.main(["should-advance", "--candidate", "0.1.0-dev.13", "--current", str(current)]) == 0
    )


# --------------------------------------------------------------------------- #
# Ephemeral minisign key (test only)
# --------------------------------------------------------------------------- #
crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


class _TestKey:
    def __init__(self) -> None:
        from cryptography.hazmat.primitives import serialization

        self.secret = crypto.Ed25519PrivateKey.generate()
        self.key_id = os.urandom(8)
        public = self.secret.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        box = (
            f"untrusted comment: minisign public key: {self.key_id[::-1].hex().upper()}\n"
            + base64.b64encode(b"Ed" + self.key_id + public).decode()
            + "\n"
        )
        self.conf_pubkey = base64.b64encode(box.encode()).decode()

    def sign(self, data: bytes, *, key_id: bytes | None = None) -> str:
        signature = self.secret.sign(hashlib.blake2b(data, digest_size=64).digest())
        trusted = "timestamp:1700000000\tfile:bundle"
        global_sig = self.secret.sign(signature + trusted.encode())
        box = (
            "untrusted comment: signature from tauri secret key\n"
            + base64.b64encode(b"ED" + (key_id or self.key_id) + signature).decode()
            + f"\ntrusted comment: {trusted}\n"
            + base64.b64encode(global_sig).decode()
            + "\n"
        )
        return base64.b64encode(box.encode()).decode()


def test_the_committed_pubkey_decodes() -> None:
    key_id, public = dc.decode_pubkey(dc.read_conf_pubkey(TAURI_CONF))
    assert len(key_id) == 8 and len(public) == 32


def test_signature_verification() -> None:
    key = _TestKey()
    data = b"installer bytes"
    assert dc.verify_updater_signature(key.conf_pubkey, key.sign(data), data) == "verified"
    with pytest.raises(dc.ChannelError, match="does not verify"):
        dc.verify_updater_signature(key.conf_pubkey, key.sign(data), b"tampered")
    with pytest.raises(dc.ChannelError, match="replace plugins.updater.pubkey"):
        dc.verify_updater_signature(_TestKey().conf_pubkey, key.sign(data), data)
    with pytest.raises(dc.ChannelError):
        dc.verify_updater_signature(key.conf_pubkey, "not-a-signature", data)


# --------------------------------------------------------------------------- #
# Staging and manifests
# --------------------------------------------------------------------------- #
def _bundle(root: Path, platform: str, *, key: _TestKey | None) -> Path:
    bundle = root / platform / "bundle"
    if platform.startswith("windows"):
        target = bundle / "nsis" / "Lattix Locus_0.2.3_x64-setup.exe"
    else:
        target = bundle / "macos" / "Lattix Locus.app.tar.gz"
        (bundle / "dmg").mkdir(parents=True)
        (bundle / "dmg" / "Lattix Locus_0.2.3_aarch64.dmg").write_bytes(b"dmg")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{platform}-payload".encode()
    target.write_bytes(payload)
    if key is not None:
        Path(f"{target}.sig").write_text(key.sign(payload), encoding="utf-8")
    return bundle


def _stage_all(tmp_path: Path, key: _TestKey | None) -> Path:
    assets = tmp_path / "assets"
    for platform in ("windows-x86_64", "darwin-aarch64"):
        dc.stage_bundle(_bundle(tmp_path, platform, key=key), platform, "0.2.3", assets)
    return assets


def test_stage_renames_to_stable_asset_names(tmp_path: Path) -> None:
    assets = _stage_all(tmp_path, _TestKey())
    names = sorted(p.name for p in assets.iterdir())
    assert "Lattix-Locus_0.2.3_windows-x86_64-setup.exe" in names
    assert "Lattix-Locus_0.2.3_windows-x86_64-setup.exe.sig" in names
    assert "Lattix-Locus_0.2.3_darwin-aarch64.app.tar.gz.sig" in names
    assert "Lattix-Locus_0.2.3_darwin-aarch64.dmg" in names
    assert all(" " not in name for name in names)


def test_manifest_is_signed_verified_and_points_at_the_release(tmp_path: Path) -> None:
    key = _TestKey()
    assets = _stage_all(tmp_path, key)
    manifest = dc.build_manifest(
        assets,
        version="0.2.3",
        repo=REPO,
        tag="dev-v0.2.3",
        pubkey=key.conf_pubkey,
        require_crypto=True,
        now=datetime(2026, 10, 3, tzinfo=UTC),
    )
    assert manifest["version"] == "0.2.3"
    assert manifest["pub_date"] == "2026-10-03T00:00:00Z"
    assert set(manifest["platforms"]) == {"windows-x86_64", "darwin-aarch64"}
    for entry in manifest["platforms"].values():
        assert entry["url"].startswith(
            f"https://github.com/{REPO}/releases/download/dev-v0.2.3/Lattix-Locus_0.2.3_"
        )
        assert entry["signature"]


def test_no_manifest_without_signatures(tmp_path: Path) -> None:
    assets = _stage_all(tmp_path, key=None)
    with pytest.raises(dc.ChannelError, match="refusing to write unsigned metadata"):
        dc.build_manifest(assets, version="0.2.3", repo=REPO, tag="dev-v0.2.3")
    out = tmp_path / "latest.json"
    args = ["manifest", "--assets", str(assets), "--version", "0.2.3"]
    args += ["--repo", REPO, "--tag", "dev-v0.2.3", "--out", str(out)]
    assert dc.main(args) == 1
    assert not out.exists()


def test_no_manifest_when_signed_with_another_key(tmp_path: Path) -> None:
    assets = _stage_all(tmp_path, _TestKey())
    with pytest.raises(dc.ChannelError, match="replace plugins.updater.pubkey"):
        dc.build_manifest(
            assets,
            version="0.2.3",
            repo=REPO,
            tag="dev-v0.2.3",
            pubkey=_TestKey().conf_pubkey,
        )


def test_manifest_rejects_a_version_mix_and_bad_names(tmp_path: Path) -> None:
    assets = _stage_all(tmp_path, _TestKey())
    with pytest.raises(dc.ChannelError):
        dc.build_manifest(assets, version="0.2.4", repo=REPO, tag="dev-v0.2.4")
    with pytest.raises(dc.ChannelError):
        dc.build_manifest(assets, version="0.2.3", repo="evil.example/x/y", tag="t")
    with pytest.raises(dc.ChannelError):
        dc.asset_url(REPO, "dev-v1", "../latest.json")


def test_promote_reuses_the_same_files_and_signatures(tmp_path: Path) -> None:
    key = _TestKey()
    assets = _stage_all(tmp_path, key)
    dev = dc.build_manifest(assets, version="0.2.3", repo=REPO, tag="dev-v0.2.3")
    stable = dc.promote_manifest(dev, assets=assets, repo=REPO, tag="stable-v0.2.3")
    assert stable["version"] == dev["version"]
    for platform, entry in stable["platforms"].items():
        assert entry["signature"] == dev["platforms"][platform]["signature"]
        assert "/releases/download/stable-v0.2.3/" in entry["url"]
        assert (
            entry["url"].rsplit("/", 1)[-1] == dev["platforms"][platform]["url"].rsplit("/", 1)[-1]
        )


def test_promote_refuses_unsigned_or_missing_assets(tmp_path: Path) -> None:
    key = _TestKey()
    assets = _stage_all(tmp_path, key)
    dev = dc.build_manifest(assets, version="0.2.3", repo=REPO, tag="dev-v0.2.3")
    unsigned = json.loads(json.dumps(dev))
    unsigned["platforms"]["windows-x86_64"]["signature"] = ""
    with pytest.raises(dc.ChannelError, match="unsigned"):
        dc.promote_manifest(unsigned, assets=assets, repo=REPO, tag="stable-v0.2.3")
    with pytest.raises(dc.ChannelError, match="not among the promoted assets"):
        dc.promote_manifest(dev, assets=tmp_path / "empty", repo=REPO, tag="stable-v0.2.3")
