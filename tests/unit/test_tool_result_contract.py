"""LOCUS-433 TDD: typed tool result allowlist and hostile-output regression."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from locus_runtime.tool_result_contract import (
    MAX_RESULT_BYTES, CounterResult, StatusResult,
    ToolResultPolicy, ToolResultRejected, decode_tool_result,
)


def test_status_success_has_canonical_digest_only():
    value = decode_tool_result(
        b'{"schema":"status/v1","ok":true,"code":0}',
        ToolResultPolicy("status/v1"),
    )
    assert isinstance(value, StatusResult)
    assert value.ok is True and value.code == 0
    assert len(value.digest) == 64
    assert "stdout" not in repr(value)
    reordered = decode_tool_result(
        b'{"code":0,"ok":true,"schema":"status/v1"}',
        ToolResultPolicy("status/v1"),
    )
    assert value.digest == reordered.digest


def test_status_failure_is_typed_and_code_is_nonzero():
    val = decode_tool_result(
        b'{"schema":"status/v1","ok":false,"code":17}',
        ToolResultPolicy("status/v1"),
    )
    assert val.ok is False and val.code == 17


def test_counter_requires_pinned_metric_and_preserves_numeric_value():
    policy = ToolResultPolicy("counter/v1", frozenset({"issues.closed"}))
    value = decode_tool_result(
        b'{"schema":"counter/v1","metric":"issues.closed","value":43}',
        policy,
    )
    assert isinstance(value, CounterResult)
    assert value.metric == "issues.closed" and value.value == 43
    assert value.schema == "counter/v1"
    other = decode_tool_result(
        b'{"value":44,"metric":"issues.closed","schema":"counter/v1"}',
        policy,
    )
    assert other.digest != value.digest


@pytest.mark.parametrize("raw", [
    b'{"schema":"status/v1","ok":true,"ok":false,"code":0}',
    b'{"schema":"status/v1","ok":true,"code":0,"unknown":"Bearer secret"}',
    b'{"schema":"status/v1","ok":true,"code":0,"nested":{"x":1,"x":2}}',
    b'{"schema":"status/v1","ok":true,"code":true}',
    b'{"schema":"status/v1","ok":"true","code":0}',
    b'{"schema":"status/v1","ok":false,"code":0}',
    b'{"schema":"status/v1","ok":true,"code":1}',
    b'{"schema":"status/v1","ok":true,"code":-1}',
    b'{"schema":"status/v1","ok":true,"code":256}',
    b'{"schema":"status/v1","ok":true,"code":0.0}',
    b'{"schema":"status/v1","ok":true,"code":NaN}',
    b'{"schema":"status/v1","ok":true,"code":Infinity}',
    b'{"schema":"status/v1","ok":true,"code":0}trailing',
    b'["status/v1",0]',
    b'{"schema":"counter/v1","metric":"issues.closed","value":1}',
    b'{"schema":"status/v1","ok":true,"code":0,"stderr":"password"}',
    b'\xff\xfe',
    b'',
    b'{"schema":"status/v1"',
])
def test_untrusted_status_strings_and_malformed_documents_fail_closed(raw):
    with pytest.raises(ToolResultRejected):
        decode_tool_result(raw, ToolResultPolicy("status/v1"))


@pytest.mark.parametrize("raw", [
    b'{"schema":"counter/v1","metric":"secret","value":1}',
    b'{"schema":"counter/v1","metric":"issues.closed","value":"1"}',
    b'{"schema":"counter/v1","metric":"issues.closed","value":false}',
    b'{"schema":"counter/v1","metric":"issues.closed","value":-1}',
    b'{"schema":"counter/v1","metric":"issues.closed","value":1000000000001}',
    b'{"schema":"counter/v1","metric":"issues.closed","value":3,"text":"PII"}',
    b'{"schema":"counter/v1","metric":"issues.closed","value":1.3}',
    b'{"schema":"status/v1","ok":true,"code":0}',
])
def test_counter_contract_never_releases_unregistered_content(raw):
    with pytest.raises(ToolResultRejected):
        decode_tool_result(
            raw, ToolResultPolicy("counter/v1", frozenset({"issues.closed"})),
        )


@pytest.mark.parametrize("schema,metrics,max_bytes", [
    ("unknown/v1", frozenset(), 1024),
    ("counter/v1", frozenset(), 1024),
    ("status/v1", frozenset({"issues.closed"}), 1024),
    ("counter/v1", {"issues.closed"}, 1024),
    ("counter/v1", frozenset({"Bad Name"}), 1024),
    ("counter/v1", frozenset({"a" * 49}), 1024),
    ("status/v1", frozenset(), 0),
    ("status/v1", frozenset(), MAX_RESULT_BYTES + 1),
    ("status/v1", frozenset(), True),
])
def test_invalid_policy_fails_before_parsing(schema, metrics, max_bytes):
    with pytest.raises(ValueError):
        ToolResultPolicy(schema, metrics, max_bytes)


def test_result_envelope_rejects_oversized_binary_and_wrong_input_type():
    policy = ToolResultPolicy("status/v1", max_bytes=64)
    with pytest.raises(ToolResultRejected):
        decode_tool_result(b" " * 65, policy)
    with pytest.raises(ToolResultRejected):
        decode_tool_result('{"schema":"status/v1","ok":true,"code":0}', policy)
    with pytest.raises(ToolResultRejected):
        decode_tool_result(bytearray(b"{}"), policy)


def test_fixed_schema_does_not_expose_input_text_in_exceptions():
    sensitive = b'{"schema":"status/v1","ok":true,"code":0,"secret":"Bearer abcdef"}'
    with pytest.raises(ToolResultRejected) as rejected:
        decode_tool_result(sensitive, ToolResultPolicy("status/v1"))
    assert "Bearer" not in str(rejected.value)
    assert "abcdef" not in str(rejected.value)


def test_policy_and_results_are_immutable():
    policy = ToolResultPolicy("counter/v1", frozenset({"runs.completed"}))
    with pytest.raises(FrozenInstanceError):
        policy.max_bytes = 1
    result = decode_tool_result(
        b'{"schema":"counter/v1","metric":"runs.completed","value":9}', policy,
    )
    with pytest.raises(FrozenInstanceError):
        result.metric = "sensitive"
    assert json.loads(
        json.dumps({"schema": result.schema, "metric": result.metric, "value": result.value})
    ) == {"schema": "counter/v1", "metric": "runs.completed", "value": 9}


def test_digest_domain_separated_by_schema():
    status = decode_tool_result(
        b'{"schema":"status/v1","ok":true,"code":0}',
        ToolResultPolicy("status/v1"),
    )
    counter = decode_tool_result(
        b'{"schema":"counter/v1","metric":"ok","value":0}',
        ToolResultPolicy("counter/v1", frozenset({"ok"})),
    )
    assert status.digest != counter.digest
