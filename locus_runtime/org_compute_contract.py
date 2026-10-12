"""OCF-01 / LOCUS-439: untrusted organization-compute wire contract.

These parsers NEVER authenticate claims and NEVER authorize remote execution.
Until OCF-02 provides a verified issuer and OCF-04 a receiver-side PEP,
remote admission is intentionally unavailable, including with well-formed data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA_VERSION = "1"
MAX_WIRE_BYTES = 16_384
_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_.:-]{0,127}\Z")
_ALLOWED = frozenset({
    "schema_version", "org_id", "sender_device_id", "recipient_device_id",
    "workspace_id", "run_id", "task_id", "policy_epoch", "expires_at",
    "nonce", "payload_sha256",
})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class InvalidRemoteEnvelope(ValueError):
    """An untrusted wire envelope is malformed or unsupported."""


class RemoteAdmissionUnavailable(PermissionError):
    """No authenticated cross-device authorization path is installed."""


@dataclass(frozen=True, slots=True)
class UntrustedRemoteEnvelope:
    schema_version: str
    org_id: str
    sender_device_id: str
    recipient_device_id: str
    workspace_id: str
    run_id: str
    task_id: str
    policy_epoch: int
    expires_at: int
    nonce: str
    payload_sha256: str


def parse_untrusted_remote_envelope(value: Mapping[str, Any]) -> UntrustedRemoteEnvelope:
    """Parse untrusted *claims* only; no identity, scope or policy is proven.

    Reject unknown fields rather than silently accepting future capability
    extensions. A gateway must additionally enforce byte bounds on raw frames.
    """
    if not isinstance(value, dict) or set(value) != _ALLOWED:
        raise InvalidRemoteEnvelope("remote envelope fields denied")
    fields = {key: value[key] for key in _ALLOWED}
    for key in _ALLOWED - {"policy_epoch", "expires_at"}:
        data = fields[key]
        if type(data) is not str or not data or len(data.encode("utf-8")) > 128:
            raise InvalidRemoteEnvelope("remote envelope identifier denied")
    for key in ("policy_epoch", "expires_at"):
        data = fields[key]
        if type(data) is not int or data <= 0 or data > 2**63 - 1:
            raise InvalidRemoteEnvelope("remote envelope numeric bound denied")
    if fields["schema_version"] != SCHEMA_VERSION:
        raise InvalidRemoteEnvelope("remote envelope version denied")
    for key in ("org_id", "sender_device_id", "recipient_device_id", "workspace_id",
                "run_id", "task_id", "nonce"):
        if _ID.fullmatch(fields[key]) is None:
            raise InvalidRemoteEnvelope("remote envelope identifier denied")
    if _SHA256.fullmatch(fields["payload_sha256"]) is None:
        raise InvalidRemoteEnvelope("remote envelope payload digest denied")
    if fields["sender_device_id"] == fields["recipient_device_id"]:
        raise InvalidRemoteEnvelope("remote envelope self-delegation denied")
    return UntrustedRemoteEnvelope(**fields)


def admit_remote_execution(envelope: UntrustedRemoteEnvelope) -> None:
    """Security hold until issuer/attestation, placement and PEP are implemented.

    Never replace with an org_id equality check. Authenticated identity must be
    injected by a trusted peer verifier, not taken from this parsed envelope.
    """
    if not isinstance(envelope, UntrustedRemoteEnvelope):
        raise InvalidRemoteEnvelope("remote envelope type denied")
    raise RemoteAdmissionUnavailable("organization remote execution is not enabled")
