# LOCUS-440 increment 1: signed organization membership assertions

Status: **security prerequisite only**, NOT remote-work authorization.

A locally pinned `PinnedOrganizationRoot` binds an exact organization,
issuer Ed25519 public key and minimum revocation epoch. A short-lived claim
must be canonical, fully typed, signed over a versioned domain-separated
message, match the organization and issuer from the pinned local root and
match the *separately authenticated peer device identity* supplied by the
receiver. Claims are at most 4KiB; maximum lifetime 3600 seconds.

The verifier rejects malformed/duplicate JSON, extra fields, untrusted roots,
invalid signatures, wrong issuer, wrong peer, wrong organization, revoked
epochs, future/expired assertions, overlong lifetimes, noncanonical encoding,
invalid Unicode, and mismatched message-signature pairs.

**Security boundary:** an issuer-signed membership assertion proves only
an issuer attested the named device at a particular epoch and expiry.
It does NOT prove current possession of the device private key, challenge
freshness, revocation state beyond the supplied epoch, entitlement to a
workspace, or authorization for tools, files, model calls or execution.
Those remain blocked by OCF-02/OCF-04 and the existing
`org_compute_contract.admit_remote_execution` always-deny barrier.

This increment adds no endpoint, daemon or peer listener, and does not
accept trust roots from the signed payload. The caller must resolve the
root from a local, explicitly provisioned trust store and device identity
from authenticated transport; failure to do so MUST deny.

Verification: `tests/unit/test_org_membership.py` covers signature
tampering, cross-org roots, issuer/peer mismatch, revocation, expiry,
duplicate/noncanonical JSON, invalid framing and proof-not-equal-permission.
Full CI, mypy/ruff, static and adversarial checks remain PR merge gates.
