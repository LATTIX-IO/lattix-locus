"""Desktop update channels for CI (LOCUS-349, D-26).

Used by ``.github/workflows/desktop-dev.yml`` (merge to main -> Dev release) and
``desktop-promote.yml`` (Dev version -> Stable release, no rebuild). Pure Python,
standard library only, so it runs on any runner and in unit tests.

Subcommands::

    version        --tauri-conf PATH --run-number N         -> prints <base>-dev.<N>
    stage          --bundle-dir DIR --platform P --version V --out DIR
    manifest       --assets DIR --version V --repo O/R --tag T --out FILE
                   [--notes TEXT] [--pubkey-conf tauri.conf.json [--require-crypto]]
    should-advance --candidate V [--current FILE]           -> prints true|false
    promote        --manifest FILE --assets DIR --repo O/R --tag T --out FILE

Security rules this script enforces (fail closed):

* ``manifest`` writes ``latest.json`` only when **every** staged platform has an
  updater signature; it never writes unsigned or partial metadata. The workflow
  only calls it when the ``TAURI_SIGNING_PRIVATE_KEY`` secret is present, and
  with ``--pubkey-conf`` every signature must verify against the pubkey the app
  ships with (a key mismatch would otherwise break updates for every install).
* URLs point at ``https://github.com/<repo>/releases/download/<tag>/<asset>``
  only, with validated repo, tag and asset names.
* ``should-advance`` keeps a channel pointer monotonic: it never moves to an
  older or equal version (overlapping runs cannot roll the channel back).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCT_SLUG = "Lattix-Locus"
#: Updater platform keys built by the Dev workflow (Tauri v2 ``{os}-{arch}``).
PLATFORMS = ("windows-x86_64", "darwin-aarch64", "darwin-x86_64")

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_BASE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


class ChannelError(ValueError):
    """Invalid input or an unsafe publish; the workflow step fails."""


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #
def parse_semver(value: str) -> tuple[int, int, int, tuple[str, ...]]:
    match = _SEMVER.match(str(value or "").strip())
    if not match:
        raise ChannelError(f"not a semantic version: {value!r}")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), tuple(pre.split(".")) if pre else ()


def _pre_key(ident: str) -> tuple[int, int, str]:
    # Numeric identifiers sort numerically and before alphanumeric ones (SemVer 11.4).
    return (0, int(ident), "") if ident.isdigit() else (1, 0, ident)


def compare_semver(a: str, b: str) -> int:
    """SemVer 2.0 precedence (build metadata ignored): -1, 0 or 1."""
    a_major, a_minor, a_patch, a_pre = parse_semver(a)
    b_major, b_minor, b_patch, b_pre = parse_semver(b)
    if (a_major, a_minor, a_patch) != (b_major, b_minor, b_patch):
        return -1 if (a_major, a_minor, a_patch) < (b_major, b_minor, b_patch) else 1
    if a_pre == b_pre:
        return 0
    if not a_pre:
        return 1  # a release outranks its pre-releases
    if not b_pre:
        return -1
    for x, y in zip(a_pre, b_pre, strict=False):
        if x != y:
            return -1 if _pre_key(x) < _pre_key(y) else 1
    return -1 if len(a_pre) < len(b_pre) else 1


def read_base_version(tauri_conf: Path) -> str:
    data = json.loads(Path(tauri_conf).read_text(encoding="utf-8"))
    base = str(data.get("version") or "").strip()
    if not _BASE.match(base):
        raise ChannelError(f"tauri.conf.json version must be MAJOR.MINOR.PATCH, got {base!r}")
    return base


def dev_version(base: str, run_number: int | str) -> str:
    """``<base>-dev.<run_number>``: numeric last identifier, so dev.10 > dev.9."""
    if not _BASE.match(str(base or "").strip()):
        raise ChannelError(f"base version must be MAJOR.MINOR.PATCH, got {base!r}")
    try:
        number = int(str(run_number).strip())
    except ValueError as exc:
        raise ChannelError(f"run number must be an integer, got {run_number!r}") from exc
    if number <= 0:
        raise ChannelError("run number must be positive")
    return f"{base.strip()}-dev.{number}"


def should_advance(candidate: str, current: str | None) -> bool:
    """Move a channel pointer only forward."""
    parse_semver(candidate)
    if not current:
        return True
    return compare_semver(candidate, current) > 0


# --------------------------------------------------------------------------- #
# Assets and manifests
# --------------------------------------------------------------------------- #
def _check_repo_tag(repo: str, tag: str) -> None:
    if not _REPO.match(repo or ""):
        raise ChannelError(f"invalid repository: {repo!r}")
    if not _TAG.match(tag or ""):
        raise ChannelError(f"invalid tag: {tag!r}")


def asset_url(repo: str, tag: str, asset: str) -> str:
    _check_repo_tag(repo, tag)
    if not _ASSET.match(asset or ""):
        raise ChannelError(f"invalid asset name: {asset!r}")
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def _single(paths: list[Path], what: str) -> Path | None:
    if len(paths) > 1:
        raise ChannelError(f"expected one {what}, found {len(paths)}: {[p.name for p in paths]}")
    return paths[0] if paths else None


def stage_bundle(bundle_dir: Path, platform: str, version: str, out: Path) -> dict[str, Any]:
    """Copy one platform's installers and updater bundle under stable asset names.

    Windows: the NSIS ``-setup.exe`` is both installer and updater bundle (Tauri
    v2). macOS: the ``.app.tar.gz`` is the updater bundle, the ``.dmg`` the
    installer. A ``.sig`` is copied when the build signed the bundle.
    """
    if platform not in PLATFORMS:
        raise ChannelError(f"unknown platform {platform!r}")
    parse_semver(version)
    bundle_dir = Path(bundle_dir)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"{PRODUCT_SLUG}_{version}_{platform}"
    installers: list[str] = []
    if platform.startswith("windows"):
        updater_src = _single(sorted(bundle_dir.glob("nsis/*-setup.exe")), "NSIS installer")
        updater_name = f"{prefix}-setup.exe"
        if updater_src is not None:
            installers.append(updater_name)
    else:
        updater_src = _single(sorted(bundle_dir.glob("macos/*.app.tar.gz")), "updater bundle")
        updater_name = f"{prefix}.app.tar.gz"
        dmg = _single(sorted(bundle_dir.glob("dmg/*.dmg")), "dmg")
        if dmg is not None:
            shutil.copyfile(dmg, out / f"{prefix}.dmg")
            installers.append(f"{prefix}.dmg")
    record: dict[str, Any] = {
        "platform": platform,
        "version": version,
        "installers": installers,
        "updater": None,
        "signature": None,
    }
    if updater_src is not None:
        shutil.copyfile(updater_src, out / updater_name)
        record["updater"] = updater_name
        sig = Path(f"{updater_src}.sig")
        if sig.is_file():
            shutil.copyfile(sig, out / f"{updater_name}.sig")
            record["signature"] = f"{updater_name}.sig"
    if not installers and record["updater"] is None:
        raise ChannelError(f"no bundle found for {platform} under {bundle_dir}")
    (out / f"{platform}.platform.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def build_manifest(
    assets: Path,
    *,
    version: str,
    repo: str,
    tag: str,
    notes: str = "",
    now: datetime | None = None,
    pubkey: str | None = None,
    require_crypto: bool = False,
) -> dict[str, Any]:
    """The Tauri v2 ``latest.json``. Raises unless every platform is signed (and,
    with ``pubkey``, unless every signature verifies against the app's key)."""
    parse_semver(version)
    _check_repo_tag(repo, tag)
    assets = Path(assets)
    records = sorted(assets.glob("*.platform.json"))
    if not records:
        raise ChannelError(f"no staged platforms in {assets}")
    platforms: dict[str, dict[str, str]] = {}
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        platform = str(record.get("platform") or "")
        if platform not in PLATFORMS:
            raise ChannelError(f"unknown platform in {path.name}: {platform!r}")
        if str(record.get("version") or "") != version:
            raise ChannelError(
                f"{path.name} was built for {record.get('version')!r}, not {version}"
            )
        updater, signature = record.get("updater"), record.get("signature")
        if not updater or not signature:
            raise ChannelError(
                f"{platform} has no signed updater bundle; refusing to write unsigned metadata"
            )
        sig_text = (assets / str(signature)).read_text(encoding="utf-8").strip()
        if not sig_text or not (assets / str(updater)).is_file():
            raise ChannelError(f"{platform}: updater bundle or signature is missing or empty")
        if pubkey is not None:
            verify_updater_signature(
                pubkey,
                sig_text,
                (assets / str(updater)).read_bytes(),
                require_crypto=require_crypto,
            )
        platforms[platform] = {"signature": sig_text, "url": asset_url(repo, tag, str(updater))}
    stamp = (now or datetime.now(UTC)).replace(microsecond=0)
    return {
        "version": version,
        "notes": notes or f"Lattix Locus {version}",
        "pub_date": stamp.isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }


def promote_manifest(
    manifest: dict[str, Any], *, assets: Path, repo: str, tag: str
) -> dict[str, Any]:
    """Point a Dev ``latest.json`` at the Stable release's copies of the same files.

    The bytes are unchanged, so the signatures stay valid; each referenced file
    must be present in ``assets`` (the files that are uploaded to the release).
    """
    version = str(manifest.get("version") or "")
    parse_semver(version)
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ChannelError("the Dev manifest has no platforms")
    out_platforms: dict[str, dict[str, str]] = {}
    for platform, entry in platforms.items():
        if platform not in PLATFORMS or not isinstance(entry, dict):
            raise ChannelError(f"unexpected platform entry {platform!r}")
        signature = str(entry.get("signature") or "").strip()
        asset = str(entry.get("url") or "").rsplit("/", 1)[-1]
        if not signature:
            raise ChannelError(f"{platform} is unsigned; refusing to promote it")
        if not (Path(assets) / asset).is_file():
            raise ChannelError(f"{platform}: {asset} is not among the promoted assets")
        out_platforms[platform] = {"signature": signature, "url": asset_url(repo, tag, asset)}
    return {**manifest, "platforms": out_platforms}


# --------------------------------------------------------------------------- #
# Minisign verification (what the Tauri updater does on the client)
# --------------------------------------------------------------------------- #
def _b64_box_lines(value: str, what: str) -> list[str]:
    """Tauri stores minisign boxes base64-encoded (the conf pubkey and ``.sig`` files)."""
    try:
        text = base64.b64decode(str(value or "").strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ChannelError(f"{what} is not a base64 minisign box") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ChannelError(f"{what} is not a minisign box")
    return lines


def _b64(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ChannelError(f"{what} is not valid base64") from exc


def decode_pubkey(conf_pubkey: str) -> tuple[bytes, bytes]:
    """``(key_id, ed25519_public_key)`` from ``plugins.updater.pubkey``."""
    raw = _b64(_b64_box_lines(conf_pubkey, "the updater pubkey")[1], "the updater pubkey")
    if len(raw) != 42 or raw[:2] != b"Ed":
        raise ChannelError("the updater pubkey is not a minisign Ed25519 public key")
    return raw[2:10], raw[10:]


def key_id_label(key_id: bytes) -> str:
    """The id minisign prints (little-endian hex)."""
    return key_id[::-1].hex().upper()


def decode_signature(sig_text: str) -> dict[str, Any]:
    lines = _b64_box_lines(sig_text, "the updater signature")
    if len(lines) < 4 or not lines[2].startswith("trusted comment: "):
        raise ChannelError("the updater signature has no trusted comment")
    raw = _b64(lines[1], "the updater signature")
    if len(raw) != 74 or raw[:2] not in {b"Ed", b"ED"}:
        raise ChannelError("the updater signature is not a minisign Ed25519 signature")
    return {
        "algorithm": raw[:2],
        "key_id": raw[2:10],
        "signature": raw[10:],
        "trusted_comment": lines[2][len("trusted comment: ") :],
        "global_signature": _b64(lines[3], "the updater global signature"),
    }


def verify_updater_signature(
    conf_pubkey: str, sig_text: str, data: bytes, *, require_crypto: bool = False
) -> str:
    """Check a bundle the way the installed app will, before anything is published.

    Always checks that the signing key is the key the app trusts (key id). With
    the ``cryptography`` package it also verifies both Ed25519 signatures. Returns
    ``"verified"`` or ``"key-id"``; raises :class:`ChannelError` on any mismatch.
    """
    key_id, public_key = decode_pubkey(conf_pubkey)
    sig = decode_signature(sig_text)
    if sig["key_id"] != key_id:
        raise ChannelError(
            f"the bundle was signed with key {key_id_label(sig['key_id'])} but the app "
            f"trusts key {key_id_label(key_id)}: replace plugins.updater.pubkey in "
            "tauri.conf.json with the public key of TAURI_SIGNING_PRIVATE_KEY"
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        if require_crypto:
            raise ChannelError("the cryptography package is required to verify signatures") from exc
        return "key-id"
    # "ED" is minisign's prehashed mode (BLAKE2b-512 of the file), the Tauri default.
    message = hashlib.blake2b(data, digest_size=64).digest() if sig["algorithm"] == b"ED" else data
    verifier = Ed25519PublicKey.from_public_bytes(public_key)
    try:
        verifier.verify(sig["signature"], message)
        verifier.verify(
            sig["global_signature"], sig["signature"] + sig["trusted_comment"].encode("utf-8")
        )
    except InvalidSignature as exc:
        raise ChannelError(
            "the updater signature does not verify against the app's pubkey"
        ) from exc
    return "verified"


def read_conf_pubkey(tauri_conf: Path) -> str:
    data = json.loads(Path(tauri_conf).read_text(encoding="utf-8"))
    pubkey = str(((data.get("plugins") or {}).get("updater") or {}).get("pubkey") or "")
    if not pubkey:
        raise ChannelError("tauri.conf.json has no plugins.updater.pubkey")
    return pubkey


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_manifest_version(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError:
        return None
    version = str(data.get("version") or "") if isinstance(data, dict) else ""
    return version or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="desktop_channel")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("version")
    p.add_argument("--tauri-conf", required=True)
    p.add_argument("--run-number", required=True)

    p = sub.add_parser("stage")
    p.add_argument("--bundle-dir", required=True)
    p.add_argument("--platform", required=True, choices=PLATFORMS)
    p.add_argument("--version", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("manifest")
    p.add_argument("--assets", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--pubkey-conf", default=None, help="tauri.conf.json whose pubkey must verify")
    p.add_argument("--require-crypto", action="store_true")

    p = sub.add_parser("should-advance")
    p.add_argument("--candidate", required=True)
    p.add_argument("--current", default=None)

    p = sub.add_parser("promote")
    p.add_argument("--manifest", required=True)
    p.add_argument("--assets", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "version":
            print(dev_version(read_base_version(Path(args.tauri_conf)), args.run_number))
        elif args.cmd == "stage":
            record = stage_bundle(
                Path(args.bundle_dir), args.platform, args.version, Path(args.out)
            )
            print(json.dumps(record))
        elif args.cmd == "manifest":
            manifest = build_manifest(
                Path(args.assets),
                version=args.version,
                repo=args.repo,
                tag=args.tag,
                notes=args.notes,
                pubkey=read_conf_pubkey(Path(args.pubkey_conf)) if args.pubkey_conf else None,
                require_crypto=args.require_crypto,
            )
            Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {args.out} ({', '.join(sorted(manifest['platforms']))})")
        elif args.cmd == "should-advance":
            current = _read_manifest_version(args.current)
            print("true" if should_advance(args.candidate, current) else "false")
        elif args.cmd == "promote":
            source = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            promoted = promote_manifest(
                source, assets=Path(args.assets), repo=args.repo, tag=args.tag
            )
            Path(args.out).write_text(json.dumps(promoted, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
    except (ChannelError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
