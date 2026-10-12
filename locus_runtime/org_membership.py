"""LOCUS-440 increment 1: verify signed, scoped organization membership claims.

Verification is NOT enrollment, peer channel authentication, authorization,
replay storage, or permission to run tasks on another device. Remote admission
remains unconditionally disabled by org_compute_contract.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MEMBERSHIP_VERSION = "1"
MEMBERSHIP_FRAME_MAX_BYTES = 4096
MAX_MEMBERSHIP_LIFETIME_SECONDS = 3600
MAX_ISSUER_CLOCK_SKEW_SECONDS = 30
_MEMBERSHIP_DOMAIN = b"LOCUS/ORG-MEMBERSHIP/v1\x00"
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_FIELDS = frozenset({
    "version", "org_id", "issuer_id", "device_id",
    "issued_at", "expires_at", "revocation_epoch",
})


class InvalidMembershipProof(ValueError):
    """A signed membership assertion is invalid, stale, or mismatched."""


# Ed25519 verification libraries vary in how they handle low-order public
# points. A locally pinned issuer key must be canonical, nonidentity and in
# the prime-order subgroup; accepting the identity key risks universal forgery.
_ED25519_P = 2**255 - 19
_ED25519_L = 2**252 + 27742317777372353535851937790883648493
_ED25519_D = (-121665 * pow(121666, -1, _ED25519_P)) % _ED25519_P
_ED25519_SQRT_M1 = pow(2, (_ED25519_P - 1) // 4, _ED25519_P)


def _valid_ed25519_issuer_key(key: bytes) -> bool:
    """Verify a public key encodes a nonzero prime-order Edwards point.

    Point arithmetic operates only on nonsecret public trust configuration.
    No private key, scalar from user data, or signing operation is performed.
    """
    if type(key) is not bytes or len(key) != 32:
        return False
    encoded = int.from_bytes(key, "little")
    y, sign = encoded & ((1 << 255) - 1), encoded >> 255
    p = _ED25519_P
    if y >= p:
        return False
    y2 = (y * y) % p
    denominator = (_ED25519_D * y2 + 1) % p
    if denominator == 0:
        return False
    x2 = (y2 - 1) * pow(denominator, p - 2, p) % p
    x = pow(x2, (p + 3) // 8, p)
    if x * x % p != x2:
        x = x * _ED25519_SQRT_M1 % p
    if x * x % p != x2 or (x == 0 and sign):
        return False
    if x & 1 != sign:
        x = p - x
    if x == 0 and y == 1:
        return False

    def add(a: tuple[int, int, int, int],
            b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x1, y1, z1, t1 = a
        x2, y2, z2, t2 = b
        aa = (y1 - x1) * (y2 - x2) % p
        bb = (y1 + x1) * (y2 + x2) % p
        cc = 2 * _ED25519_D * t1 * t2 % p
        dd = 2 * z1 * z2 % p
        ee, ff, gg, hh = (bb - aa) % p, (dd - cc) % p, (dd + cc) % p, (bb + aa) % p
        return ee * ff % p, gg * hh % p, ff * gg % p, ee * hh % p

    point = (x, y, 1, x * y % p)
    result = (0, 1, 1, 0)
    scalar = _ED25519_L
    while scalar:
        if scalar & 1:
            result = add(result, point)
        point = add(point, point)
        scalar >>= 1
    xx, yy, zz, _ = result
    return xx % p == 0 and (yy - zz) % p == 0


@dataclass(frozen=True, slots=True)
class PinnedOrganizationRoot:
    """Trust anchor provisioned by a local administrator, NEVER from the claim."""

    org_id: str
    issuer_id: str
    public_key: bytes
    min_revocation_epoch: int

    def __post_init__(self) -> None:
        if (
            any(type(v) is not str or _ID.fullmatch(v) is None
                for v in (self.org_id, self.issuer_id))
            or not _valid_ed25519_issuer_key(self.public_key)
            or type(self.min_revocation_epoch) is not int
            or self.min_revocation_epoch <= 0
        ):
            raise InvalidMembershipProof("pinned organization trust root invalid")


@dataclass(frozen=True, slots=True)
class VerifiedMembershipAssertion:
    """Signature-bound evidence only; NOT a runtime access capability."""

    org_id: str
    issuer_id: str
    device_id: str
    revocation_epoch: int
    expires_at: int


def verify_membership_assertion(
    raw: bytes,
    signature: bytes,
    *,
    trusted_root: PinnedOrganizationRoot,
    authenticated_peer_device_id: str,
    now_seconds: int,
) -> VerifiedMembershipAssertion:
    """Verify pinned-root Ed25519 issuer signature and bounded identity claims.

    The caller must supply the PEER DEVICE ID from a separately verified channel
    and the root from a locally controlled trust store. Never derive either
    from fields in the untrusted membership assertion.
    """
    if (
        type(raw) is not bytes or not 0 < len(raw) <= MEMBERSHIP_FRAME_MAX_BYTES
        or type(signature) is not bytes or len(signature) != 64
        or not isinstance(trusted_root, PinnedOrganizationRoot)
        or type(authenticated_peer_device_id) is not str
        or _ID.fullmatch(authenticated_peer_device_id) is None
        or type(now_seconds) is not int or now_seconds <= 0
    ):
        raise InvalidMembershipProof("membership proof framing denied")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("duplicate claim")
            values[key] = value
        return values

    def reject_constant(value: str) -> NoReturn:
        raise ValueError("nonfinite JSON constant")

    try:
        claims = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
        )
        if type(claims) is not dict or set(claims) != _FIELDS:
            raise ValueError("unknown membership fields")
        if raw != json.dumps(
            claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii"):
            raise ValueError("noncanonical membership assertion")
        for name in ("org_id", "issuer_id", "device_id"):
            val = claims[name]
            if type(val) is not str or _ID.fullmatch(val) is None:
                raise ValueError("bad claim identifier")
        if claims["version"] != MEMBERSHIP_VERSION:
            raise ValueError("unsupported membership assertion version")
        for name in ("issued_at", "expires_at", "revocation_epoch"):
            val = claims[name]
            if type(val) is not int or val <= 0 or val > 2**63 - 1:
                raise ValueError("bad membership numeric bound")
        if (
            claims["org_id"] != trusted_root.org_id
            or claims["issuer_id"] != trusted_root.issuer_id
            or claims["device_id"] != authenticated_peer_device_id
            or claims["revocation_epoch"] < trusted_root.min_revocation_epoch
        ):
            raise ValueError("organization, issuer, peer or revocation mismatch")
        if (
            claims["issued_at"] > now_seconds + MAX_ISSUER_CLOCK_SKEW_SECONDS
            or claims["expires_at"] <= now_seconds
            or claims["expires_at"] <= claims["issued_at"]
            or claims["expires_at"] - claims["issued_at"] > MAX_MEMBERSHIP_LIFETIME_SECONDS
        ):
            raise ValueError("membership assertion freshness denied")
        Ed25519PublicKey.from_public_bytes(trusted_root.public_key).verify(
            signature, _MEMBERSHIP_DOMAIN + raw,
        )
    except (UnicodeError, ValueError, RecursionError, InvalidSignature):
        raise InvalidMembershipProof("organization membership proof denied") from None
    return VerifiedMembershipAssertion(
        org_id=claims["org_id"],
        issuer_id=claims["issuer_id"],
        device_id=claims["device_id"],
        revocation_epoch=claims["revocation_epoch"],
        expires_at=claims["expires_at"],
    )
