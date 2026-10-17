# OCF-01 increment 2 — bounded untrusted organization envelope frames

This implementation adds `decode_untrusted_remote_envelope` as a strict,
UTF-8 JSON ingress contract behind the existing always-deny
`admit_remote_execution` seam. **No remote listener, execution permission,
discovery or organization authority is enabled.**

- Maximum frame size: 16 KiB, applied **before** string decoding/JSON parsing.
- Only immutable byte strings accepted. Invalid JSON/UTF-8, duplicate keys,
  non-finite JSON literals, wrong data types, unsupported versions and unknown
  fields are rejected deterministically.
- Unicode surrogate escapes and overlong identifiers cannot produce an
  unhandled encoding error. All identifier values are untrusted claims.
- Successful parsing never authorizes a remote call; issuer verification,
  revocation freshness, workspace policy and receiving-side gateway mediation
  remain prerequisites under LOCUS-440 and LOCUS-442.

## Test matrix and regression boundary

`tests/unit/test_org_compute_contract.py`: raw frame success, bounded length,
preparse denial, truncated JSON, replay-shaped/foreign identifiers, duplicated
JSON keys, non-finite literals, Unicode errors, malformed types, unknown
capability smuggling and always-denied remote admission.

Use the backend unit, formatting/type, integration, DAST, Windows sandbox,
CodeQL and required gate jobs on the exact PR head. Keep
`Release-Impact: patch` because this is a parser tightening atop the
versioned contract introduced by the parent PR (minor 0.7). The parent PR
must merge first; rebase and rerun every required check before standalone merge.
