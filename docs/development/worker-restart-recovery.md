# LOCUS-413 — restart-safe Gateway worker driver

## Implementation boundary

This is a follow-on to the durable DAG scheduler and multi-agent wave
dispatcher. It connects `DurableTaskScheduler.recover_unstarted` to the
same `GatewayTaskDriver` and **does not** introduce a second orchestrator,
policy engine, executable launcher or inference pathway.

A trusted supervisor may now call:

```python
driver.run_until_idle(accepted_plan, authenticated_parent)
```

The driver defaults to safe unstarted recovery on each bounded wave:

1. Authenticate the original parent and confirm the immutable plan,
   graph fingerprint and durable concurrency ceiling.
2. Reconstruct `claimed` attempts with no durable reservation under their
   **original** idempotency keys.
3. Restore `reserved` holds that were committed before a scheduler crash,
   without allocating another hold, then issue only the original Gateway
   child admission (exactly once).
4. Requeue previously canceled, definitively unadmitted holds with bounded
   incremented attempt identities as defined by the scheduler.
5. If a recovered reservation has no approved engine capable of the required
   tools, retain the hold and return `uncertain`. Do not silently cancel
   or substitute an unregistered engine.
6. Preserve ambiguous, admitted, active, closed and uncertain child attempts
   for operator reconciliation. No duplicate child launch and no implicit
   approval, side effect replay, capability widening, or budget credit.

`run_wave(..., recover_unstarted=True)` makes recovery explicit for direct
wave callers; the default for a direct `run_wave` is false. The bounded
`run_until_idle` default is true. The recovery option must be a real bool;
truthy strings or agent-controlled payloads are rejected.

## Crash/recovery protocol

A restart coordinator should first invoke the reservation ledger's existing
`recover_expired()` to classify expired reservations; **this driver does not
globally sweep other principals' reservations**. Then run the new driver,
using the exact original authenticated Gateway parent and compiled plan.

No operation may consider a worker's model output, arbitrary artifact bytes,
an expired lease, or a closed pod sufficient to unlock descendants. The
independent verifier remains authoritative. A worker exception or a verifier
outage never reactivates a potentially effectful child.

Explicit recovery does not replace OS-level process lifecycle controls:
adapter kill trees, tool sandbox attestation, MCP/broker mediation, token/cost
accounting, durable checkpoints while a worker is actually running, device
computer-use leases, live OpenCode/Ollama smoke and dynamic plan revisions
remain follow-on scope.

## TDD and checks

`pytest -q tests/unit/test_task_scheduler.py tests/unit/test_task_dispatch_driver.py tests/unit/test_task_dispatch_recovery.py`

Synthetic fault fixtures simulate:

- committed claim whose `NodeClaim` return was lost;
- reservation committed while scheduler state still says claimed;
- canceled, definitively unadmitted reservation that needs a fresh key;
- child admitted before supervisor state commit (never reissued);
- recovered hold with temporarily unavailable trusted worker;
- worker interruption and irreversible side-effect uncertainty;
- two concurrent recovering supervisors competing for one admission;
- invalid recovery flag and cancellation before recovery.

All tests use real SQLite admission, Gateway child sessions, and a trusted
fake worker. These are *not* a substitute for live browser/computer or
OpenCode/Ollama process smoke. CI needs exact-head backend unit,
Ruff/mypy/policy/Helm, frontend, Windows sandbox, integration, performance,
DAST and required-gates evidence; disabled CodeQL/AppSec/SBOM must be
reported NOT EXECUTED.

## Rollback

Do not enable this optional recovery flag outside the trusted driver.
To roll back, stop invoking recovery and preserve existing `task_dispatch_*`
and `pod_attempt` state. Never delete active/uncertain attempts to release
budgets. Reconcile them against broker/action receipts first.
