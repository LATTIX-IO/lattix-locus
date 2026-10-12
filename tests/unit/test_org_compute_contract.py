"""OCF-01 trust-boundary contract: untrusted data is never authority."""
from __future__ import annotations

import pytest

from locus_runtime.org_compute_contract import (
    InvalidRemoteEnvelope, RemoteAdmissionUnavailable,
    admit_remote_execution, parse_untrusted_remote_envelope,
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



@pytest.mark.parametrize("field", [
    "org_id", "sender_device_id", "recipient_device_id",
    "workspace_id", "run_id", "task_id", "nonce", "payload_sha256",
])
def test_unpaired_unicode_surrogate_fails_closed_without_unhandled_exception(field):
    wire = valid()
    wire[field] = "\\ud800"
    with pytest.raises(InvalidRemoteEnvelope, match="identifier denied"):
        parse_untrusted_remote_envelope(wire)
