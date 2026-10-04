"""The pinned Open Policy Agent release that Locus ships and fetches.

The gateway fails closed without a policy engine: every model call, tool call
and computer-use action is denied with ``policy_engine_unavailable``. The
desktop bundle therefore carries its own OPA binary, and native installs fetch
the same pinned build. Standard library only, so the desktop CI workflows can
run it before anything else is installed::

    python -m locus_tooling.opa_release fetch --triple x86_64-pc-windows-msvc --dest dist/locus-opa.exe
    python -m locus_tooling.opa_release check --binary dist/locus-opa.exe

Pins (P28 provenance: official URL + sha256): Open Policy Agent, a CNCF
graduated project (Apache-2.0), release ``v{OPA_VERSION}`` on
https://github.com/open-policy-agent/opa/releases. Each sha256 is the value the
release publishes beside the asset (``<asset>.sha256``); the same values are
served at ``https://openpolicyagent.org/downloads/v{OPA_VERSION}/<asset>.sha256``.
Bumping the version means updating ``OPA_VERSION``, every sha256 below and the
pins in ``.github/workflows/ci.yml`` / ``docker-compose.yml``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

OPA_VERSION = "0.68.0"
RELEASE_BASE_URL = f"https://github.com/open-policy-agent/opa/releases/download/v{OPA_VERSION}"
#: File name of the bundled binary next to the desktop sidecar (Tauri
#: ``externalBin`` strips the target-triple suffix at install time).
BUNDLED_STEM = "locus-opa"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
_VERSION_RE = re.compile(r"^Version:\s*(\S+)\s*$", re.MULTILINE)
_DOWNLOAD_TIMEOUT_SECONDS = 120
_PROBE_TIMEOUT_SECONDS = 30


class OpaReleaseError(RuntimeError):
    """No pinned asset for the platform, a failed download, or a digest mismatch."""


@dataclass(frozen=True)
class OpaAsset:
    """One release asset: a single raw executable (no archive)."""

    name: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASE_BASE_URL}/{self.name}"


#: Keyed by ``(os, arch)`` as :func:`locus_tooling.native_binaries.current_platform`
#: reports it. Static builds where OPA publishes one (no libc dependency).
ASSETS: dict[tuple[str, str], OpaAsset] = {
    ("windows", "amd64"): OpaAsset(
        "opa_windows_amd64.exe",
        "fb147aa46a204337d169b2d721139f99e7ea7a88448f47abb7ff5533fe367522",
    ),
    ("darwin", "arm64"): OpaAsset(
        "opa_darwin_arm64_static",
        "bde5d5f1b50b19d4f044a8a10cc018a324aa5ca014dd81cf7a0c89c68533dda7",
    ),
    ("darwin", "amd64"): OpaAsset(
        "opa_darwin_amd64",
        "cbe0f536725ddd594c7c44c298a20a95bc7eb63b5404d240b92199ef24573d41",
    ),
    ("linux", "amd64"): OpaAsset(
        "opa_linux_amd64_static",
        "dfd5081fc6f930dfeaf2a225e31e616fc227dc0c7b43019b73d6f8fb8a1de1aa",
    ),
    ("linux", "arm64"): OpaAsset(
        "opa_linux_arm64_static",
        "1a583e593cdf4931c0b0bbedd3c9f585012953449115bcc3e15b3806d0f5ee68",
    ),
}

#: Rust target triples the desktop workflows build, mapped to ``(os, arch)``.
TRIPLES: dict[str, tuple[str, str]] = {
    "x86_64-pc-windows-msvc": ("windows", "amd64"),
    "aarch64-apple-darwin": ("darwin", "arm64"),
    "x86_64-apple-darwin": ("darwin", "amd64"),
    "x86_64-unknown-linux-gnu": ("linux", "amd64"),
}


def asset_for(os_name: str, arch: str) -> OpaAsset:
    asset = ASSETS.get((os_name, arch))
    if asset is None:
        raise OpaReleaseError(f"no pinned OPA {OPA_VERSION} build for {os_name}/{arch}")
    return asset


def asset_for_triple(triple: str) -> OpaAsset:
    platform_key = TRIPLES.get(triple)
    if platform_key is None:
        raise OpaReleaseError(f"no pinned OPA {OPA_VERSION} build for target {triple!r}")
    return asset_for(*platform_key)


def bundled_binary_name(windows: bool | None = None) -> str:
    """``locus-opa.exe`` on Windows, ``locus-opa`` elsewhere."""
    on_windows = os.name == "nt" if windows is None else windows
    return f"{BUNDLED_STEM}.exe" if on_windows else BUNDLED_STEM


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """Raise :class:`OpaReleaseError` unless ``path`` hashes to ``expected``."""
    want = str(expected or "").strip().lower()
    if not _SHA256_RE.match(want):
        raise OpaReleaseError(f"invalid pinned sha256 for {path.name}: {expected!r}")
    got = sha256_of(path)
    if got != want:
        raise OpaReleaseError(f"sha256 mismatch for {path.name}: got {got}, want {want}")


def _default_download(url: str, dest: Path) -> None:
    from urllib.request import urlopen

    if not url.startswith("https://"):
        raise OpaReleaseError(f"refusing a non-HTTPS download: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp, open(dest, "wb") as fh:  # noqa: S310 - pinned HTTPS release URL
        shutil.copyfileobj(resp, fh)


def _make_executable(path: Path) -> None:
    if os.name == "nt":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


DownloadFn = Callable[[str, Path], None]


def fetch(asset: OpaAsset, dest: Path, *, download: DownloadFn = _default_download) -> Path:
    """Download ``asset``, verify its pinned sha256, then move it to ``dest``.

    Fails closed: on a download error or a digest mismatch nothing is written to
    ``dest`` and :class:`OpaReleaseError` is raised.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.download")
    try:
        try:
            download(asset.url, tmp)
        except OpaReleaseError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise OpaReleaseError(f"could not download {asset.url}: {exc}") from exc
        verify_sha256(tmp, asset.sha256)
        os.replace(tmp, dest)
        _make_executable(dest)
        return dest
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@dataclass(frozen=True)
class OpaProbe:
    """Result of running ``<binary> version``."""

    ok: bool
    binary: str
    version: str
    detail: str = ""


RunFn = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _default_run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv (a resolved binary + "version"), no shell
        list(argv),
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def parse_version(output: str) -> str:
    """The ``Version:`` line of ``opa version`` output, or ``""``."""
    match = _VERSION_RE.search(str(output or ""))
    return match.group(1) if match else ""


def probe(
    binary: str | Path | None,
    *,
    expected_version: str | None = OPA_VERSION,
    run: RunFn = _default_run,
) -> OpaProbe:
    """Run ``<binary> version``; ok only when it exits 0 (and reports
    ``expected_version`` when one is given)."""
    if not binary:
        return OpaProbe(False, "", "", "OPA binary not found")
    path = str(binary)
    if not Path(path).is_file():
        return OpaProbe(False, path, "", "OPA binary not found")
    try:
        completed = run([path, "version"])
    except (OSError, subprocess.SubprocessError) as exc:
        return OpaProbe(False, path, "", f"could not run OPA: {type(exc).__name__}: {exc}")
    version = parse_version(completed.stdout)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        return OpaProbe(
            False, path, version, f"'opa version' exited {completed.returncode}: {tail}"
        )
    if not version:
        return OpaProbe(False, path, "", "'opa version' printed no Version line")
    if expected_version and version != expected_version:
        return OpaProbe(False, path, version, f"OPA {version} is not the pinned {expected_version}")
    return OpaProbe(True, path, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m locus_tooling.opa_release")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch", help="download + verify the pinned OPA build")
    fetch_cmd.add_argument("--triple", required=True, help="Rust target triple")
    fetch_cmd.add_argument("--dest", required=True, help="where to write the binary")
    check_cmd = sub.add_parser("check", help="run '<binary> version' against the pin")
    check_cmd.add_argument("--binary", required=True)
    args = parser.parse_args(argv)

    if args.command == "fetch":
        try:
            asset = asset_for_triple(args.triple)
            path = fetch(asset, Path(args.dest))
        except OpaReleaseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"OPA {OPA_VERSION} ({asset.name}, sha256 {asset.sha256}) -> {path}")
        return 0

    result = probe(args.binary)
    if not result.ok:
        print(f"error: {result.detail}", file=sys.stderr)
        return 1
    print(f"OPA {result.version} OK: {result.binary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
