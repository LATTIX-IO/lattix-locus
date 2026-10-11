# LOCUS-433 — transactional pod admission and budget reservation, increment 1

Status: initial durable **single-device** admission service; no general agent execution.

## Contract and authority

\`locus_runtime.pod_reservations\` provides \`PodReservationLedger\`,
\`ResourceBudget\`, \`ReservationRequest\`, \`ResourceReservation\`,
\`LedgerSnapshot\` and \`PodLease\`.

The **authenticated supervising GatewaySession** and trusted
\`CompiledTaskPlan\` bind the parent run, principal, area, plan digest, node
and risk/tool ceilings. The model is never permitted to claim its own
identity, budget pool, parent session or authority. A supervisor must have
\`allow_child_delegation=True\` at session registration. A child session is
created through the **existing gateway PEP** only after a reservation is
persisted; its authority is derived from the node and parent registered
capabilities and every child has bounded actions, tools, tokens, execution
duration, spend and R0–R3 risk.

SQLite \`BEGIN IMMEDIATE\` guarantees serialized budget reservations across
threads/processes sharing the same local database. Parent snapshots calculate
**used + remaining held** reservations atomically; a cumulative usage
settlement moves tokens/spend from held to used without double charge. All
budgets are integers, currency is in **USD micro-units** internally; the
gateway's legacy floating USD input is only an advisory limit and is not the
transaction ledger. A zero-spend offline child is valid.

## Lifecycle / crash safety

\`reserved → admitting → active → closed\` on success.
\`reserved → canceled\` safely releases an unused reservation.
An expired \`admitting\` or \`active\` attempt becomes **uncertain**, retaining
its unspent hold. An explicit evidence-bearing final reconciliation settles
consumption and releases unused capacity. No externally irreversible
action is automatically retried, reissued or considered complete. An
idempotency collision on parent + attempt key returns the **same record** for
exactly the same specification; differing data is denied.

The \`PodLease\` wrapper checks the durable DB before each gateway call and
requires an active, nonexpired reservation. Parent revocation blocks future
admissions and invalidates leases. Each admission, usage settlement and
state transition produces a durable local event without recording secrets.

## TDD / test matrix

\`\`\`sh
python -m pytest -q tests/unit/test_pod_reservations.py
\`\`\`

Covers a 30-thread resource contention race using independent SQLite
connections, oversubscription, idempotent retries, plan mismatches,
pre-launch restart, expired execution uncertainty, explicit reconciliation,
revocation, cumulative spend, budget denial and schema-version fail-closed.

## Deliberately deferred

This increment is **not** an executable scheduler or secure cross-process
agent isolation. The PodLease is an **in-process trusted supervisor object**;
do not pass the underlying GatewaySession, its caller token, or Python
object internals to an untrusted worker. Process jail/IPC mediation, actual
side-effect action receipts, atomic shared-budget updates across a remote
database, gateway-session recovery across interpreter restart, engine
interoperability, multi-tenant federation and independent artifact
verification are still tracked by LOCUS-415, LOCUS-413, LOCUS-432 and
subsequent LOCUS-433 stories. An uncertain side effect requires human or
tool-provided evidence before retry. SQLite file must be protected with
the platform's local application data ACL. All policies remain fail-closed.

## Definition of Done for this incremental PR

- SQLite WAL transaction and schema v1 with fail-closed migration versioning;
- no simultaneous budget overcommit in deterministic concurrent tests;
- monotone usage, explicit terminal evidence and restart-safe uncertainty;
- child session bound to committed reserved plan and registered gateway
  identity, never to prompt-provided authority;
- all affected tests, build and CI gates pass and security review complete;
- documented recovery and rollback (disable admission, retain ledger until
  uncertain side-effects are reconciled; do not truncate records).

## Security review hardening

A running supervisor may already have consumed tokens, time or cost. A child
inherits these gateway absolute-used baselines and receives only its requested
increment as headroom; its **durable** usage remains relative to its own
reservation. If a parent cannot accommodate that increment, gateway admission
fails closed and releases the unlaunched hold. Evidence is committed as a
SHA-256 fingerprint in both the attempt and audit records: **no raw evidence,
authorization headers or PII** are written by the reconciliation path.
