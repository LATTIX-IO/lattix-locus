# LOCUS-437 — durable, revision-bound child engine admission

This increment seals the already-reviewed `TrustedEnginePins` node mapping
into the durable per-parent SQLite task scheduler *before any claim*.

- First binding uses `BEGIN IMMEDIATE`, requires the complete unchanged graph
  to be pending and persists deterministic sorted node-to-engine bindings.
- Subsequent supervisors can resume only with the identical mapping, including
  after SQLite recovery and re-opening the same parent run.
- The scheduler checks parent and per-node bindings on every graph operation.
  `admit()` independently denies a mismatched child engine before forwarding
  to `PodReservationLedger.admit`, even if a caller bypasses the driver.
- A legacy unpinned dispatcher is denied for a sealed run, preventing
  registration-order fallback in that run.
- Existing, never-sealed internal DAGs retain prior behavior for compatibility.

**Security scope:** The trusted supervisor supplies the bindings; untrusted
models cannot set them. SQLite durability detects accidental or single-record
mutation, but is not a cryptographic signing system and does not protect
against a malicious host administrator modifying both records. The attached
engine ID does not authenticate a CLI executable or an operating-system
sandbox. Under the current implementation, the scheduler admits children
through the existing Gateway, but the worker remains a trusted adapter.

**Production blockers:** Before executable Codex/native mixed-pod acceptance,
require principal-bound review revisions, immutable registered executable
digest, confined process lifecycle, cancellation and reaping, per-pod MCP
receipts and tools, and the Docker #107/#108 host-mount remediation with
live Engine tests. This increment does not enable TaskPlan launch.

Regression tests intentionally use real Gateway child sessions and the actual
SQLite ledger, with synthetic workers. They verify restart/rebinding, changed
engine refusal, direct child-admission fencing, unpinned route denial, and
tampered ledger records. No Docker or vendor CLI execution is claimed.

Release-Impact: patch

## Review correction: atomic preflight-to-claim fencing

An independent reviewer identified a TOCTOU if an unpinned legacy supervisor
passed a preliminary check before a concurrently pinned supervisor committed
its mapping. The subsequent unpinned claim could hold capacity or pick the
same engine by coincidence.

The claim and recovery transactions now accept the caller's expected mapping,
checking it **under the exact BEGIN IMMEDIATE transaction** used to claim or
reconcile attempts. A sealed graph refuses unpinned claims, while an unsealed
graph refuses claims asserting nonexistent pins. This closes the check/claim
race without depending on the caller's earlier preflight. The authoritative
child admission also retains its per-node engine match.
