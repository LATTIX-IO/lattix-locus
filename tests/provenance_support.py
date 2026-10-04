"""Builders for D-29 attestation records used by the provenance tests (LOCUS-358)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from locus_tooling.provenance.records import (
    REVIEWER_AGENT_PENDING,
    REVIEWER_PRINCIPAL,
    attestation_relpath,
)

SHA = "a" * 64
DATE = "2026-10-04"


def _checks() -> list[dict[str, Any]]:
    return [
        {
            "list": name,
            "method": "test screening",
            "source": "https://example.test/list",
            "date": DATE,
            "queries": ["Example Maintainer"],
            "result": "no-match",
            "matches": [],
        }
        for name in ("us-commerce-entity-list", "dod-1260h")
    ]


def package_record(
    *,
    name: str = "examplepkg",
    version: str = "1.0.0",
    signed: bool = True,
    outcome: str = "pass",
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "kind": "package",
        "ecosystem": "pypi",
        "name": name,
        "version": version,
        "artifacts": [
            {"filename": f"{name}-{version}-py3-none-any.whl", "sha256": SHA, "inspected": True}
        ],
        "origin": {
            "p28_status": "listed",
            "countries": ["CN"],
            "summary": "test fixture from a listed origin",
            "confidence": "high",
            "evidence": [{"claim": "fixture", "url": "https://example.test/", "retrieved": DATE}],
        },
        "maintainers": [{"name": "Example Maintainer", "role": "author"}],
        "funding": {"summary": "none", "sources": []},
        "entity_list_checks": _checks(),
        "sbom": {
            "generator": "test",
            "generator_kind": "locus-fallback",
            "format": "CycloneDX JSON",
            "path": f"sbom/pypi/{name}@{version}.cdx.json",
            "sha256": SHA,
            "components": 1,
        },
        "vulnerabilities": {
            "source": "OSV",
            "mode": "online",
            "date": DATE,
            "status": "none-known",
            "results": [],
        },
        "static_findings": {"ruleset": "test", "files_scanned": 3, "findings": []},
        "dynamic_egress": {
            "status": "pass",
            "isolation": "test jail",
            "network": "denied",
            "date": DATE,
            "jail_probe": "blocked",
            "attempts": [],
            "limitations": [],
        },
        "decision": {"outcome": outcome, "conditions": [], "rationale": "fixture"},
        "review": {
            "reviewer": REVIEWER_PRINCIPAL if signed else REVIEWER_AGENT_PENDING,
            "date": DATE,
        },
    }


def model_record(
    *,
    name: str = "qwen2.5-coder",
    tag: str = "7b",
    digest: str = SHA,
    signed: bool = True,
    eval_status: str = "pass",
) -> dict[str, Any]:
    record = package_record(name=name, version=tag, signed=signed)
    record.update(
        {
            "kind": "model",
            "ecosystem": "ollama",
            "artifacts": [{"filename": f"sha256-{digest}", "sha256": digest, "inspected": True}],
            "vulnerabilities": {
                "source": "n/a",
                "mode": "not-applicable",
                "date": DATE,
                "status": "not-applicable",
                "results": [],
            },
            "dynamic_egress": {
                "status": "not-run",
                "isolation": "n/a",
                "network": "denied",
                "date": DATE,
                "attempts": [],
                "limitations": [],
            },
            "model": {
                "lineage": "qwen",
                "served_as": [{"engine": "ollama", "model": f"{name}:{tag}"}],
                "format_check": {
                    "status": "pass",
                    "weights": [{"path": f"sha256-{digest}", "format": "gguf", "sha256": digest}],
                    "refused": [],
                },
                "behavioural_eval": {"status": eval_status, "tracking": "LOCUS-351"},
                "inference_network": "loopback-only",
            },
        }
    )
    return record


def write_record(root: Path, record: dict[str, Any]) -> Path:
    path = root / attestation_relpath(record["ecosystem"], record["name"], record["version"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def variant(record: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Deep copy with dotted-path changes, e.g. ``variant(r, **{"decision.outcome": "fail"})``."""
    out = copy.deepcopy(record)
    for dotted, value in changes.items():
        target = out
        *parents, leaf = dotted.split(".")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return out


def write_ollama_manifest(models_dir: Path, model: str, tag: str, digest: str) -> Path:
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / model / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": f"sha256:{digest}",
                    },
                    {
                        "mediaType": "application/vnd.ollama.image.template",
                        "digest": "sha256:" + "b" * 64,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest
