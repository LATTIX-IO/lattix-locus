Release-Impact: minor
Version: 0.5

Add the local-first transactional pod resource admission ledger with durable SQLite schema, bounded per-pod action/token/tool/time/spend reservations, parent-bound gateway sessions, and safe uncertainty on crashed attempts. No autonomous execution is enabled. The new persisted schema is opt-in for the supervisor; migrate no prior pod reservations. Safety-review hardening prevents budget-baseline reset and stores only digests of reconciliation evidence.
