# LOCUS-415 / LOCUS-437: exact-revision trusted pod engine pins

Status: **incremental integration contract**, not production native/CLI execution.

## Problem

The durable Gateway task driver already admits parallel child PodLeases but
chooses the first registered engine advertising a node's requested tools.
That is appropriate for earlier internal tests; it cannot safely implement
a principal's explicit Codex, Claude, OpenCode or native engine choice.
A different engine may happen to support the same tool set, and provider
fallback must never widen the model/data boundary implicitly.

## This increment

`TrustedEnginePins` is a frozen control-plane-only mapping of every DAG
node to one pre-registered engine ID, bound to the exact `CompiledTaskPlan.digest`.
The driver preflights the full node set and advertised capabilities **before**
the first claim, reservation or Gateway child session. Unknown engines,
stale plans, missing/extra node assignments and incompatible capability
registrations fail closed. During execution, a pinned adapter failure leaves
its existing attempt uncertain; the driver does not invoke a replacement
engine or retry side effects.

No model-produced plan field, chat message, HTTP payload, untrusted skill or
MCP response is allowed to become `TrustedEnginePins`. The trusted supervisor
must resolve principal-approved engine policy and current engine/catalog
version, and construct the mapping itself. The mapping is *not* signed,
persisted, or an authorization grant; the existing Gateway session, pod
reservation, action receipts, isolation backend, and independent verifier
retain exclusive authority. Unpinned existing callers preserve their
prior internal deterministic registry-order selection for compatibility.

## Synthetic proof (not a Codex smoke)

`tests/unit/test_task_engine_selection.py` uses the **real** durable SQLite
scheduler and Gateway child leases, with synthetic trusted-worker adapters
named `native/deep-agents` and `cli/codex`. Two sibling pods execute in
parallel under separate child identities; a downstream node only runs after
independent verification. Tests cover exact node pinning, no adapter fallback
after failure, revision mismatch, capability mismatch, and zero budget or
child allocation on denied preflight.

These adapters intentionally do not spawn Deep Agents, Codex CLI or a sandbox.
A fake adapter passing this test is not an execution security attestation.

## Follow-on mandatory gates

1. LOCUS-432/433 and #107/#108: close Docker host-target creation risk, audit
   confinement, and execute real Docker/kernel validation before live CLI.
2. LOCUS-415: wire every real entrypoint to the admitted, durable runtime.
3. LOCUS-437: bind immutable PodSpec / PodAttempt identity, local isolated
   workspace, lifecycle cancellation and bounded teardown to each lease.
4. LOCUS-416: install a real Codex adapter using the same child lease and
   receipt-fenced MCP transport (not the legacy independently opened session).
5. LOCUS-459/462/463: persist human-reviewed engine choices under a signed,
   expiring plan revision; revalidate identity, policy and catalog at launch.
6. Live Windows/macOS Codex and native two-child acceptance including denied
   egress, tool bypass, kill-tree, artifact authenticity and rollback.

Rollback: omit `engine_pins` from internal driver construction, reverting
to the existing behavior. Do not use this fallback in a principal-selected
production path; a real user-selected engine requires immutable policy pins.
