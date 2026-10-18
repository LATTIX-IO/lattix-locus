# LOCUS-465/A — Durable StageLease identity (no execution)

Status: **contract-only, additive SQLite metadata and explicit receipt association**.

## Delivery boundary

This increment makes a supervisor-owned disposable workspace snapshot
addressable as one immutable, verified-at-bind **StageLease** for one active
PodAttempt. It uses the reservation ledger's existing SQLite WAL and
BEGIN IMMEDIATE transaction authority. It does not launch Docker, expand
Gateway scopes, authorize subprocess tools, save agent outputs, verify
artifacts, promote a git change or enable host workspace writes.

Dependencies: PR #129 provides the source snapshot creator and denies
HARDENED_DOCKER children with original-host writable roots at the
PodSandboxBroker. PR #127/#128 provides durable node-to-engine pins.
Neither invariant is relaxed.

## Binding sequence

1. Supervisor binds the exact TaskPlan into DurableTaskScheduler and seals the
   full node-to-engine mapping **before claiming anything**.
2. Gateway authorizes the specific parent, reserves a bounded PodAttempt and
   creates its attenuated child session. Scheduler marks that node active.
3. Trusted supervisor creates a disposable stage via
   pod_workspace_staging.staged_workspace(source, staging_parent=...).
   The context manager must remain open during StageLease use.
4. PodStageBindings.bind(lease, snapshot) requires the same active
   reservation ledger and supervisor-bound PodActionReceipts. Under one
   BEGIN IMMEDIATE transaction it rechecks live parent and child identity,
   parent plan revision, exact node attempt key and reservation, complete
   scheduler engine pin set, current child engine, Gateway-derived *immutable capability limits* fingerprint (not volatile usage counters), initial source manifest, and stage filesystem ownership,
   inode identity and outside-source ancestry. It persists one unique
   stage/reservation mapping. A duplicate exact bind returns the same record;
   attempts to substitute a different stage or reuse the stage for another
   pod are denied.
5. A separately prepared, Gateway-authorized PodActionReceipt may be linked
   via link_prepared_receipt, but only when it is in prepared state, belongs
   to this reservation/child, and matches the trusted supervising principal
   token digest. This link is durable and one-use; it does *not* dispatch
   the action or provide tool authorization by itself.
6. inspect rechecks active child and stage identity. retire marks the binding
   unusable without deleting or promoting stage contents. The snapshot
   context manager remains responsible for temporary-directory cleanup.

## Version 1 record

The isolated pod_stage_schema table uses version=1 and refuses unknown
versions. pod_stage_binding contains a random stage identifier, exact
reservation, parent run, child run, task node/attempt key, plan digest,
PodSpec revision, pinned engine, initial snapshot SHA-256, Gateway scope
SHA-256, canonical binding digest, source/staged paths, source and stage
device/inode identity, lifecycle state, optional prepared action receipt
reference, and timestamp. The reservation and action each have UNIQUE bindings;
no executable grant is recorded or inferred from the row.

The audit table records only stage ID, lifecycle event and timestamp.
Device/inode identity is additionally fenced by the stage root's admission-time ctime/mtime to detect immediate inode reuse at the same path; only still-existing historical stage paths block parallel sharing of the same live inode. StageLease objects are not bearer capabilities. The on-disk database contains
local source/staging path metadata; protect the SQLite file under the
supervisor's operating-system ACL. No raw model prompts, tool arguments,
credentials or staged file contents are stored here.

## Explicit release hold

- The accepted snapshot is created by trusted supervisor code. This is
  **not** an independent content rehash of a caller-supplied forged
  WorkspaceSnapshot object and must not be exposed as an untrusted RPC
  payload. A later launch worker must independently attest the stage's exact
  contents and immutable workspace state at dispatch.
- Stable inode checks guard absent, renamed, symlinked or reused stage paths;
  they do not prevent a privileged daemon or hostile same-UID host process
  from mutating directory *contents* after inspection. Container launch must
  own and enforce its OS-level mount boundary.
- A prepared action receipt is *not* a launch permit. Before any subprocess,
  the next broker increment must atomically verify the stage, authenticated
  receipt, immutable command and PodLease while entering the existing
  dispatch fence and verify a real Docker/kernel sandbox. Do not infer
  this from the present in-process tests.
- A writable stage capability is not granted here: a child with any
  writable Gateway host roots is denied at StageLease bind. Per-stage-only
  scopes and verified artifact promotion require subsequent independent
  approval.
- No automatic retry of any admitted/uncertain attempt, and no auto-promotion
  to the original workspace. Crash cleanup and orphan reaper safety remain
  separate LOCUS-465 work. Windows remains unsupported until no-reparse
  staging meets equivalent requirements. Draft PRs #107/#108 remain held.

## Regression check

    pytest -q tests/unit/test_pod_stage_bindings.py
    pytest -q tests/unit/test_pod_workspace_staging.py
    pytest -q tests/unit/test_task_engine_selection.py
    pytest -q tests/unit/test_pod_sandbox_broker.py

Expected adversarial cases: unsealed/stale DAG, mutated engine pin, foreign
sibling child, stale/expired reservation, reused stage inode, removed/renamed
stage path, replaced snapshot, missing/symlinked stage, duplicate receipt,
previously dispatched action, concurrent competing binders, unexpected
schema version and revoked child.

Release-Impact: patch
