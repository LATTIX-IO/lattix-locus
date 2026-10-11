# LOCUS-433 — kernel-fenced local dispatch recovery

This increment is layered on LOCUS-433's merged reservation ledger (PR #86)
and the action receipt barrier (PR #87). It provides a **local-host-only**
orphan recovery controller and no new executor or external authority.

## Safety contract

A trusted supervisor's \`execute_once\` acquires a **per-action, kernel-held
nonblocking lock** before the durable \`dispatching\` transaction, and holds
that lock through the entire callback and terminal receipt transaction.
A protected dispatch writes \`fence_guarded\` in the *same SQLite
transaction* as \`dispatching\`. A legacy or manually started dispatch
without this marker **cannot be automatically reconciled**.

The recovery controller authenticates the original delegating supervisor,
matches its random token digest against the persisted action owner and
checks its parent run identity. Only then does it try to acquire the
particular action's OS lock. Failure means a live holder exists — **skip**.
Success proves the kernel released the previous owner; the controller
rechecks persisted action state and marker in \`BEGIN IMMEDIATE\` and changes
only a still-dispatching, guarded record to **uncertain**. It does not
mark success, run callbacks, release parent funds or generate replay
permissions. If a prior callback already closed successfully, the
controller leaves that terminal record intact.

POSIX uses \`flock(LOCK_EX|LOCK_NB)\`; Windows uses a one-byte
\`msvcrt.locking(LK_NBLCK)\` lock. Per-action files live in a private
local directory with hashed action IDs and no caller-supplied paths.
Independent action locks permit concurrent parallel pods; a shared
global lock would have destroyed task parallelism.

## Unit / regression / adversarial verification

\`tests/unit/test_fenced_recovery.py\` verifies live callbacks never
get reclaimed, kernel release after killed subprocess, two parallel
callbacks, replay prevention, legacy unfenced rejection, same-run-ID
foreign Gateway impersonation refusal, nondelegating supervision refusal,
cross-tenant action access denial, malformed IDs and oversized batches,
private-directory permission enforcement, and no raw credential leakage.
Existing pod-budget, action-receipt, gateway and cross-platform CI suites
remain mandatory. Run the Linux and Windows job matrices; do not rely
only on mocked locks for this trust boundary.

## Explicit limitations

- This is **not** distributed fencing. Shared storage across hosts or
  copied databases is not supported: the kernel lock only proves local
  process liveness. Hosted or multi-device deployments must use a
  consensus/fenced coordination service.
- Python's trusted supervisor and its raw GatewaySession must not be
  exposed to an untrusted agent. Callback execution still requires
  separately specified sandbox/IPC isolation.
- Original Gateway sessions are process-local. Full system-process
  restart cannot automatically reauthenticate an absent supervisor;
  such orphan actions remain blocked until an independent secure
  recovery identity service is implemented.
- A dispatch marked \`uncertain\` may or may not have reached the external
  provider. It requires authoritative external receipt reconciliation;
  **never** auto-retry or mark it completed based on OS lock ownership.
- Directory protection relies on the trusted OS user and locally
  controlled application-data path; symlinked and world-accessible
  POSIX lock directories fail closed.
