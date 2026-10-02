from __future__ import annotations

import json
from pathlib import Path

from scripts.build_release_bundle import build_release_bundle


def test_build_release_bundle_copies_artifacts_and_writes_manifests(tmp_path: Path) -> None:
    chart_dist = tmp_path / "chart"
    installer_dist = tmp_path / "installer"
    output_root = tmp_path / "release"

    chart_dist.mkdir()
    installer_dist.mkdir()
    (chart_dist / "lattix-locus-1.2.3.tgz").write_text("chart-bytes", encoding="utf-8")
    (installer_dist / "bootstrap.ps1").write_text("Write-Host ok", encoding="utf-8")
    (installer_dist / "manifest.json").write_text('{"installer": true}', encoding="utf-8")

    bundle_dir = build_release_bundle(
        version="v1.2.3",
        repo="LATTIX-IO/lattix-locus",
        git_sha="abc123",
        output_root=output_root,
        chart_dist=chart_dist,
        installer_dist=installer_dist,
        previous_version="v1.2.2",
    )

    assert bundle_dir == output_root / "v1.2.3"
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    promotion = json.loads((bundle_dir / "promotion-plan.json").read_text(encoding="utf-8"))
    rollback = json.loads((bundle_dir / "rollback-plan.json").read_text(encoding="utf-8"))

    assert manifest["version"] == "v1.2.3"
    assert manifest["rollback"]["previous_version"] == "v1.2.2"
    assert manifest["images"][0]["reference"] == "lattix-locus/orchestrator:v1.2.3"
    assert promotion["environments"][1]["name"] == "stage"
    assert promotion["environments"][1]["requires_approval"] is True
    assert rollback["target_version"] == "v1.2.2"

    copied_chart = bundle_dir / "artifacts" / "chart" / "lattix-locus-1.2.3.tgz"
    copied_installer = bundle_dir / "artifacts" / "installer" / "bootstrap.ps1"
    assert copied_chart.exists()
    assert copied_installer.exists()
    assert any(
        item["path"].endswith("artifacts/chart/lattix-locus-1.2.3.tgz")
        for item in manifest["artifacts"]["chart"]
    )


def test_build_release_bundle_requires_artifacts(tmp_path: Path) -> None:
    chart_dist = tmp_path / "chart"
    installer_dist = tmp_path / "installer"
    chart_dist.mkdir()
    installer_dist.mkdir()
    (installer_dist / "bootstrap.ps1").write_text("Write-Host ok", encoding="utf-8")

    try:
        build_release_bundle(
            version="v1.2.3",
            repo="LATTIX-IO/lattix-locus",
            git_sha="abc123",
            output_root=tmp_path / "release",
            chart_dist=chart_dist,
            installer_dist=installer_dist,
        )
    except ValueError as exc:
        assert "no chart artifacts found" in str(exc)
    else:
        raise AssertionError("expected build_release_bundle to reject missing chart artifacts")
