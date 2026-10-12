"""Signed peer challenge regression/security tests; no remote execution enabled."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from locus_runtime.org_membership import PinnedOrganizationRoot
from locus_runtime.org_peer_possession import (
    DevicePossessionChallenge, InvalidPeerProof, LocallyPinnedDevice,
)

NOW = 2_000_000_000
MEMBER_DOMAIN = b"LOCUS/ORG-MEMBERSHIP/v1\x00"


def keys():
    root_sk, device_sk = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    def raw(sk):
        return sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    root = PinnedOrganizationRoot("org:alpha", "issuer:primary", raw(root_sk), 5)
    device = LocallyPinnedDevice("org:alpha", "device:a", raw(device_sk))
    return root_sk, device_sk, root, device


def signed_membership(root_sk, **changes):
    claims = {
        "version": "1", "org_id": "org:alpha", "issuer_id": "issuer:primary",
        "device_id": "device:a", "issued_at": NOW - 5,
        "expires_at": NOW + 900, "revocation_epoch": 5,
    }
    claims.update(changes)
    wire = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("ascii")
    return wire, root_sk.sign(MEMBER_DOMAIN + wire)


def challenge_setup():
    root_sk, dev_sk, root, device = keys()
    instance = DevicePossessionChallenge(
        organization=root, enrolled_device=device, issued_at=NOW,
    )
    raw, signature = signed_membership(root_sk)
    return root_sk, dev_sk, instance, raw, signature


def test_valid_issuer_claim_and_peer_key_possession_only_produce_evidence():
    _, sk, challenge, raw, member_sig = challenge_setup()
    evidence = challenge.verify(
        peer_signature=sk.sign(challenge.signing_payload),
        membership_raw=raw, membership_signature=member_sig,
        now_seconds=NOW,
    )
    assert evidence.org_id == "org:alpha"
    assert evidence.device_id == "device:a"
    assert evidence.revocation_epoch == 5
    assert len(evidence.challenge_sha256) == 64
    assert not hasattr(evidence, "execute")


def test_same_signed_response_replay_and_failed_attempt_both_consume_nonce():
    _, sk, challenge, raw, member_sig = challenge_setup()
    valid = sk.sign(challenge.signing_payload)
    with pytest.raises(InvalidPeerProof):
        challenge.verify(
            peer_signature=bytes(64), membership_raw=raw,
            membership_signature=member_sig, now_seconds=NOW,
        )
    with pytest.raises(InvalidPeerProof, match="already consumed"):
        challenge.verify(
            peer_signature=valid, membership_raw=raw,
            membership_signature=member_sig, now_seconds=NOW,
        )


def test_wrong_key_or_replayed_signature_from_another_challenge_denied():
    _, sk, challenge, raw, member_sig = challenge_setup()
    other_sk = Ed25519PrivateKey.generate()
    for signer in (other_sk,):
        other = DevicePossessionChallenge(
            organization=challenge._organization, enrolled_device=challenge._device,
            issued_at=NOW,
        )
        with pytest.raises(InvalidPeerProof):
            other.verify(
                peer_signature=signer.sign(other.signing_payload),
                membership_raw=raw, membership_signature=member_sig,
                now_seconds=NOW,
            )
    other = DevicePossessionChallenge(
        organization=challenge._organization, enrolled_device=challenge._device,
        issued_at=NOW,
    )
    with pytest.raises(InvalidPeerProof):
        other.verify(
            peer_signature=sk.sign(challenge.signing_payload),
            membership_raw=raw, membership_signature=member_sig,
            now_seconds=NOW,
        )


@pytest.mark.parametrize("offset", [-1, 60, 3600])
def test_challenge_expiry_and_time_bounds_are_strict(offset):
    _, sk, challenge, raw, member_sig = challenge_setup()
    with pytest.raises(InvalidPeerProof):
        challenge.verify(
            peer_signature=sk.sign(challenge.signing_payload),
            membership_raw=raw, membership_signature=member_sig,
            now_seconds=NOW + offset,
        )


@pytest.mark.parametrize("updates", [
    {"org_id": "org:other"}, {"device_id": "device:b"},
    {"revocation_epoch": 4}, {"expires_at": NOW - 1},
])
def test_membership_tampering_foreign_scope_revocation_and_expiry_denied(updates):
    root_sk, sk, challenge, _, _ = challenge_setup()
    raw, member_sig = signed_membership(root_sk, **updates)
    with pytest.raises(InvalidPeerProof):
        challenge.verify(
            peer_signature=sk.sign(challenge.signing_payload),
            membership_raw=raw, membership_signature=member_sig,
            now_seconds=NOW,
        )


def test_peer_and_root_must_match_organization_even_before_challenge_issue():
    _, _, root, device = keys()
    other = LocallyPinnedDevice("org:beta", device.device_id, device.public_key)
    with pytest.raises(InvalidPeerProof):
        DevicePossessionChallenge(
            organization=root, enrolled_device=other, issued_at=NOW,
        )


@pytest.mark.parametrize("key", [
    bytes([1]) + bytes(31), bytes(32), b"short",
])
def test_untrusted_low_order_or_invalid_device_keys_denied(key):
    with pytest.raises(InvalidPeerProof):
        LocallyPinnedDevice("org:alpha", "device:a", key)


def test_atomic_consumption_allows_at_most_one_parallel_verification():
    _, sk, instance, raw, member_sig = challenge_setup()
    response = sk.sign(instance.signing_payload)

    def run(_):
        try:
            instance.verify(
                peer_signature=response,
                membership_raw=raw, membership_signature=member_sig,
                now_seconds=NOW,
            )
            return True
        except InvalidPeerProof:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(run, range(24))) == 1
