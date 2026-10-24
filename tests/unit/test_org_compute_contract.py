"""OCF-01 trust-boundary contract: untrusted data is never authority."""
from __future__ import annotations

import json

import pytest

from locus_runtime.org_compute_contract import (
    InvalidRemoteEnvelope, RemoteAdmissionUnavailable,
    admit_remote_execution, decode_untrusted_remote_envelope,
    parse_untrusted_remote_envelope,
)


def valid():
    return {
        "schema_version": "1", "org_id": "org:alpha",
        "sender_device_id": "device:a", "recipient_device_id": "device:b",
        "workspace_id": "workspace:one", "run_id": "run:one",
        "task_id": "task:one", "policy_epoch": 1, "expires_at": 2000000000,
        "nonce": "nonce:unique", "payload_sha256": "a" * 64,
    }


def test_well_formed_claims_are_not_implicitly_authorized():
    envelope = parse_untrusted_remote_envelope(valid())
    with pytest.raises(RemoteAdmissionUnavailable, match="not enabled"):
        admit_remote_execution(envelope)


@pytest.mark.parametrize("change", [
    {"org_id": "org:beta"},
    {"workspace_id": "workspace:private"},
    {"sender_device_id": "device:attacker"},
    {"recipient_device_id": "device:attacker"},
    {"policy_epoch": 2},
])
def test_well_formed_cross_boundary_claims_still_cannot_execute(change):
    wire = valid()
    wire.update(change)
    with pytest.raises(RemoteAdmissionUnavailable):
        admit_remote_execution(parse_untrusted_remote_envelope(wire))


@pytest.mark.parametrize("change", [
    {"schema_version": "2"},
    {"org_id": ""},
    {"org_id": "../../foreign"},
    {"org_id": "a" * 129},
    {"sender_device_id": "device:b"},
    {"policy_epoch": -1},
    {"policy_epoch": True},
    {"policy_epoch": 0},
    {"expires_at": 0},
    {"expires_at": 2**63},
    {"nonce": "\\u0000"},
    {"payload_sha256": "b" * 63},
    {"payload_sha256": "B" * 64},
])
def test_invalid_claims_rejected_before_security_admission(change):
    wire = valid()
    wire.update(change)
    with pytest.raises(InvalidRemoteEnvelope):
        parse_untrusted_remote_envelope(wire)


def test_unknown_fields_and_missing_claims_rejected():
    with pytest.raises(InvalidRemoteEnvelope):
        parse_untrusted_remote_envelope({**valid(), "allow_all": True})
    wire = valid()
    del wire["org_id"]
    with pytest.raises(InvalidRemoteEnvelope):
        parse_untrusted_remote_envelope(wire)


def test_untrusted_type_never_grants_remote_execution():
    with pytest.raises(InvalidRemoteEnvelope):
        admit_remote_execution(valid())


@pytest.mark.parametrize("bad", [None, [], "hello", 1, {"org_id": "org:alpha"}])
def test_malformed_container_denied(bad):
    with pytest.raises(InvalidRemoteEnvelope):
        parse_untrusted_remote_envelope(bad)



def test_bounded_frame_valid_decoding_still_cannot_execute():
    wire = json.dumps(valid(), separators=(",", ":")).encode("utf-8")
    assert decode_untrusted_remote_envelope(wire) == parse_untrusted_remote_envelope(valid())
    with pytest.raises(RemoteAdmissionUnavailable):
        admit_remote_execution(decode_untrusted_remote_envelope(wire))


@pytest.mark.parametrize("raw", [
    b"", b"[]", b"null", b"NaN", b"{} trailing", b"not json",
    b'{"org_id":"org:alpha","org_id":"org:beta"}',
    b'{"unknown":{"x":1,"x":2}}',
    bytes([0xef, 0xbb, 0xbf]) + b"{}",
    bytes([0xff]),
    b"{" + b" " * 16384 + b"}",
])
def test_raw_frame_malformed_duplicate_or_oversized_denied(raw):
    with pytest.raises(InvalidRemoteEnvelope):
        decode_untrusted_remote_envelope(raw)


@pytest.mark.parametrize("raw", [None, "{}", bytearray(b"{}"), {}, 123])
def test_ingress_accepts_only_immutable_bytes(raw):
    with pytest.raises(InvalidRemoteEnvelope):
        decode_untrusted_remote_envelope(raw)


def test_oversized_frame_is_rejected_before_json_parser(monkeypatch):
    from locus_runtime import org_compute_contract as module

    def forbidden_parser(*args, **kwargs):
        raise AssertionError("oversized payload must not reach JSON parsing")

    monkeypatch.setattr(module.json, "loads", forbidden_parser)
    with pytest.raises(InvalidRemoteEnvelope, match="frame size denied"):
        decode_untrusted_remote_envelope(b"x" * (module.MAX_WIRE_BYTES + 1))


def test_escaped_unicode_surrogate_is_explicitly_denied_not_server_error():
    wire = valid()
    wire["org_id"] = chr(0xD800)
    with pytest.raises(InvalidRemoteEnvelope, match="identifier denied"):
        decode_untrusted_remote_envelope(json.dumps(wire).encode("utf-8"))
    with pytest.raises(InvalidRemoteEnvelope, match="identifier denied"):
        parse_untrusted_remote_envelope(wire)


@pytest.mark.parametrize("field", ["org_id", "workspace_id", "sender_device_id"])
def test_encoded_foreign_claims_have_no_authority(field):
    wire = valid()
    wire[field] = "foreign:spoofed"
    with pytest.raises(RemoteAdmissionUnavailable):
        admit_remote_execution(
            decode_untrusted_remote_envelope(json.dumps(wire).encode("utf-8"))
        )


@pytest.mark.parametrize("field", [
    "org_id", "sender_device_id", "recipient_device_id",
    "workspace_id", "run_id", "task_id", "nonce", "payload_sha256",
])
def test_unpaired_unicode_surrogate_fails_closed_without_unhandled_exception(field):
    wire = valid()
    wire[field] = chr(0xD800)
    with pytest.raises(InvalidRemoteEnvelope, match="identifier denied"):
        parse_untrusted_remote_envelope(wire)
