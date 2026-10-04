"""CI dependency gate for D-29 (no network).

Reads the declared dependency set -- ``[project].dependencies`` in
``pyproject.toml`` and ``apps/backend/requirements.txt`` -- plus the transitive
packages recorded in ``provenance/origins.json``, and fails when:

* a package has no origin record in ``provenance/origins.json``;
* a package from a P28-listed origin is not pinned to one exact version, or has no
  passing attestation (principal-signed, see :mod:`.records`) for that version;
* a package of unknown origin is not in ``provenance/unknown_origin_allowlist.json``
  with a reason and a reviewer.

Allowlist entries still awaiting the principal's sign-off are reported as
warnings; ``--strict`` turns them into failures. Run it as
``python -m locus_tooling.provenance.gate`` or ``lattix provenance gate``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .records import (
    P28_LISTED_COUNTRIES,
    REVIEWER_AGENT_PENDING,
    REVIEWER_PRINCIPAL,
    lookup,
    normalize_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?\s*(?P<spec>[^;]*?)\s*(?:;(?P<marker>.*))?$"
)
_EXACT = re.compile(r"^===?\s*(?P<version>[A-Za-z0-9][A-Za-z0-9.+!_-]*)$")
ORIGIN_STATUSES = frozenset({"listed", "not-listed", "unknown"})
_REVIEWERS = frozenset({REVIEWER_PRINCIPAL, REVIEWER_AGENT_PENDING})


@dataclass(frozen=True)
class Requirement:
    name: str
    spec: str
    source: str
    marker: str = ""

    @property
    def key(self) -> str:
        return normalize_name("pypi", self.name)

    @property
    def exact_version(self) -> str:
        match = _EXACT.match(self.spec.strip())
        return match.group("version") if match else ""


def parse_requirement(line: str, source: str) -> Requirement | None:
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-"):
        return None
    match = _REQUIREMENT.match(text)
    if match is None:
        raise ValueError(f"{source}: cannot parse requirement {line!r}")
    if "@" in match.group("spec") or "://" in text:
        raise ValueError(f"{source}: direct references are not supported by the gate: {line!r}")
    return Requirement(
        name=match.group("name"),
        spec=match.group("spec").replace(" ", ""),
        source=source,
        marker=(match.group("marker") or "").strip(),
    )


def read_pyproject(path: Path) -> list[Requirement]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    out = [parse_requirement(str(d), path.name) for d in deps]
    return [r for r in out if r is not None]


def read_requirements(path: Path) -> list[Requirement]:
    out = [
        parse_requirement(line, path.as_posix())
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    return [r for r in out if r is not None]


@dataclass
class Result:
    package: str
    version: str
    origin: str
    ok: bool
    reason: str
    warning: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(r.ok for r in self.results)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.origin] = counts.get(result.origin, 0) + 1
        return counts

    def render(self) -> str:
        lines = ["D-29 dependency provenance gate"]
        for result in sorted(self.results, key=lambda r: (r.ok, r.package)):
            mark = "ok  " if result.ok else "FAIL"
            line = f"  {mark} {result.package} {result.version or '(unpinned)'} [{result.origin}] {result.reason}"
            lines.append(line)
            if result.warning:
                lines.append(f"       warning: {result.warning}")
        lines += [f"  ERROR {e}" for e in self.errors]
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(self.counts().items()))
        lines.append(f"{len(self.results)} packages ({summary}); {'PASS' if self.ok else 'FAIL'}")
        return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def validate_origins(origins: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    packages = origins.get("packages")
    if not isinstance(packages, dict):
        return ["origins.json: 'packages' must be an object"]
    for key, entry in packages.items():
        where = f"origins.json packages.{key}"
        if key != normalize_name("pypi", key):
            errors.append(f"{where}: key must be the normalized (PEP 503) name")
        if not isinstance(entry, dict):
            errors.append(f"{where}: must be an object")
            continue
        status = entry.get("p28_status")
        if status not in ORIGIN_STATUSES:
            errors.append(f"{where}: p28_status must be one of {sorted(ORIGIN_STATUSES)}")
        countries = set(entry.get("countries") or [])
        if countries & P28_LISTED_COUNTRIES and status != "listed":
            errors.append(f"{where}: names a P28-listed country but is not 'listed'")
        if status != "unknown" and not entry.get("evidence"):
            errors.append(f"{where}: a known origin needs evidence links")
        if not str(entry.get("basis") or "").strip():
            errors.append(f"{where}: 'basis' (how the origin was established) is required")
        if entry.get("scope") not in {"direct", "transitive"}:
            errors.append(f"{where}: scope must be 'direct' or 'transitive'")
        if entry.get("scope") == "transitive" and not str(entry.get("version") or "").strip():
            errors.append(f"{where}: a transitive entry needs the resolved 'version'")
    return errors


def _allowlisted(allowlist: Mapping[str, Any], key: str, version: str) -> tuple[bool, str, str]:
    """``(allowed, reason, warning)`` for an unknown-origin package."""
    for entry in allowlist.get("entries") or []:
        if not isinstance(entry, dict) or normalize_name("pypi", str(entry.get("name", ""))) != key:
            continue
        pinned = str(entry.get("version") or "*")
        if pinned != "*" and pinned != version:
            return False, f"allowlisted only at {pinned}", ""
        reason = str(entry.get("reason") or "").strip()
        reviewer = str(entry.get("reviewed_by") or "").strip()
        if not reason or reviewer not in _REVIEWERS or not str(entry.get("date") or "").strip():
            return False, "allowlist entry lacks a reason, a reviewer or a date", ""
        warning = (
            "allowlist entry awaits principal sign-off" if reviewer != REVIEWER_PRINCIPAL else ""
        )
        return True, f"allowlisted: {reason[:80]}", warning
    return False, "unknown origin and not in provenance/unknown_origin_allowlist.json", ""


def evaluate(
    requirements: Sequence[Requirement],
    origins: Mapping[str, Any],
    allowlist: Mapping[str, Any],
    *,
    attestation_roots: Iterable[str | Path],
    strict: bool = False,
) -> Report:
    report = Report(errors=validate_origins(origins))
    packages: Mapping[str, Any] = origins.get("packages") or {}
    roots = list(attestation_roots)
    # One spec per package: pyproject and requirements.txt must agree.
    declared: dict[str, Requirement] = {}
    for requirement in requirements:
        previous = declared.get(requirement.key)
        if previous is not None and previous.spec != requirement.spec:
            report.errors.append(
                f"{requirement.name}: '{previous.spec}' in {previous.source} but "
                f"'{requirement.spec}' in {requirement.source}"
            )
        declared.setdefault(requirement.key, requirement)
    targets: dict[str, tuple[str, str]] = {
        key: (r.exact_version, r.spec) for key, r in declared.items()
    }
    for key, entry in packages.items():
        if isinstance(entry, dict) and entry.get("scope") == "transitive" and key not in targets:
            version = str(entry.get("version") or "")
            targets[key] = (version, f"=={version}")
    for key, (version, spec) in sorted(targets.items()):
        entry = packages.get(key)
        if not isinstance(entry, dict):
            report.results.append(
                Result(
                    key,
                    version or spec,
                    "missing",
                    False,
                    "no origin record in provenance/origins.json",
                )
            )
            continue
        status = str(entry.get("p28_status"))
        if set(entry.get("countries") or []) & P28_LISTED_COUNTRIES:
            status = "listed"
        if status == "listed":
            if not version:
                report.results.append(
                    Result(
                        key,
                        spec,
                        status,
                        False,
                        "P28-listed origin must be pinned to one exact version (==)",
                    )
                )
                continue
            verdict, _ = lookup("pypi", key, version, roots=roots)
            report.results.append(
                Result(
                    key,
                    version,
                    status,
                    verdict.passing,
                    "passing attestation" if verdict.passing else verdict.reason,
                )
            )
        elif status == "unknown":
            allowed, reason, warning = _allowlisted(allowlist, key, version or "*")
            if allowed and warning and strict:
                allowed, reason = False, f"{reason} ({warning}; --strict)"
            report.results.append(Result(key, version or spec, status, allowed, reason, warning))
        else:
            report.results.append(
                Result(key, version or spec, status, True, str(entry.get("basis", ""))[:90])
            )
    return report


def run(
    *,
    root: Path = REPO_ROOT,
    pyproject: Path | None = None,
    requirements: Path | None = None,
    origins: Path | None = None,
    allowlist: Path | None = None,
    attestation_roots: Sequence[Path] | None = None,
    strict: bool = False,
) -> Report:
    reqs = read_pyproject(pyproject or root / "pyproject.toml")
    req_file = requirements or root / "apps" / "backend" / "requirements.txt"
    if req_file.is_file():
        reqs += read_requirements(req_file)
    origin_data = _load_json(origins or root / "provenance" / "origins.json")
    allow_data = _load_json(allowlist or root / "provenance" / "unknown_origin_allowlist.json")
    return evaluate(
        reqs,
        origin_data,
        allow_data,
        attestation_roots=attestation_roots or [root / "provenance"],
        strict=strict,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m locus_tooling.provenance.gate", description=__doc__
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--strict", action="store_true", help="fail on allowlist entries awaiting sign-off"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = run(root=args.root, strict=args.strict)
    sys.stdout.write(report.render() + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
