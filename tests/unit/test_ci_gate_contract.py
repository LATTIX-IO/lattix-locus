"""Regression/security tests for CI gate enforcement and attestation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from locus_tooling.ci_gate_contract import GateContractError, main, validate_ci_gates

NAMES = (
    "release-version", "frontend-unit", "backend-unit", "integration",
    "quality-policy-helm", "dast", "performance", "windows-sandbox",
    "sast-codeql", "lattix-cicd-pentest", "sbom-sca",
)
SECURITY = ("sast-codeql", "lattix-cicd-pentest", "sbom-sca")


def jobs():
    return {name: {"result": "success"} for name in NAMES}


def switches(**updates):
    data = {
        "CODEQL_ENABLED": "true",
        "LATTIX_PENTEST_ENABLED": "true",
        "LATTIX_SECURITY_RELEASE_REQUIRED": "false",
    }
    data.update(updates)
    return data


def test_all_executed_security_jobs_pass():
    assert validate_ci_gates(jobs(), env=switches()).not_executed == ()


def test_disabled_optional_scans_report_not_executed_not_success():
    values = jobs()
    for name in SECURITY:
        values[name]["result"] = "skipped"
    decision = validate_ci_gates(
        values,
        env=switches(CODEQL_ENABLED="false", LATTIX_PENTEST_ENABLED="false"),
    )
    assert set(decision.not_executed) == set(SECURITY)


@pytest.mark.parametrize("name", SECURITY)
def test_skipped_enabled_security_gate_is_never_pass(name):
    values = jobs()
    values[name]["result"] = "skipped"
    with pytest.raises(GateContractError, match=name):
        validate_ci_gates(values, env=switches())


@pytest.mark.parametrize("name", SECURITY)
def test_strict_release_denies_disabled_security_scan(name):
    values = jobs()
    values[name]["result"] = "skipped"
    with pytest.raises(GateContractError, match=name):
        validate_ci_gates(
            values,
            env=switches(
                CODEQL_ENABLED="false",
                LATTIX_PENTEST_ENABLED="false",
                LATTIX_SECURITY_RELEASE_REQUIRED="true",
            ),
        )


@pytest.mark.parametrize("status", ["skipped", "failure", "cancelled", "pending", "neutral"])
def test_ordinary_required_job_never_accepts_non_success(status):
    values = jobs()
    values["backend-unit"]["result"] = status
    with pytest.raises(GateContractError, match="backend-unit"):
        validate_ci_gates(values, env=switches())


def test_missing_and_unexpected_ci_jobs_fail_closed():
    values = jobs()
    del values["integration"]
    with pytest.raises(GateContractError, match="needs contract"):
        validate_ci_gates(values, env=switches())
    values = jobs()
    values["dummy"] = {"result": "success"}
    with pytest.raises(GateContractError, match="needs contract"):
        validate_ci_gates(values, env=switches())


@pytest.mark.parametrize("bad", ["enable", "none", "invalid"])
def test_malformed_security_toggle_never_defaults_to_disabled(bad):
    with pytest.raises(GateContractError, match="invalid CI security toggle"):
        validate_ci_gates(jobs(), env=switches(CODEQL_ENABLED=bad))


def test_blank_security_toggles_are_explicit_not_executed():
    values = jobs()
    for name in SECURITY:
        values[name]["result"] = "skipped"
    decision = validate_ci_gates(values, env={})
    assert set(decision.not_executed) == set(SECURITY)


def test_gate_workflow_prevents_duplicate_feature_branch_push_and_pr_runs():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "push:\n    branches: [main]\n  pull_request:" in workflow
    assert "python -m locus_tooling.ci_gate_contract" in workflow


def test_realistic_serialization_has_same_result():
    sample = json.loads(json.dumps(jobs()))
    assert validate_ci_gates(sample, env=switches()).not_executed == ()



def test_entrypoint_marks_optional_disabled_scans_unexecuted(
    monkeypatch, capsys,
):
    values = jobs()
    for name in SECURITY:
        values[name]["result"] = "skipped"
    monkeypatch.setenv("RESULTS", json.dumps(values))
    monkeypatch.setenv("CODEQL_ENABLED", "false")
    monkeypatch.setenv("LATTIX_PENTEST_ENABLED", "false")
    monkeypatch.setenv("LATTIX_SECURITY_RELEASE_REQUIRED", "false")
    assert main() == 0
    output = capsys.readouterr().out
    assert "NOT EXECUTED" in output
    assert "NOT ATTESTED" in output


def test_entrypoint_fails_strict_release_when_scanners_skipped(
    monkeypatch, capsys,
):
    values = jobs()
    values["sast-codeql"]["result"] = "skipped"
    monkeypatch.setenv("RESULTS", json.dumps(values))
    monkeypatch.setenv("CODEQL_ENABLED", "false")
    monkeypatch.setenv("LATTIX_PENTEST_ENABLED", "true")
    monkeypatch.setenv("LATTIX_SECURITY_RELEASE_REQUIRED", "true")
    assert main() == 1
    assert "DENIED" in capsys.readouterr().err


@pytest.mark.parametrize("payload", ["not json", "[]", "null"])
def test_entrypoint_fails_closed_on_invalid_ci_payload(
    monkeypatch, payload,
):
    monkeypatch.setenv("RESULTS", payload)
    assert main() == 1
