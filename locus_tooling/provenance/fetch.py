"""Network and archive helpers for an inspection: PyPI metadata, hash-checked
downloads, and safe unpacking. Nothing here executes downloaded content."""

from __future__ import annotations

import hashlib
import json
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
_USER_AGENT = "lattix-locus-provenance/1 (+https://github.com/LATTIX-IO/lattix-locus)"


class IntegrityError(RuntimeError):
    """A downloaded file does not match the hash its index published."""


def http_get(url: str, *, timeout: int = 60) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
        data: bytes = response.read()
        return data


def pypi_release(name: str, version: str) -> dict[str, Any]:
    data = json.loads(http_get(PYPI_JSON.format(name=name, version=version)).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"unexpected PyPI response for {name}=={version}")
    return data


def download(url: str, dest: Path, *, sha256: str) -> Path:
    """Download ``url`` to ``dest`` and verify it against the published SHA-256."""
    data = http_get(url, timeout=300)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256.lower():
        raise IntegrityError(f"{dest.name}: sha256 {actual} != published {sha256}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not (path.parts and ":" in path.parts[0])
    )


def unpack_wheel(wheel: Path, dest: Path) -> Path:
    """Extract a wheel (a zip) without executing anything; refuses path traversal."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if not _safe_member(member.filename):
                raise ValueError(f"{wheel.name}: unsafe member path {member.filename!r}")
        archive.extractall(dest)
    return dest


def unpack_sdist(sdist: Path, dest: Path) -> Path:
    """Extract an sdist tarball with the ``data`` filter (no links out, no devices)."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            if not _safe_member(member.name):
                raise ValueError(f"{sdist.name}: unsafe member path {member.name!r}")
        archive.extractall(dest, filter="data")
    return dest
