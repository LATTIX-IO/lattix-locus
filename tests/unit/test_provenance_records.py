"""LOCUS-358 / D-29: attestation schema, consistency rules and the passing verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

from locus_tooling.provenance import records, schema_lite
from tests.provenance_support import model_record, package_record, variant, write_record

ATTESTATIONS = sorted((records.REPO_PROVENANCE_DIR / "attestations").rglob("*.json"))


def test_schema_uses_only_supported_keywords() -> None:
    schema_lite.check_schema(records.attestation_schema())


def test_schema_is_valid_draft_2020_12_and_agrees_with_reference_validator() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = records.attestation_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    good = package_record()
    bad = variant(
        good,
        **{
            "review.reviewer": "someone",
            "artifacts": [{"filename": "x", "sha256": "nope", "inspected": True}],
        },
    )
    assert not list(validator.iter_errors(good)) and not records.schema_errors(good)
    assert list(validator.iter_errors(bad)) and records.schema_errors(bad)


def test_unsupported_schema_keyword_is_rejected() -> None:
    with pytest.raises(schema_lite.SchemaError):
        schema_lite.validate({}, {"type": "object", "oneOf": []})


@pytest.mark.parametrize("path", ATTESTATIONS, ids=lambda p: p.name)
def test_committed_attestations_validate_and_summaries_are_current(path: Path) -> None:
    record = records.load_record(path)
    assert records.schema_errors(record) == []
    assert records.consistency_errors(record) == []
    assert path.with_suffix(".md").read_text(encoding="utf-8") == records.render_markdown(record)
    assert path.relative_to(records.REPO_PROVENANCE_DIR) == records.attestation_relpath(
        record["ecosystem"], record["name"], record["version"]
    )


def test_committed_attestations_are_pending_or_carry_a_principal_sign_off() -> None:
    # Agents only ever write "agent-prepared, principal sign-off pending". A record
    # marked "principal" must name the signer and carry the sign-off note, and it
    # must then pass; a pending record must not pass.
    assert ATTESTATIONS, "the LOCUS-358 inspections must be committed"
    for path in ATTESTATIONS:
        record = records.load_record(path)
        review = record["review"]
        if review["reviewer"] == records.REVIEWER_PRINCIPAL:
            assert review.get("name"), path
            assert "principal" in (review.get("notes") or "").lower(), path
            assert records.evaluate(record).passing, (path, records.evaluate(record).reason)
        else:
            assert review["reviewer"] == records.REVIEWER_AGENT_PENDING, path
            assert not records.evaluate(record).passing, path


def test_signed_passing_record_passes() -> None:
    verdict = records.evaluate(package_record())
    assert verdict.passing, verdict.reason


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"review.reviewer": records.REVIEWER_AGENT_PENDING}, "sign-off pending"),
        ({"decision.outcome": "fail"}, "decision is 'fail'"),
        ({"dynamic_egress.status": "fail"}, "connection attempts"),
        ({"dynamic_egress.status": "not-run"}, "use 'conditional'"),
        ({"dynamic_egress.jail_probe": "connected"}, "positive control"),
        ({"decision.outcome": "conditional"}, "requires at least one condition"),
        ({"origin.p28_status": "not-listed"}, "P28-listed country"),
        ({"vulnerabilities.status": "unavailable"}, "unavailable"),
        ({"vulnerabilities.status": "not-applicable"}, "only for model weights"),
    ],
)
def test_failed_or_unsigned_parts_keep_a_record_from_passing(
    changes: dict[str, object], reason: str
) -> None:
    verdict = records.evaluate(variant(package_record(), **changes))
    assert not verdict.passing
    assert reason in verdict.reason


def test_findings_and_entity_hits_need_resolution() -> None:
    record = variant(
        package_record(), **{"decision.outcome": "conditional", "decision.conditions": ["c"]}
    )
    finding = {
        "rule": "native-extension",
        "severity": "review",
        "path": "x.so",
        "detail": "d",
        "disposition": "needs-review",
    }
    pending = variant(record, **{"static_findings.findings": [finding]})
    assert records.consistency_errors(pending) == []
    assert "open: static findings needing review" in records.evaluate(pending).reason
    unconditional = variant(pending, **{"decision.outcome": "pass"})
    assert "use 'conditional'" in records.evaluate(unconditional).reason
    blocking = variant(
        record, **{"static_findings.findings": [{**finding, "disposition": "blocking"}]}
    )
    assert "blocking static finding" in records.evaluate(blocking).reason
    checks = record["entity_list_checks"]
    possible = variant(
        record, entity_list_checks=[{**checks[0], "result": "possible-match"}, checks[1]]
    )
    assert "possible matches unresolved" in records.evaluate(possible).reason
    match = variant(record, entity_list_checks=[{**checks[0], "result": "match"}, checks[1]])
    assert "entity-list match" in records.evaluate(match).reason
    missing = variant(record, entity_list_checks=[checks[0], checks[0]])
    assert "1260H" in records.evaluate(missing).reason
    not_run = variant(record, entity_list_checks=[{**checks[0], "result": "not-run"}, checks[1]])
    assert "open: an entity-list check not run" in records.evaluate(not_run).reason


def test_model_record_needs_a_passing_behavioural_eval() -> None:
    assert records.evaluate(model_record()).passing
    pending = records.evaluate(model_record(eval_status="pending"))
    assert not pending.passing and "LOCUS-351" in pending.reason
    refused = variant(model_record(), **{"model.format_check.status": "fail"})
    assert "weights-only" in records.evaluate(refused).reason


@pytest.mark.parametrize(
    ("ecosystem", "name", "version", "expected"),
    [
        ("pypi", "PyASN1_Modules", "0.4.2", "attestations/pypi/pyasn1-modules@0.4.2.json"),
        ("ollama", "user/Model", "7b", "attestations/ollama/user__model@7b.json"),
    ],
)
def test_attestation_paths_are_normalized(
    ecosystem: str, name: str, version: str, expected: str
) -> None:
    assert records.attestation_relpath(ecosystem, name, version).as_posix() == expected


@pytest.mark.parametrize("bad", ["../evil", "a/../../b", ".hidden", ""])
def test_attestation_paths_refuse_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        records.attestation_relpath("pypi", "pkg", bad)


def test_lookup_requires_the_exact_version_and_name(tmp_path: Path) -> None:
    write_record(tmp_path, package_record(version="1.0.0"))
    assert records.lookup("pypi", "ExamplePkg", "1.0.0", roots=[tmp_path])[0].passing
    other, _ = records.lookup("pypi", "examplepkg", "1.0.1", roots=[tmp_path])
    assert not other.passing and "no attestation" in other.reason
    path = write_record(tmp_path, package_record(version="2.0.0"))
    path.write_text(path.read_text().replace('"version": "2.0.0"', '"version": "9.9.9"'))
    mismatch, _ = records.lookup("pypi", "examplepkg", "2.0.0", roots=[tmp_path])
    assert not mismatch.passing and "does not describe" in mismatch.reason
