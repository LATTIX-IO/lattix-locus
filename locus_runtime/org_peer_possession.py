"""LOCUS-440 increment 2: one-use signed device-possession challenges.

No incoming network interface and no remote-work authorization are installed.
Callers MUST obtain the trusted device public key from a local enrolled-device
registry, NEVER from a remote response or the issuer membership assertion.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from threading import Lock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from locus_runtime.org_membership import (
    InvalidMembershipProof, PinnedOrganizationRoot,
    _valid_ed25519_issuer_key, verify_membership_assertion,
)

_CHALLENGE_DOMAIN = b"LOCUS/ORG-PEER-POSSESSION/v1\x00"
_DEVICE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_CHALLENGE_TTL = 60


class InvalidPeerProof(PermissionError):
    """An untrusted or replayed device-possession proof was denied."""


@dataclass(frozen=True, slots=True)
class LocallyPinnedDevice:
    """An enrolled public key obtained from a LOCAL administrative trust store."""

    org_id: str
    device_id: str
    public_key: bytes

    def __post_init__(self) -> None:
        if (
            type(self.org_id) is not str or _DEVICE_ID.fullmatch(self.org_id) is None
            or type(self.device_id) is not str
            or _DEVICE_ID.fullmatch(self.device_id) is None
            or not _valid_ed25519_issuer_key(self.public_key)
        ):
            raise InvalidPeerProof("local enrolled device key denied")


@dataclass(frozen=True, slots=True)
class VerifiedPeerPossession:
    """Verified issuer claim AND possession of the pinned device key; not a grant."""

    org_id: str
    device_id: str
    membership_expires_at: int
    revocation_epoch: int
    challenge_sha256: str


class DevicePossessionChallenge:
    """Single-use local verifier; failed attempts consume the challenge too."""

    def __init__(
        self, *,
        organization: PinnedOrganizationRoot,
        enrolled_device: LocallyPinnedDevice,
        issued_at: int,
    ) -> None:
        if (
            not isinstance(organization, PinnedOrganizationRoot)
            or not isinstance(enrolled_device, LocallyPinnedDevice)
            or organization.org_id != enrolled_device.org_id
            or type(issued_at) is not int or issued_at <= 0
            or issued_at > 2**63 - 1 - _CHALLENGE_TTL
        ):
            raise InvalidPeerProof("peer challenge trust context denied")
        self._organization = organization
        self._device = enrolled_device
        self._issued_at = issued_at
        self._nonce = secrets.token_bytes(32)
        self._consumed = False
        self._lock = Lock()

    @property
    def signing_payload(self) -> bytes:
        """Versioned, context-bound public challenge bytes sent to the device."""
        fields = {
            "device_id": self._device.device_id,
            "expires_at": self._issued_at + _CHALLENGE_TTL,
            "issued_at": self._issued_at,
            "nonce": self._nonce.hex(),
            "org_id": self._organization.org_id,
        }
        return _CHALLENGE_DOMAIN + json.dumps(
            fields, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")

    def verify(
        self,
        *, peer_signature: bytes,
        membership_raw: bytes,
        membership_signature: bytes,
        now_seconds: int,
    ) -> VerifiedPeerPossession:
        """Consume before any attacker-controlled parsing or signature work."""
        with self._lock:
            if self._consumed:
                raise InvalidPeerProof("peer challenge already consumed")
            self._consumed = True
        if (
            type(now_seconds) is not int
            or not self._issued_at <= now_seconds < self._issued_at + _CHALLENGE_TTL
            or type(peer_signature) is not bytes or len(peer_signature) != 64
        ):
            raise InvalidPeerProof("peer challenge expired or malformed")
        try:
            member = verify_membership_assertion(
                membership_raw,
                membership_signature,
                trusted_root=self._organization,
                authenticated_peer_device_id=self._device.device_id,
                now_seconds=now_seconds,
            )
            Ed25519PublicKey.from_public_bytes(self._device.public_key).verify(
                peer_signature, self.signing_payload,
            )
        except (InvalidMembershipProof, InvalidSignature, ValueError):
            raise InvalidPeerProof("peer possession assertion denied") from None
        return VerifiedPeerPossession(
            org_id=member.org_id,
            device_id=member.device_id,
            membership_expires_at=member.expires_at,
            revocation_epoch=member.revocation_epoch,
            challenge_sha256=hashlib.sha256(self.signing_payload).hexdigest(),
        )
