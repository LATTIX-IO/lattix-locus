# LOCUS-433 incremental action receipt and at-most-once dispatch barrier

Depends on [PR #86](https://github.com/LATTIX-IO/lattix-locus/pull/86),
which must merge first. Adds a **trusted in-process action broker primitive**
without launching pods or exposing callbacks to untrusted agents.

## Invariants and threat model

1. Only a currently admitted, unexpired and gateway-authenticated PodLease
   may prepare an intent; **PEP allow** (not ask/denied) is mandatory.
2. The signed gateway decision fingerprint and a caller-supplied bounded
   idempotency key bind the action. Only digests/audit IDs are stored.
3. \`prepared\` is a harmless durable intent; \`dispatching\` is written and
   committed **before** any external callback can run.
4. A restarted broker converts orphaned \`dispatching\` records to
   \`uncertain\`, never automatically invokes them again. A provider timeout
   or failure after dispatch follows the same rule.
5. Success creates a durable receipt with no raw response or secrets.
   \`reconciled\` records a trusted supervisor's **reference**, not independently
   verified success; it does not authorize replay.
6. Parent revocation, wrong pod, stale lease, or unsupported schema denies.
   All persistence uses the same SQLite file and scoped foreign-key relation
   to the existing admitted pod identity; the safety gate does not replace
   Locus Gateway.

## Test-driven acceptance

\`tests/unit/test_action_receipts.py\` covers unit semantics, 16-way concurrent
dispatch contention, idempotency mismatch, crash before vs after dispatch,
backend failure/timeout, PEP denial, forged lease, supervisor authority,
stale/revoked parent, sanitized receipt storage, schema mismatch, and
resource ceilings. Run alongside existing pod admission and gateway suites.

## Limitations and next increment

This primitive provides **at-most-once broker callback invocation per
action-id**, not exactly-once external effects across distributed providers.
A process crash in the gap after marking \`dispatching\` but before invoking the
provider leaves a possibly-unexecuted action in \`uncertain\`. A remote provider
may also commit and time out; the broker will not blindly retry. Provider-signed
receipts, effect-specific idempotency, host-side jailed executor, UI action
interlocks, strong verifier evidence, and Postgres/fleet parity are still needed
before claiming safe general computer-use or multi-device autonomy.

Rollback: disable invocation, preserve the durable receipt database and all
uncertain holds. Never delete or auto-mark \`uncertain\` as successful.

## Result fidelity

A successfully returned callback result is canonicalized as bounded JSON
(up to 1 MiB), then SHA-256 hashed. Equal output *types* with different
values produce distinct durable digests; raw output values are not persisted.
An unserializable or oversized result becomes **uncertain**, because its
external side effect may already have happened. A digest alone is not a
provider-signed result receipt and must not substitute for later verification.

## Independent security review follow-up

Supervisor identity is bound to the original registered parent session token
digest, not merely the publicly known run ID. The intent fingerprint is
restart-stable (derived from signed decision action facts and argument digest,
not the process-local Gateway approval HMAC). A shared-DB recovery scan is
**read-only**: it must not rewrite an active dispatch being executed by a
second broker. Such records are non-replayable even before an exclusive
process-liveness/fencing mechanism can safely establish orphan status.
Unexpected failures *returned by the callback* are explicitly stored
`uncertain` and may be recorded with a trusted supervisor evidence reference.
A genuine process-crash dispatch requires a subsequent exclusive/fenced
reconciliation workflow; this incremental module does not claim one.
