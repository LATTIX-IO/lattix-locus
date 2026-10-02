from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _chart_value(field: str) -> str:
    match = re.search(
        rf"^{re.escape(field)}:\s*([^\n]+)$",
        _read("helm/lattix-locus/Chart.yaml"),
        flags=re.MULTILINE,
    )
    assert match is not None, f"{field} missing from Chart.yaml"
    return match.group(1).strip().strip('"').strip("'")


def test_public_version_metadata_stays_in_sync() -> None:
    pyproject = tomllib.loads(_read("pyproject.toml"))
    frontend_package = json.loads(_read("apps/frontend/package.json"))
    installer_manifest = json.loads(_read("install/manifest.json"))

    project_version = str(pyproject["project"]["version"])

    assert frontend_package["version"] == project_version
    assert installer_manifest["version"] == project_version
    assert _chart_value("version") == project_version
    assert _chart_value("appVersion") == project_version
