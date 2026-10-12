# LOCUS-433 — gateway and durable receipt binding to the administrator catalog

This increment binds the *actual immutable administrator catalog revision* to an existing native `process_exec` authorization and durable action receipt. It does not create an executor, introduce a second authorization plane, or enable raw tool output.

## Contract

The trusted `SandboxedPodToolBroker` passes its existing canonical 64-character SHA-256 catalog revision identifier through the new optional `BrokerCommand.catalog_digest` field. Direct legacy `PodSandboxBroker` callers may omit the field; the broker validates any supplied value as canonical lowercase hexadecimal.

At the existing `PodSandboxBroker.execute` authorization point, catalog identity enters the `process_exec` gateway action as `args={"catalog_digest": digest}`. The gateway's action fingerprint commits to its canonical args digest. The existing `PodActionReceipts.prepare` implementation also HMAC-binds the full canonical authorization request, using the child session's run-scoped secret. No new credential, model-provided argument, or user-controlled path is accepted.

A replay using the same durable idempotency key and command but a **different catalog revision** is rejected as a different action by the existing receipt ledger; an ordinary retry of the same action remains non-replayable after dispatch. The catalog identifier is not an executable signature, proof of its mounted identity, or verification of OS sandboxing. Those enforcement steps remain independently required.

## Tests and acceptance

- Authorized registered execution includes the trusted catalog digest in gateway authorization, with no raw argv in the newly added `args` structure.
- Durable receipt fingerprint is populated, and full command output remains suppressed.
- The same command under a distinct administrator catalog revision cannot reuse an existing idempotency key; there is only one external subprocess invocation.
- The prior same-revision replay restriction remains.
- Malformed, uppercase, non-hex, wrong-length, and non-string identifiers reject command construction.
- Direct trusted calls omitting a catalog identity preserve compatibility with preexisting sandbox broker callers.
- All existing gateway, receipt, OS-fencing, canonical path, digest, strict catalog, Windows sandbox, DAST, integration and unit suites must remain successful.

## Limits

A digest without an administrator signature is a correlation value and an action-identity input, not executable attestation. A malicious privileged control-plane process could construct direct broker commands without a catalog identity, and this increment does not close that trust boundary. This is not a race-free binary identity mechanism. No changes to subprocess lifecycle, stderr/stdout or sandbox policy are made.
