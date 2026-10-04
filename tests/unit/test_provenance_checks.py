"""LOCUS-358 / D-29: OSV lookup, SBOM fallback, entity screening, decisions, CLI."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from locus_tooling.provenance import cli, inspection, sbom, screening, vulns
from tests.provenance_support import package_record

OSV_ANSWER = {
    "vulns": [
        {
            "id": "GHSA-xxxx",
            "summary": "decoder recursion",
            "aliases": ["CVE-2099-0001"],
            "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.0.1"}]}]}],
        }
    ]
}


def test_osv_lookup_online_then_cached_then_unavailable(tmp_path: Path) -> None:
    calls: list[bytes] = []

    def transport(url: str, body: bytes) -> bytes:
        calls.append(body)
        return json.dumps(OSV_ANSWER).encode()

    online = vulns.lookup("pypi", "examplepkg", "1.0.0", cache_dir=tmp_path, transport=transport)
    assert online.mode == "online" and online.status == "found"
    assert online.results[0]["fixed_in"] == ["1.0.1"] and online.results[0]["aliases"] == [
        "CVE-2099-0001"
    ]
    assert json.loads(calls[0]) == {
        "package": {"name": "examplepkg", "ecosystem": "PyPI"},
        "version": "1.0.0",
    }

    def offline(url: str, body: bytes) -> bytes:
        raise OSError("no network")

    cached = vulns.lookup("pypi", "examplepkg", "1.0.0", cache_dir=tmp_path, transport=offline)
    assert cached.mode == "cache" and cached.status == "found"
    missing = vulns.lookup("pypi", "other", "1.0.0", cache_dir=tmp_path, transport=offline)
    assert missing.mode == "unavailable" and missing.status == "unavailable"
    clean = vulns.lookup("pypi", "clean", "2.0", transport=lambda u, b: b"{}")
    assert clean.status == "none-known"


def test_sbom_fallback_reads_dist_info_and_hashes_native_files(tmp_path: Path) -> None:
    dist = tmp_path / "examplepkg-1.0.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: examplepkg\nVersion: 1.0.0\nLicense: MIT\nRequires-Dist: other>=1\n",
        encoding="utf-8",
    )
    (tmp_path / "examplepkg").mkdir()
    (tmp_path / "examplepkg" / "_ext.so").write_bytes(b"\x7fELF")
    result = sbom.fallback_sbom(
        tmp_path, name="examplepkg", version="1.0.0", artifact_sha256="a" * 64
    )
    assert result.generator_kind == "locus-fallback" and result.components == 2
    library, native = result.document["components"]
    assert library["purl"] == "pkg:pypi/examplepkg@1.0.0"
    assert {"name": "locus:requires-dist", "value": "other>=1"} in library["properties"]
    assert native["name"] == "examplepkg/_ext.so" and native["hashes"][0]["alg"] == "SHA-256"
    digest = sbom.write(result, tmp_path / "out" / "sbom.json")
    assert len(digest) == 64 and sbom.write(result, tmp_path / "out" / "again.json") == digest


def test_pick_dynamic_wheel() -> None:
    names = ["p-1-py3-none-win_amd64.whl", "p-1-py3-none-manylinux_2_17_x86_64.whl", "p-1.tar.gz"]
    assert inspection.pick_dynamic_wheel(names, ["win_amd64"]) == names[0]
    assert inspection.pick_dynamic_wheel(names, ["linux", "x86_64"]) == names[1]
    assert inspection.pick_dynamic_wheel(names, ["macosx", "arm64"]) == ""
    assert inspection.pick_dynamic_wheel(["q-1-py3-none-any.whl", *names]) == "q-1-py3-none-any.whl"


def _csl(path: Path) -> Path:
    rows = [
        {
            "source": screening.ENTITY_LIST_SOURCE,
            "name": "Shady Robotics Co., Ltd.",
            "alt_names": "SR Co",
            "addresses": "Beijing, CN",
        },
        {
            "source": "Specially Designated Nationals (SDN) - Treasury Department",
            "name": "MATA GARCIA, Americo Alex",
            "alt_names": "",
            "addresses": "Miranda, VE",
        },
        {
            "source": "Specially Designated Nationals (SDN) - Treasury Department",
            "name": "GIL GARCIA, Jose Alejandro",
            "alt_names": "",
            "addresses": "MX",
        },
        {
            "source": "Specially Designated Nationals (SDN) - Treasury Department",
            "name": "ZAMBADA GARCIA, Ismael",
            "alt_names": "",
            "addresses": "MX",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "name", "alt_names", "addresses"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_entity_list_screening(tmp_path: Path) -> None:
    csl = _csl(tmp_path / "csl.csv")
    queries = [screening.Query("Alex Garcia", "person"), screening.Query("Shady Robotics", "org")]
    el = screening.entity_list_check(csl, queries).as_record()
    assert el["list"] == "us-commerce-entity-list" and el["result"] == "possible-match"
    assert [m["listed_name"] for m in el["matches"]] == ["Shady Robotics Co., Ltd."]
    other = screening.consolidated_list_check(csl, queries).as_record()
    hits = {(m["listed_name"], m["strength"]) for m in other["matches"]}
    assert ("MATA GARCIA, Americo Alex", "name") in hits
    assert ("GIL GARCIA, Jose Alejandro", "variant") in hits
    assert not any("ZAMBADA" in name for name, _ in hits), "a shared surname alone is not a hit"
    clean = screening.entity_list_check(
        csl, [screening.Query("Christian Heimes", "person")]
    ).as_record()
    assert clean["result"] == "no-match" and len(clean["source_sha256"]) == 64


def test_1260h_screening_and_review_resolution(tmp_path: Path) -> None:
    notice = tmp_path / "1260h.xml"
    notice.write_text(
        "<P>Alibaba Group Holding Limited (Alibaba)</P><P>Example Corp</P>", encoding="utf-8"
    )
    check = screening.dod_1260h_check(
        notice,
        [screening.Query("Alibaba", "org"), screening.Query("Red Hat", "org")],
        source_url="https://example.test/n",
        notice="FR 1",
    ).as_record()
    assert check["result"] == "possible-match" and check["matches"][0]["query"] == "Alibaba"
    confirmed = inspection.resolve_entity_reviews(
        dict(check),
        [
            {
                "list": "dod-1260h",
                "query": "Alibaba",
                "listed_name": "alibaba",
                "resolution": "same-entity",
                "reason": "listed",
            }
        ],
    )
    assert confirmed["result"] == "match"
    unreviewed = inspection.resolve_entity_reviews(dict(check), [])
    assert unreviewed["result"] == "possible-match"


def test_proposed_decisions() -> None:
    record = package_record(outcome="pass")
    assert inspection.propose_decision(record, None)["outcome"] == "pass"
    failing = {**record, "dynamic_egress": {**record["dynamic_egress"], "status": "fail"}}
    assert inspection.propose_decision(failing, None)["outcome"] == "fail"
    finding = {
        "rule": "r",
        "severity": "review",
        "path": "p",
        "detail": "d",
        "disposition": "needs-review",
    }
    review = {**record, "static_findings": {**record["static_findings"], "findings": [finding]}}
    proposed = inspection.propose_decision(review, None)
    assert proposed["outcome"] == "conditional" and "needs-review" in proposed["conditions"][0]


def test_cli_verify_accepts_committed_attestations_and_rejects_a_broken_one(tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(cli.provenance, ["verify"])
    assert ok.exit_code == 0, ok.output
    # The committed LOCUS-358 attestations carry the principal's sign-off (2026-10-04).
    assert ": passing" in ok.output
    broken = tmp_path / "broken@1.json"
    broken.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")
    bad = runner.invoke(cli.provenance, ["verify", str(broken)])
    assert bad.exit_code == 1 and "INVALID" in bad.output


@pytest.mark.parametrize("requirement", ["pyasn1", "pyasn1>=0.6", "==0.6.4"])
def test_cli_inspect_requires_an_exact_version(requirement: str, tmp_path: Path) -> None:
    research = tmp_path / "r.json"
    research.write_text(
        json.dumps({"origin": {}, "maintainers": [], "funding": {}}), encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli.provenance, ["inspect", "pypi", requirement, "--research", str(research)]
    )
    assert result.exit_code != 0 and "NAME==VERSION" in result.output
