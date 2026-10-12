# LOCUS-440 increment 2 — one-use peer possession challenge

Purpose: bind a valid organization issuer assertion to cryptographic **proof of
possession** of a locally pinned enrolled device public key.

- Challenge verifier constructs a random 256-bit nonce locally using
  cryptographically secure entropy and a versioned, domain-separated signing
  payload bound to organization, device ID, issuance and 60-second expiry.
- Local configured organization trust root and *separately enrolled* device
  public key must come from trusted state, **never** the peer response.
- Device signs the challenge with its own Ed25519 key. Verifier independently
  validates device signature, issuer-signed canonical membership, peer ID,
  revocation epoch and expiry.
- Challenge is consumed atomically on the **first attempt**, including invalid
  attempts; concurrent replay cannot obtain multiple successes. Restart
  discards all challenges and never reuses old random nonces.
- Public device key rejects identity, low-order or noncanonical points. The
  resulting VerifiedPeerPossession is evidence, **not a remote grant**.

This module has no listener, no credential issuer, no enrollment store,
no network transport and no execution path. Organization-wide membership and
workspace delegation remain blocked by existing
`org_compute_contract.admit_remote_execution`.
Future LOCUS-440 work must add trusted enrollment storage, issuer rotation,
revocation propagation, key life cycle and transport binding; LOCUS-442 must
build separate receiving-side workload authorization.

The tests include issuer and peer mismatch, wrong private key, replay on new
challenge, concurrent nonce consumption, expiry, malformed key and
foreign-organization rejection. Do not claim full LOCUS-440 SDD completion
from this crypto primitive or treat skipped CI security scans as passed.
