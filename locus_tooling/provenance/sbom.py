"""SBOM for one inspected artifact: syft when installed, else a pure-Python fallback.

The fallback reads the unpacked wheel's ``*.dist-info`` metadata (the same data
``importlib.metadata`` reads from an installed distribution) and writes a minimal
CycloneDX 1.5 JSON document: the package with its PyPI purl, license and artifact
hash, its declared requirements, and every native binary in the wheel as a
``file`` component with its SHA-256. The attestation records which generator ran.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from importlib.metadata import PathDistribution
from pathlib import Path
from typing import Any

from .static_rules import NATIVE_SUFFIXES

FALLBACK_GENERATOR = "locus-provenance sbom fallback (dist-info metadata, CycloneDX 1.5)"


@dataclass(frozen=True)
class SbomResult:
    document: dict[str, Any]
    generator: str
    generator_kind: str  # syft | locus-fallback

    @property
    def components(self) -> int:
        return len(self.document.get("components") or [])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{name.lower()}@{version}"


def fallback_sbom(
    unpacked: Path, *, name: str, version: str, artifact_sha256: str = ""
) -> SbomResult:
    """CycloneDX from the dist-info metadata of an unpacked wheel (deterministic)."""
    dist_infos = sorted(unpacked.glob("*.dist-info"))
    meta: dict[str, Any] = {}
    requires: list[str] = []
    if dist_infos:
        dist = PathDistribution(dist_infos[0])
        metadata = dist.metadata
        meta = {
            "name": metadata.get("Name") or name,
            "version": metadata.get("Version") or version,
            "license": metadata.get("License-Expression") or metadata.get("License") or "",
            "author": metadata.get("Author-email") or metadata.get("Author") or "",
        }
        requires = list(dist.requires or [])
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": _purl(name, version),
        "name": meta.get("name", name),
        "version": meta.get("version", version),
        "purl": _purl(name, version),
    }
    if meta.get("license"):
        component["licenses"] = [{"license": {"name": str(meta["license"])[:200]}}]
    if meta.get("author"):
        component["author"] = str(meta["author"])
    if artifact_sha256:
        component["hashes"] = [{"alg": "SHA-256", "content": artifact_sha256}]
    if requires:
        component["properties"] = [
            {"name": "locus:requires-dist", "value": requirement} for requirement in requires
        ]
    components = [component]
    for path in sorted(unpacked.rglob("*")):
        if path.is_file() and (path.suffix.lower() in NATIVE_SUFFIXES or ".so." in path.name):
            rel = path.relative_to(unpacked).as_posix()
            components.append(
                {
                    "type": "file",
                    "bom-ref": f"{_purl(name, version)}#{rel}",
                    "name": rel,
                    "hashes": [{"alg": "SHA-256", "content": _sha256(path)}],
                    "properties": [{"name": "locus:native", "value": "true"}],
                }
            )
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, _purl(name, version))}",
        "version": 1,
        "metadata": {
            "tools": [{"name": "locus-provenance", "version": "1"}],
            "component": {"type": "library", "name": name, "version": version},
        },
        "components": components,
    }
    return SbomResult(document, FALLBACK_GENERATOR, "locus-fallback")


def syft_sbom(unpacked: Path, *, syft: str) -> SbomResult:
    version = subprocess.run([syft, "version"], capture_output=True, text=True, check=False)
    label = next(
        (
            line.split(":", 1)[1].strip()
            for line in version.stdout.splitlines()
            if line.startswith("Version")
        ),
        "unknown",
    )
    proc = subprocess.run(
        [syft, "scan", f"dir:{unpacked}", "-o", "cyclonedx-json", "-q"],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    return SbomResult(json.loads(proc.stdout), f"syft {label}", "syft")


def generate(
    unpacked: Path, *, name: str, version: str, artifact_sha256: str = "", prefer_syft: bool = True
) -> SbomResult:
    syft = shutil.which("syft") if prefer_syft else None
    if syft:
        return syft_sbom(unpacked, syft=syft)
    return fallback_sbom(unpacked, name=name, version=version, artifact_sha256=artifact_sha256)


def write(result: SbomResult, path: Path) -> str:
    """Write the SBOM (stable formatting) and return its SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result.document, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
