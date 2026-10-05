from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_INSTALLER_PATH = REPO_ROOT / "install" / "locus-installer.py"


def _load_public_installer_module():
    spec = importlib.util.spec_from_file_location(
        "locus_public_installer", PUBLIC_INSTALLER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_installer_detects_bundled_repo_root(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True, exist_ok=True)
    script_path = install_dir / "locus-installer.py"
    script_path.write_text("# placeholder\n", encoding="utf-8")
    packaged_installer = tmp_path / "locus_tooling" / "installer.py"
    packaged_installer.parent.mkdir(parents=True, exist_ok=True)
    packaged_installer.write_text("# packaged installer\n", encoding="utf-8")

    module = _load_public_installer_module()

    assert module._bundled_repo_root(script_path) == tmp_path


def test_public_installer_prefers_bundled_repo_over_remote_archive(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_public_installer_module()
    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir(parents=True, exist_ok=True)
    captured: list[Path] = []

    monkeypatch.setattr(module, "_bundled_repo_root", lambda script_path: bundled_root)
    monkeypatch.setattr(
        module,
        "_download_repo_archive",
        lambda target_dir: (_ for _ in ()).throw(
            AssertionError("remote archive should not be downloaded")
        ),
    )
    monkeypatch.setattr(
        module, "_run_packaged_installer", lambda repo_root: captured.append(repo_root)
    )

    module.main()

    assert captured == [bundled_root]
