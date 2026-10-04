"""LOCUS-358 / D-29: the CI dependency gate (pass and fail fixtures, and the real repo)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from locus_tooling.provenance import gate
from locus_tooling.provenance.records import REVIEWER_AGENT_PENDING, REVIEWER_PRINCIPAL
from tests.provenance_support import package_record, write_record


def _origin(status: str, countries: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ecosystem": "pypi",
        "scope": "direct",
        "p28_status": status,
        "countries": countries
        if countries is not None
        else ([] if status == "unknown" else ["US"]),
        "basis": "test",
        "confidence": "medium",
        "evidence": [] if status == "unknown" else ["https://example.test/"],
    }
    entry.update(extra)
    return entry


def _repo(
    tmp_path: Path,
    *,
    deps: list[str],
    requirements: list[str] | None = None,
    origins: dict[str, Any],
    allow: list[dict[str, Any]] | None = None,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'x'\nversion = '0'\ndependencies = " + json.dumps(deps) + "\n",
        encoding="utf-8",
    )
    if requirements is not None:
        req = tmp_path / "apps" / "backend" / "requirements.txt"
        req.parent.mkdir(parents=True)
        req.write_text("# comment\n" + "\n".join(requirements) + "\n", encoding="utf-8")
    prov = tmp_path / "provenance"
    prov.mkdir()
    (prov / "origins.json").write_text(json.dumps({"packages": origins}), encoding="utf-8")
    (prov / "unknown_origin_allowlist.json").write_text(
        json.dumps({"entries": allow or []}), encoding="utf-8"
    )
    return tmp_path


def _results(report: gate.Report) -> dict[str, gate.Result]:
    return {r.package: r for r in report.results}


def test_clean_origins_pass(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        deps=["click>=8.1.7", "PyJWT[crypto]>=2.10.1", "comtypes==1.4.17; sys_platform == 'win32'"],
        requirements=["click>=8.1.7", "comtypes==1.4.17; sys_platform == 'win32'"],
        origins={
            "click": _origin("not-listed"),
            "pyjwt": _origin("not-listed"),
            "comtypes": _origin("not-listed"),
        },
    )
    report = gate.run(root=root)
    assert report.ok, report.render()
    assert _results(report)["comtypes"].version == "1.4.17"


def test_missing_origin_record_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, deps=["newdep>=1.0"], origins={})
    report = gate.run(root=root)
    assert not report.ok
    assert "no origin record" in _results(report)["newdep"].reason


def test_listed_origin_needs_an_exact_pin(tmp_path: Path) -> None:
    root = _repo(
        tmp_path, deps=["examplepkg>=1.0"], origins={"examplepkg": _origin("listed", ["CN"])}
    )
    result = _results(gate.run(root=root))["examplepkg"]
    assert not result.ok and "exact version" in result.reason


@pytest.mark.parametrize(
    ("record_version", "reviewer", "ok", "reason"),
    [
        (None, None, False, "no attestation"),
        ("1.0.0", REVIEWER_AGENT_PENDING, False, "sign-off pending"),
        ("0.9.0", REVIEWER_PRINCIPAL, False, "no attestation"),
        ("1.0.0", REVIEWER_PRINCIPAL, True, "passing attestation"),
    ],
)
def test_listed_origin_needs_a_passing_attestation_for_that_exact_version(
    tmp_path: Path, record_version: str | None, reviewer: str | None, ok: bool, reason: str
) -> None:
    root = _repo(
        tmp_path, deps=["examplepkg==1.0.0"], origins={"examplepkg": _origin("listed", ["CN"])}
    )
    if record_version is not None:
        write_record(
            root / "provenance",
            package_record(version=record_version, signed=reviewer == REVIEWER_PRINCIPAL),
        )
    report = gate.run(root=root)
    result = _results(report)["examplepkg"]
    assert result.ok is ok and report.ok is ok
    assert reason in result.reason


def test_a_listed_country_overrides_a_wrong_status(tmp_path: Path) -> None:
    root = _repo(
        tmp_path, deps=["examplepkg==1.0.0"], origins={"examplepkg": _origin("not-listed", ["RU"])}
    )
    report = gate.run(root=root)
    assert not report.ok
    assert any("P28-listed country" in e for e in report.errors)
    assert _results(report)["examplepkg"].origin == "listed"


def test_unknown_origin_needs_an_allowlist_entry(tmp_path: Path) -> None:
    root = _repo(tmp_path, deps=["mystery>=1"], origins={"mystery": _origin("unknown")})
    assert "unknown_origin_allowlist" in _results(gate.run(root=root))["mystery"].reason


def test_allowlisted_unknown_passes_with_a_warning_and_fails_strict(tmp_path: Path) -> None:
    allow = [
        {
            "name": "Mystery",
            "version": "*",
            "reason": "reviewed",
            "reviewed_by": REVIEWER_AGENT_PENDING,
            "date": "2026-10-04",
        }
    ]
    root = _repo(
        tmp_path, deps=["mystery>=1"], origins={"mystery": _origin("unknown")}, allow=allow
    )
    report = gate.run(root=root)
    assert report.ok and "sign-off" in _results(report)["mystery"].warning
    assert not gate.run(root=root, strict=True).ok
    signed = [{**allow[0], "reviewed_by": REVIEWER_PRINCIPAL}]
    root2 = _repo(
        tmp_path / "signed",
        deps=["mystery>=1"],
        origins={"mystery": _origin("unknown")},
        allow=signed,
    )
    assert gate.run(root=root2, strict=True).ok


def test_allowlist_entry_without_reason_or_for_another_version_fails(tmp_path: Path) -> None:
    no_reason = [
        {"name": "mystery", "reason": "", "reviewed_by": REVIEWER_PRINCIPAL, "date": "2026-10-04"}
    ]
    root = _repo(
        tmp_path, deps=["mystery==2.0"], origins={"mystery": _origin("unknown")}, allow=no_reason
    )
    assert "lacks a reason" in _results(gate.run(root=root))["mystery"].reason
    other = [
        {
            "name": "mystery",
            "version": "1.0",
            "reason": "r",
            "reviewed_by": REVIEWER_PRINCIPAL,
            "date": "2026-10-04",
        }
    ]
    root2 = _repo(
        tmp_path / "v", deps=["mystery==2.0"], origins={"mystery": _origin("unknown")}, allow=other
    )
    assert "only at 1.0" in _results(gate.run(root=root2))["mystery"].reason


def test_transitive_entries_are_gated_at_their_recorded_version(tmp_path: Path) -> None:
    origins = {"examplepkg": _origin("listed", ["CN"], scope="transitive", version="1.0.0")}
    root = _repo(tmp_path, deps=[], origins=origins)
    assert not gate.run(root=root).ok
    write_record(root / "provenance", package_record(version="1.0.0"))
    assert gate.run(root=root).ok


def test_pyproject_and_requirements_must_agree(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        deps=["click>=8.1.7"],
        requirements=["click>=8.0"],
        origins={"click": _origin("not-listed")},
    )
    report = gate.run(root=root)
    assert not report.ok and any("click" in e for e in report.errors)


def test_main_exit_code(tmp_path: Path) -> None:
    root = _repo(tmp_path, deps=["newdep>=1.0"], origins={})
    assert gate.main(["--root", str(root)]) == 1


def test_the_repository_dependency_set_passes_the_gate() -> None:
    report = gate.run()
    assert report.ok, report.render()
    # Every currently declared dependency and every documented transitive one is seeded.
    assert {r.origin for r in report.results} <= {"not-listed", "unknown", "listed"}
    assert {"pyasn1", "pyasn1-modules", "sqlite-vec"} <= {r.package for r in report.results}
