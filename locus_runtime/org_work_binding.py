"""OCF-02: bind a signed one-use peer challenge to one untrusted work envelope.

This inspection proves only what an enrolled device actually signed. It does
NOT authorize execution, delegate tools, or attest the body behind its hash.
The receiving-side policy enforcement point remains unimplemented.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from locus_runtime.org_compute_contract import (
    UntrustedRemoteEnvelope, decode_untrusted_remote_envelope,
)
from locus_runtime.org_peer_possession import DevicePossessionChallenge, InvalidPeerProof

_WORK_DOMAIN = b"LOCUS/ORG-WORK-ENVELOPE/v1\x00"
_MAX_WORK_TTL_SECONDS = 300


class InvalidBoundWorkProof(PermissionError):
    """No valid binding of authenticated peer, organization and work claims."""


@dataclass(frozen=True, slots=True)
class VerifiedBoundWorkClaim:
    """Cryptographic integrity evidence. This is NEVER an execution grant."""

    org_id: str
    sender_device_id: str
    recipient_device_id: str
    workspace_id: str
    run_id: str
    task_id: str
    policy_epoch: int
    expires_at: int
    payload_sha256: str
    frame_sha256: str
    challenge_sha256: str


def work_binding_signing_payload(challenge_payload: bytes, raw_frame: bytes) -> bytes:
    """Domain-separate a peer's signature over BOTH nonce and exact raw frame.

    Signing a standalone possession challenge is insufficient: an attacker on
    another session could substitute different task claims after proof. This
    second signature binds the exact canonical wire bytes to the one-use proof.
    """
    if type(challenge_payload) is not bytes or type(raw_frame) is not bytes:
        raise InvalidBoundWorkProof("work binding input denied")
    if len(challenge_payload) > 512 or not 0 < len(raw_frame) <= 16_384:
        raise InvalidBoundWorkProof("work binding size denied")
    return (
        _WORK_DOMAIN
        + hashlib.sha256(challenge_payload).digest()
        + hashlib.sha256(raw_frame).digest()
    )


def inspect_signed_org_work_claim(
    *,
    raw_frame: bytes,
    challenge: DevicePossessionChallenge,
    peer_signature: bytes,
    envelope_signature: bytes,
    membership_raw: bytes,
    membership_signature: bytes,
    locally_authenticated_recipient: str,
    now_seconds: int,
) -> VerifiedBoundWorkClaim:
    """Cryptographically inspect bounded work intent; do not admit execution.

    This must be called inside a trusted receiver where challenge and locally
    authenticated recipient are NOT supplied by an untrusted peer. No policy
    decision, workspace grants, artifact decryption or tool call happens here.
    """
    if not isinstance(challenge, DevicePossessionChallenge):
        raise InvalidBoundWorkProof("trusted challenge missing")
    if type(now_seconds) is not int or now_seconds <= 0:
        raise InvalidBoundWorkProof("work inspection clock invalid")
    try:
        # Spend the nonce BEFORE parsing attacker-controlled work bytes.
        # Even malformed frames or unexpected fields must consume the proof.
        peer = challenge.verify(
            peer_signature=peer_signature,
            membership_raw=membership_raw,
            membership_signature=membership_signature,
            now_seconds=now_seconds,
        )
        envelope: UntrustedRemoteEnvelope = decode_untrusted_remote_envelope(raw_frame)
        if type(envelope_signature) is not bytes or len(envelope_signature) != 64:
            raise InvalidBoundWorkProof("envelope signature invalid")
        # Device identity/key are pinned inside the trusted challenge. NEVER
        # accept a key included in raw_frame or supplied with the response.
        Ed25519PublicKey.from_public_bytes(challenge._device.public_key).verify(
            envelope_signature,
            work_binding_signing_payload(challenge.signing_payload, raw_frame),
        )
        if (
            type(locally_authenticated_recipient) is not str
            or envelope.org_id != peer.org_id
            or envelope.sender_device_id != peer.device_id
            or envelope.recipient_device_id != locally_authenticated_recipient
            or envelope.recipient_device_id == peer.device_id
            or envelope.policy_epoch != peer.revocation_epoch
            or envelope.expires_at > peer.membership_expires_at
            or not now_seconds < envelope.expires_at <= now_seconds + _MAX_WORK_TTL_SECONDS
        ):
            raise InvalidBoundWorkProof("work claim trust/scope mismatch")
    except (ValueError, InvalidSignature, InvalidPeerProof):
        raise InvalidBoundWorkProof("signed work claim denied") from None
    return VerifiedBoundWorkClaim(
        org_id=envelope.org_id,
        sender_device_id=envelope.sender_device_id,
        recipient_device_id=envelope.recipient_device_id,
        workspace_id=envelope.workspace_id,
        run_id=envelope.run_id,
        task_id=envelope.task_id,
        policy_epoch=envelope.policy_epoch,
        expires_at=envelope.expires_at,
        payload_sha256=envelope.payload_sha256,
        frame_sha256=hashlib.sha256(raw_frame).hexdigest(),
        challenge_sha256=peer.challenge_sha256,
    )
