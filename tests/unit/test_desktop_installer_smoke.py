"""LOCUS-475: NSIS must contain the same hashed frontend bytes as its stage."""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from scripts.desktop_installer_smoke import InstallerSmokeError, verify_extracted_frontend


def _frontend(root: Path) -> Path:
    frontend = root / "resources" / "frontend"
    static = frontend / ".next" / "static" / "chunks"
    static.mkdir(parents=True)
    (frontend / "server.js").write_text("server", encoding="utf-8")
    (frontend / ".next" / "BUILD_ID").write_text("build-123", encoding="utf-8")
    (static / "runtime.js").write_text("runtime-123", encoding="utf-8")
    (static / "main.js").write_text("main-123", encoding="utf-8")
    (static / "app.css").write_text("body { color: black }", encoding="utf-8")
    return frontend


def test_nsis_manifest_exact_match(tmp_path: Path) -> None:
    staged = _frontend(tmp_path / "staged")
    packed = tmp_path / "packed" / "resources" / "frontend"
    shutil.copytree(staged, packed)
    assert verify_extracted_frontend(staged, packed) == 3


def test_nsis_missing_one_late_js_file_fails(tmp_path: Path) -> None:
    staged = _frontend(tmp_path / "staged")
    packed = tmp_path / "packed" / "resources" / "frontend"
    shutil.copytree(staged, packed)
    (packed / ".next" / "static" / "chunks" / "main.js").unlink()
    with pytest.raises(InstallerSmokeError, match="missing="):
        verify_extracted_frontend(staged, packed)


def test_nsis_missing_stylesheet_fails(tmp_path: Path) -> None:
    staged = _frontend(tmp_path / "staged")
    packed = tmp_path / "packed" / "resources" / "frontend"
    shutil.copytree(staged, packed)
    (packed / ".next" / "static" / "chunks" / "app.css").unlink()
    with pytest.raises(InstallerSmokeError, match="app.css"):
        verify_extracted_frontend(staged, packed)


def test_nsis_corrupted_chunk_fails(tmp_path: Path) -> None:
    staged = _frontend(tmp_path / "staged")
    packed = tmp_path / "packed" / "resources" / "frontend"
    shutil.copytree(staged, packed)
    (packed / ".next" / "static" / "chunks" / "main.js").write_text("wrong", encoding="utf-8")
    with pytest.raises(InstallerSmokeError, match="content hash mismatch"):
        verify_extracted_frontend(staged, packed)


def test_nsis_stale_build_id_fails(tmp_path: Path) -> None:
    staged = _frontend(tmp_path / "staged")
    packed = tmp_path / "packed" / "resources" / "frontend"
    shutil.copytree(staged, packed)
    (packed / ".next" / "BUILD_ID").write_text("old-build", encoding="utf-8")
    with pytest.raises(InstallerSmokeError, match="BUILD_ID"):
        verify_extracted_frontend(staged, packed)


def test_nsis_nested_static_tree_fails(tmp_path: Path) -> None:
    staged = _frontend(tmp_path / "staged")
    packed = tmp_path / "packed" / "resources" / "frontend"
    shutil.copytree(staged, packed)
    (packed / ".next" / "static" / "static").mkdir()
    with pytest.raises(InstallerSmokeError, match="nested"):
        verify_extracted_frontend(staged, packed)
