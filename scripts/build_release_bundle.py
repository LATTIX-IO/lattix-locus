"""Build a release bundle with promotion and rollback metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_IMAGES = (
    "lattix-locus/orchestrator",
    "lattix-locus/agent-base",
)
DEFAULT_PROMOTION_ORDER = ("dev", "stage", "prod")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree_files(
    source_dir: Path, destination_dir: Path, *, relative_to: Path
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    if not source_dir.exists():
        return copied

    for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(source_dir)
        target = destination_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(
            {
                "name": path.name,
                "path": target.relative_to(relative_to).as_posix(),
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
            }
        )
    return copied


def build_release_bundle(
    *,
    version: str,
    repo: str,
    git_sha: str,
    output_root: Path,
    chart_dist: Path,
    installer_dist: Path,
    previous_version: str | None = None,
    images: tuple[str, ...] = DEFAULT_IMAGES,
    promotion_order: tuple[str, ...] = DEFAULT_PROMOTION_ORDER,
) -> Path:
    normalized_version = str(version).strip()
    if not normalized_version:
        raise ValueError("version is required")

    bundle_dir = output_root / normalized_version
    artifacts_dir = bundle_dir / "artifacts"
    chart_bundle_dir = artifacts_dir / "chart"
    installer_bundle_dir = artifacts_dir / "installer"
    chart_bundle_dir.mkdir(parents=True, exist_ok=True)
    installer_bundle_dir.mkdir(parents=True, exist_ok=True)

    chart_artifacts = _copy_tree_files(chart_dist, chart_bundle_dir, relative_to=bundle_dir)
    installer_artifacts = _copy_tree_files(
        installer_dist, installer_bundle_dir, relative_to=bundle_dir
    )

    if not chart_artifacts:
        raise ValueError(f"no chart artifacts found under {chart_dist}")
    if not installer_artifacts:
        raise ValueError(f"no installer artifacts found under {installer_dist}")

    images_payload = [
        {
            "name": image,
            "tag": normalized_version,
            "reference": f"{image}:{normalized_version}",
        }
        for image in images
    ]

    previous = str(previous_version or "").strip() or None
    manifest = {
        "schema_version": "locus-release-bundle/1.0",
        "version": normalized_version,
        "repo": repo,
        "git_sha": git_sha,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            "chart": chart_artifacts,
            "installer": installer_artifacts,
        },
        "images": images_payload,
        "promotion": {
            "environments": list(promotion_order),
            "gates": [
                "bundle-built",
                "release-assets-published",
                "environment-secret-validation",
                "foundry-smoke",
            ],
        },
        "rollback": {
            "previous_version": previous,
            "recommended_target": previous,
            "strategy": "promote-previous-release-bundle",
            "steps": [
                "retrieve prior release bundle",
                "validate target environment secrets",
                "re-run environment smoke checks",
                "promote previous release metadata to the target environment",
            ],
        },
    }

    promotion_plan = {
        "version": normalized_version,
        "environments": [
            {
                "name": environment,
                "requires_smoke": True,
                "requires_approval": environment in {"stage", "prod"},
            }
            for environment in promotion_order
        ],
    }
    rollback_plan = {
        "version": normalized_version,
        "target_version": previous,
        "environment_action": "manual-workflow-dispatch",
        "smoke_required": True,
    }

    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (bundle_dir / "promotion-plan.json").write_text(
        json.dumps(promotion_plan, indent=2), encoding="utf-8"
    )
    (bundle_dir / "rollback-plan.json").write_text(
        json.dumps(rollback_plan, indent=2), encoding="utf-8"
    )
    (bundle_dir / "RELEASE_NOTES.md").write_text(
        "\n".join(
            [
                f"# Release {normalized_version}",
                "",
                f"- Repository: `{repo}`",
                f"- Git SHA: `{git_sha}`",
                f"- Previous release: `{previous or 'none'}`",
                "- Promotion order: `dev -> stage -> prod`",
                "- Rollback path: promote the previous release bundle after smoke validation.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", default="")
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--chart-dist", default="dist/chart")
    parser.add_argument("--installer-dist", default="dist/installer")
    parser.add_argument("--output-root", default="dist/release")
    parser.add_argument("--previous-version", default="")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    bundle_dir = build_release_bundle(
        version=args.version,
        repo=str(args.repo or "").strip(),
        git_sha=str(args.git_sha or "").strip(),
        output_root=Path(args.output_root),
        chart_dist=Path(args.chart_dist),
        installer_dist=Path(args.installer_dist),
        previous_version=str(args.previous_version or "").strip() or None,
    )
    print(bundle_dir)  # noqa: T201


if __name__ == "__main__":
    main()
