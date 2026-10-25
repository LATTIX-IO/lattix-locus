"""LOCUS-477: do not synthesize provider/auth evidence for arbitrary errors."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime_failure_diagnostics import (
    describe_unexpected_failure,
    historical_failure_report,
    make_failure_report,
)


def _raise_error(error: Exception) -> None:
    raise error


def test_structural_report_excludes_sensitive_exception_arguments() -> None:
    secret = "sk-test-DO-NOT-PRINT-123"
    try:
        _raise_error(RuntimeError(f"provider crashed at path /secret: {secret}"))
    except RuntimeError as exc:
        detail = describe_unexpected_failure(exc, run_id="run-123")
    body = make_failure_report(detail)
    assert detail["exception_type"] == "RuntimeError"
    assert detail["code"] == "execution_unhandled_exception"
    assert detail["message"] == "Runtime execution failed."
    assert "test_runtime_failure_diagnostics.py:_raise_error:" in detail["failure_site"]
    assert secret not in body
    assert "/secret" not in body
    assert "sk-test" not in body
    assert json.loads(body)["schema_version"] == "locus/runtime-failure/v1"


def test_typed_exception_category_is_finite_and_no_raw_message() -> None:
    cases = [
        (TimeoutError("secret-token-xyz"), "execution_timeout"),
        (PermissionError("secret-token-xyz"), "execution_permission_denied"),
        (ConnectionError("secret-token-xyz"), "execution_dependency_unreachable"),
        (ValueError("secret-token-xyz"), "execution_unhandled_exception"),
    ]
    for exc, code in cases:
        result = describe_unexpected_failure(exc, run_id="run-456")
        report = make_failure_report(result)
        assert result["code"] == code
        assert "secret-token-xyz" not in report


def test_historical_run_no_longer_claims_provider_configuration_failure() -> None:
    result = json.loads(historical_failure_report(run_id="old-run"))
    assert result["error"]["code"] == "historical_failure_details_unavailable"
    assert "provider/auth cause has not been established" in result["error"]["message"]


def test_report_has_no_extra_untrusted_fields() -> None:
    report = make_failure_report({
        "code": "execution_unhandled_exception",
        "message": "Runtime execution failed.",
        "run_id": "run-789",
        "secret": "password123",
        "exception_args": "sk-test-do-not-leak",
    })
    assert "password123" not in report
    assert "sk-test" not in report
    assert json.loads(report)["error"]["code"] == "execution_unhandled_exception"
