"""End-to-end cryptographic evidence tests; NEVER remote-work admission."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from locus_runtime.org_compute_contract import (
    RemoteAdmissionUnavailable, admit_remote_execution, decode_untrusted_remote_envelope,
)
from locus_runtime.org_membership import PinnedOrganizationRoot
from locus_runtime.org_peer_possession import DevicePossessionChallenge, LocallyPinnedDevice
from locus_runtime.org_work_binding import (
    InvalidBoundWorkProof, inspect_signed_org_work_claim,
    work_binding_signing_payload,
)

NOW = 2_000_000_000
_MEMBER_DOMAIN = b"LOCUS/ORG-MEMBERSHIP/v1\x00"


def scenario():
    signer, device = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    def pub(sk):
        return sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    org = PinnedOrganizationRoot("org:alpha", "issuer:primary", pub(signer), 7)
    enrolled = LocallyPinnedDevice("org:alpha", "device:worker", pub(device))
    challenge = DevicePossessionChallenge(
        organization=org, enrolled_device=enrolled, issued_at=NOW,
    )
    member = {
        "version": "1", "org_id": "org:alpha", "issuer_id": "issuer:primary",
        "device_id": "device:worker", "revocation_epoch": 7,
        "issued_at": NOW - 10, "expires_at": NOW + 900,
    }
    raw_membership = json.dumps(member, sort_keys=True, separators=(",", ":")).encode()
    return signer, device, challenge, raw_membership, signer.sign(_MEMBER_DOMAIN + raw_membership)


def frame(**changes):
    claims = {
        "schema_version": "1", "org_id": "org:alpha",
        "sender_device_id": "device:worker", "recipient_device_id": "device:receiver",
        "workspace_id": "workspace:one", "run_id": "run:one", "task_id": "task:one",
        "policy_epoch": 7, "expires_at": NOW + 180,
        "nonce": "nonce:unique", "payload_sha256": "a" * 64,
    }
    claims.update(changes)
    return json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()


def inspect(raw=None, *, override_work_sig=None, recipient="device:receiver", now=NOW):
    _, sk, challenge, membership_raw, membership_sig = scenario()
    raw = frame() if raw is None else raw
    response = sk.sign(challenge.signing_payload)
    bound = sk.sign(work_binding_signing_payload(challenge.signing_payload, raw))
    if override_work_sig is not None:
        bound = override_work_sig
    return inspect_signed_org_work_claim(
        raw_frame=raw, challenge=challenge, peer_signature=response,
        envelope_signature=bound, membership_raw=membership_raw,
        membership_signature=membership_sig,
        locally_authenticated_recipient=recipient, now_seconds=now,
    )


def test_cryptographically_bound_claim_is_not_remote_execution_authority():
    raw = frame()
    evidence = inspect(raw)
    assert evidence.org_id == "org:alpha"
    assert evidence.sender_device_id == "device:worker"
    assert evidence.recipient_device_id == "device:receiver"
    assert evidence.policy_epoch == 7
    assert len(evidence.frame_sha256) == 64
    assert len(evidence.challenge_sha256) == 64
    with pytest.raises(RemoteAdmissionUnavailable):
        admit_remote_execution(decode_untrusted_remote_envelope(raw))


@pytest.mark.parametrize("changes", [
    {"org_id": "org:beta"},
    {"sender_device_id": "device:attacker"},
    {"recipient_device_id": "device:other"},
    {"policy_epoch": 6},
    {"policy_epoch": 8},
    {"expires_at": NOW - 1},
    {"expires_at": NOW + 400},
    {"expires_at": NOW + 901},
])
def test_signed_but_scope_invalid_work_intents_are_rejected(changes):
    with pytest.raises(InvalidBoundWorkProof):
        inspect(frame(**changes))


def test_locally_authenticated_receiver_not_chosen_by_sender():
    with pytest.raises(InvalidBoundWorkProof):
        inspect(recipient="device:other")


def test_swapped_frame_invalidates_signature_even_with_same_member():
    _, sk, challenge, membership_raw, membership_sig = scenario()
    original = frame(task_id="task:a")
    stolen_signature = sk.sign(
        work_binding_signing_payload(challenge.signing_payload, original)
    )
    with pytest.raises(InvalidBoundWorkProof):
        inspect_signed_org_work_claim(
            raw_frame=frame(task_id="task:b"), challenge=challenge,
            peer_signature=sk.sign(challenge.signing_payload),
            envelope_signature=stolen_signature,
            membership_raw=membership_raw, membership_signature=membership_sig,
            locally_authenticated_recipient="device:receiver", now_seconds=NOW,
        )


def test_work_signature_cannot_be_from_another_private_key():
    stranger = Ed25519PrivateKey.generate()
    with pytest.raises(InvalidBoundWorkProof):
        inspect(override_work_sig=stranger.sign(b"unrelated-task"))


def test_noncanonical_raw_frame_signing_cannot_bypass_strict_decode():
    raw = b'{"org_id":"org:alpha","org_id":"org:beta"}'
    with pytest.raises(InvalidBoundWorkProof):
        inspect(raw)


def test_forged_credential_or_scope_fields_rejected_before_possession_verification():
    claims = json.loads(frame())
    claims["execute_unrestricted"] = True
    with pytest.raises(InvalidBoundWorkProof):
        inspect(json.dumps(claims).encode())


def test_nonce_consumed_on_failed_work_signature():
    _, sk, challenge, membership_raw, membership_sig = scenario()
    raw = frame()
    def call(signature):
        return inspect_signed_org_work_claim(
            raw_frame=raw, challenge=challenge,
            peer_signature=sk.sign(challenge.signing_payload),
            envelope_signature=signature, membership_raw=membership_raw,
            membership_signature=membership_sig,
            locally_authenticated_recipient="device:receiver", now_seconds=NOW,
        )
    with pytest.raises(InvalidBoundWorkProof):
        call(bytes(64))
    valid = sk.sign(work_binding_signing_payload(challenge.signing_payload, raw))
    with pytest.raises(InvalidBoundWorkProof):
        call(valid)


@pytest.mark.parametrize("time", [NOW - 1, NOW + 60, NOW + 3600])
def test_session_challenge_lifetime_cannot_be_bypassed(time):
    with pytest.raises(InvalidBoundWorkProof):
        inspect(now=time)



def test_malformed_first_work_attempt_consumes_challenge_even_after_valid_possession():
    _, sk, challenge, membership_raw, membership_sig = scenario()
    proof = sk.sign(challenge.signing_payload)
    bad = b'{"org_id":"org:alpha","org_id":"org:beta"}'
    with pytest.raises(InvalidBoundWorkProof):
        inspect_signed_org_work_claim(
            raw_frame=bad, challenge=challenge, peer_signature=proof,
            envelope_signature=sk.sign(work_binding_signing_payload(challenge.signing_payload, bad)),
            membership_raw=membership_raw, membership_signature=membership_sig,
            locally_authenticated_recipient="device:receiver", now_seconds=NOW,
        )
    valid = frame()
    with pytest.raises(InvalidBoundWorkProof):
        inspect_signed_org_work_claim(
            raw_frame=valid, challenge=challenge, peer_signature=proof,
            envelope_signature=sk.sign(work_binding_signing_payload(challenge.signing_payload, valid)),
            membership_raw=membership_raw, membership_signature=membership_sig,
            locally_authenticated_recipient="device:receiver", now_seconds=NOW,
        )
