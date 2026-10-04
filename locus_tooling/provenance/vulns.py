"""Known-vulnerability lookup for one package version via the OSV API (osv.dev).

The same database osv-scanner and pip-audit use. Answers are cached on disk
(``<cache>/osv/<ecosystem>/<name>@<version>.json``) so a re-inspection works
offline; with neither network nor cache the result is ``unavailable``, which an
attestation records and which keeps it from passing. The transport is injectable
so tests never touch the network.
"""

from __future__ import annotations

import datetime as _dt
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "go": "Go"}

Transport = Callable[[str, bytes], bytes]


def _http_post(url: str, body: bytes) -> bytes:
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URL
        data: bytes = response.read()
        return data


@dataclass
class VulnResult:
    mode: str  # online | cache | unavailable
    status: str  # none-known | found | unavailable
    results: list[dict[str, Any]] = field(default_factory=list)
    date: str = field(default_factory=lambda: _dt.date.today().isoformat())

    def as_record(self) -> dict[str, Any]:
        return {
            "source": f"OSV API ({OSV_QUERY_URL})",
            "mode": self.mode,
            "date": self.date,
            "status": self.status,
            "results": self.results,
        }


def _summarize(vuln: dict[str, Any]) -> dict[str, Any]:
    fixed: list[str] = []
    for affected in vuln.get("affected") or []:
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if "fixed" in event:
                    fixed.append(str(event["fixed"]))
    entry: dict[str, Any] = {
        "id": str(vuln.get("id", "")),
        "summary": str(vuln.get("summary") or vuln.get("details", ""))[:300],
        "aliases": [str(a) for a in vuln.get("aliases") or []],
        "fixed_in": sorted(set(fixed)),
    }
    severity = (
        vuln.get("database_specific", {}).get("severity")
        if isinstance(vuln.get("database_specific"), dict)
        else None
    )
    if severity:
        entry["severity"] = str(severity)
    return entry


def lookup(
    ecosystem: str,
    name: str,
    version: str,
    *,
    cache_dir: Path | None = None,
    transport: Transport | None = None,
    offline: bool = False,
) -> VulnResult:
    osv_ecosystem = OSV_ECOSYSTEMS.get(ecosystem.lower())
    if osv_ecosystem is None:
        return VulnResult(mode="unavailable", status="unavailable")
    cache_file = (
        cache_dir / "osv" / ecosystem.lower() / f"{name.lower()}@{version}.json"
        if cache_dir
        else None
    )
    payload: dict[str, Any] | None = None
    mode = "online"
    if not offline:
        body = json.dumps(
            {"package": {"name": name, "ecosystem": osv_ecosystem}, "version": version}
        ).encode("utf-8")
        try:
            raw = (transport or _http_post)(OSV_QUERY_URL, body)
            loaded = json.loads(raw.decode("utf-8") or "{}")
            payload = loaded if isinstance(loaded, dict) else None
        except (urllib.error.URLError, OSError, ValueError):
            payload = None
        if payload is not None and cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    if payload is None and cache_file is not None and cache_file.is_file():
        try:
            loaded = json.loads(cache_file.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else None
            mode = "cache"
        except (OSError, ValueError):
            payload = None
    if payload is None:
        return VulnResult(mode="unavailable", status="unavailable")
    results = [_summarize(v) for v in payload.get("vulns") or [] if isinstance(v, dict)]
    return VulnResult(mode=mode, status="found" if results else "none-known", results=results)
