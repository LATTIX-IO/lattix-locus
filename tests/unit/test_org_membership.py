"""LOCUS-440: signed membership assertions cannot elevate remote authority."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from locus_runtime.org_compute_contract import (
    RemoteAdmissionUnavailable, admit_remote_execution, parse_untrusted_remote_envelope,
)
from locus_runtime.org_membership import (
    InvalidMembershipProof, PinnedOrganizationRoot, verify_membership_assertion,
)


NOW = 2_000_000_000
DOMAIN = b"LOCUS/ORG-MEMBERSHIP/v1\x00"


def claim(**changes):
    value = {
        "version": "1", "org_id": "org:alpha", "issuer_id": "issuer:primary",
        "device_id": "device:a", "issued_at": NOW - 10,
        "expires_at": NOW + 900, "revocation_epoch": 5,
    }
    value.update(changes)
    return value


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def keys():
    signer = Ed25519PrivateKey.generate()
    public_key = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    root = PinnedOrganizationRoot("org:alpha", "issuer:primary", public_key, 5)
    return signer, root


def verify(value=None, *, signer=None, root=None, peer="device:a", now=NOW, raw=None):
    if signer is None or root is None:
        signer, root = keys()
    message = raw if raw is not None else encode(claim() if value is None else value)
    signature = signer.sign(DOMAIN + message)
    return verify_membership_assertion(
        message, signature, trusted_root=root,
        authenticated_peer_device_id=peer, now_seconds=now,
    )


def test_verified_org_assertion_is_not_authorization():
    signer, root = keys()
    evidence = verify(signer=signer, root=root)
    assert (evidence.org_id, evidence.device_id, evidence.revocation_epoch) == (
        "org:alpha", "device:a", 5,
    )
    envelope = {
        "schema_version": "1", "org_id": "org:alpha", "sender_device_id": "device:a",
        "recipient_device_id": "device:b", "workspace_id": "workspace:one",
        "run_id": "run:one", "task_id": "task:one", "policy_epoch": 5,
        "expires_at": NOW + 100, "nonce": "nonce:one", "payload_sha256": "a" * 64,
    }
    with pytest.raises(RemoteAdmissionUnavailable):
        admit_remote_execution(parse_untrusted_remote_envelope(envelope))


@pytest.mark.parametrize("change", [
    {"org_id": "org:beta"}, {"issuer_id": "issuer:rogue"},
    {"device_id": "device:other"}, {"revocation_epoch": 4},
    {"revocation_epoch": True}, {"revocation_epoch": 0},
    {"issued_at": NOW + 45}, {"issued_at": NOW - 9999},
    {"expires_at": NOW}, {"expires_at": NOW - 1},
    {"expires_at": NOW + 3601}, {"expires_at": True},
    {"version": "2"}, {"allow_remote_execution": True},
])
def test_mismatched_or_expired_signed_claim_rejected(change):
    signer, root = keys()
    with pytest.raises(InvalidMembershipProof):
        verify(claim(**change), signer=signer, root=root)


def test_signature_domain_tamper_and_foreign_org_roots_rejected():
    signer, root = keys()
    foreign_signer, foreign_root = keys()
    raw = encode(claim())
    for signature, anchor in [
        (signer.sign(b"not-Locus" + raw), root),
        (foreign_signer.sign(DOMAIN + raw), root),
        (signer.sign(DOMAIN + raw), foreign_root),
        (b"\x00" * 64, root),
    ]:
        with pytest.raises(InvalidMembershipProof):
            verify_membership_assertion(
                raw, signature, trusted_root=anchor,
                authenticated_peer_device_id="device:a", now_seconds=NOW,
            )


@pytest.mark.parametrize("data", [
    b"{}", b"[]", b"NaN", b"not json", b"\xff",
    b'{"org_id":"org:alpha","org_id":"org:beta"}',
    b"{" + b" " * 4096 + b"}",
])
def test_malformed_or_oversized_membership_claim_rejected(data):
    signer, root = keys()
    with pytest.raises(InvalidMembershipProof):
        verify(raw=data, signer=signer, root=root)


def test_noncanonical_json_rejected_even_with_valid_signature():
    signer, root = keys()
    raw = json.dumps(claim(), indent=2).encode("ascii")
    with pytest.raises(InvalidMembershipProof):
        verify(raw=raw, signer=signer, root=root)


@pytest.mark.parametrize("peer", ["device:b", "", "device:a/../../other"])
def test_claim_cannot_choose_independently_authenticated_peer_identity(peer):
    signer, root = keys()
    with pytest.raises(InvalidMembershipProof):
        verify(signer=signer, root=root, peer=peer)


def test_pinned_root_requires_valid_key_and_epoch():
    for key, epoch in [(b"bad", 5), (b"0" * 32, False), (b"0" * 32, 0)]:
        with pytest.raises(InvalidMembershipProof):
            PinnedOrganizationRoot("org:alpha", "issuer:primary", key, epoch)


def test_explicit_missing_trust_context_denies_without_allowing_callers_to_self_sign():
    signer, root = keys()
    raw = encode(claim())
    for bad_root in (None, {"org_id": "org:alpha"}, object()):
        with pytest.raises(InvalidMembershipProof):
            verify_membership_assertion(
                raw, signer.sign(DOMAIN + raw), trusted_root=bad_root,
                authenticated_peer_device_id="device:a", now_seconds=NOW,
            )


def test_revocation_epoch_advance_invalidates_previously_signed_claim():
    signer, root = keys()
    raw = encode(claim())
    signature = signer.sign(DOMAIN + raw)
    updated = PinnedOrganizationRoot(root.org_id, root.issuer_id, root.public_key, 6)
    with pytest.raises(InvalidMembershipProof):
        verify_membership_assertion(
            raw, signature, trusted_root=updated,
            authenticated_peer_device_id="device:a", now_seconds=NOW,
        )


@pytest.mark.parametrize("org,issuer", [
    (None, "issuer:primary"),
    ("org:alpha", None),
    (123, "issuer:primary"),
    ("../foreign", "issuer:primary"),
])
def test_invalid_trust_root_identifiers_deny_with_typed_error(org, issuer):
    with pytest.raises(InvalidMembershipProof):
        PinnedOrganizationRoot(org, issuer, b"0" * 32, 5)



@pytest.mark.parametrize("key", [
    bytes([1]) + bytes(31),       # identity public point (universal forgery risk)
    (2**255 - 20).to_bytes(32, "little"),  # order-2 point, y=-1
    bytes(32),                   # low order/noncanonical invalid point
    bytes([0xFF]) * 32,          # noncanonical y field
])
def test_small_order_or_noncanonical_issuer_keys_never_become_trust_roots(key):
    with pytest.raises(InvalidMembershipProof):
        PinnedOrganizationRoot("org:alpha", "issuer:primary", key, 5)


def test_identity_key_cannot_accept_zero_signature_forgery():
    identity = bytes([1]) + bytes(31)
    forged_signature = identity + bytes(32)
    raw = encode(claim())
    with pytest.raises(InvalidMembershipProof):
        root = PinnedOrganizationRoot("org:alpha", "issuer:primary", identity, 5)
        verify_membership_assertion(
            raw, forged_signature, trusted_root=root,
            authenticated_peer_device_id="device:a", now_seconds=NOW,
        )
