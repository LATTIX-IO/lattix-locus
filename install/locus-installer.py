#!/usr/bin/env python3
"""Public bootstrap that fetches the packaged Locus installer if needed."""

from __future__ import annotations

import http.client
import importlib
import io
import os
from pathlib import Path
import shutil
import ssl
import sys
import tempfile
from urllib.parse import urljoin, urlsplit
import zipfile

DEFAULT_ARCHIVE_URL = "https://github.com/LATTIX-IO/lattix-locus/archive/refs/heads/main.zip"


def _bundled_repo_root(script_path: Path) -> Path | None:
    install_dir = script_path.resolve().parent
    candidate = install_dir.parent
    packaged = candidate / "locus_tooling" / "installer.py"
    return candidate if packaged.exists() else None


def _validated_archive_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SystemExit("Installer archive URL must use http or https.")
    if not parsed.hostname:
        raise SystemExit("Installer archive URL must include a host.")
    if parsed.username or parsed.password:
        raise SystemExit("Installer archive URL must not include credentials.")
    if parsed.fragment:
        raise SystemExit("Installer archive URL must not include fragments.")
    return parsed.geturl()


def _download_url_bytes(url: str, *, redirects_remaining: int = 3) -> bytes:
    validated_url = _validated_archive_url(url)
    parsed = urlsplit(validated_url)
    connection: http.client.HTTPConnection
    if parsed.scheme.lower() == "https":
        # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port,
            timeout=30,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)

    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    try:
        connection.request("GET", target, headers={"User-Agent": "locus-public-installer/1.0"})
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            location = response.getheader("Location")
            if not location or redirects_remaining <= 0:
                raise SystemExit("Installer archive download redirected too many times.")
            return _download_url_bytes(
                urljoin(validated_url, location), redirects_remaining=redirects_remaining - 1
            )
        if response.status >= 400:
            raise SystemExit(f"Installer archive download failed with HTTP {response.status}.")
        return response.read()
    finally:
        connection.close()


def _download_repo_archive(target_dir: Path) -> Path:
    archive_url = _validated_archive_url(DEFAULT_ARCHIVE_URL)
    archive_bytes = _download_url_bytes(archive_url)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        archive.extractall(target_dir)
    extracted_roots = [path for path in target_dir.iterdir() if path.is_dir()]
    if not extracted_roots:
        raise SystemExit("Downloaded installer archive did not contain a repository directory.")
    return extracted_roots[0]


def _run_packaged_installer(repo_root: Path) -> None:
    sys.path.insert(0, str(repo_root))
    importlib.invalidate_caches()
    module = importlib.import_module("locus_tooling.installer")
    module.main()


def main() -> None:
    bundled_repo_root = _bundled_repo_root(Path(__file__))
    if bundled_repo_root is not None:
        os.chdir(bundled_repo_root)
        _run_packaged_installer(bundled_repo_root)
        return

    temp_root = Path(tempfile.mkdtemp(prefix="locus-public-installer-"))
    try:
        repo_root = _download_repo_archive(temp_root)
        packaged = repo_root / "locus_tooling" / "installer.py"
        if not packaged.exists():
            raise SystemExit("The downloaded archive did not include locus_tooling/installer.py")
        os.chdir(repo_root)
        _run_packaged_installer(repo_root)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
