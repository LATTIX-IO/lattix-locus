# LOCUS-440 increment 3 — bind signed peer possession to exact work intent

Status: **cryptographic evidence only**; no remote execution or tool grant.

An authenticated organization peer can prove possession of its **locally
pinned** device key and sign a *specific* bounded work-envelope frame. The
signature message domain-separates
`SHA256(peer_challenge.signing_payload) || SHA256(raw_frame)` from unrelated
device authentication traffic. The challenge is single-use and expires in 60
seconds. Frame validation enforces the 16 KiB parser boundary, exact org,
sender-device identity, trusted recipient, revocation epoch equality,
membership expiry and at most 300 seconds of remaining work-intent validity.

The signed envelope contains a *claim* about the payload hash, not the task
payload. Future artifact/PodSpec code MUST compare actual transmitted bytes
against `payload_sha256` and independently authorize the workspace,
classification, data locality, purpose, resources, tools and recipient.

## Failure and threat model

Denials: swapped work frame after signed challenge, cross-org sender/recipient
spoof, wrong worker or receiver identity, stale/future epoch, stale/overlong
expiry, forged signature, replay after a failed signature, unsupported fields
and malformed frame. The caller provides the authenticated recipient only
from trusted local context; the challenge contains pinned issuer/device keys.
Only the receiving-side verifier and the requester-side verified channel
may use these APIs. No caller-supplied `org_id` can establish membership.

## Tests and DoD

`tests/unit/test_org_work_binding.py` tests valid frame-evidence conversion,
negative authorization, tampering, foreign org, wrong recipient, replay,
expired challenge, forgery, unknown fields and wrong-epoch policy claims.
The unchanged `admit_remote_execution` stays fail-closed. Passing unit tests
do not prove secure transport, revocation distribution, secure host custody,
confidential computing or workload authorization. CI and independently
reviewed exact-head PR gates are necessary before merge.
