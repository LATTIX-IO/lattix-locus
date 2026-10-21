# LOCUS-469 / detached terminal artifact escrow (Linux)

This is a bounded **single-file, local-machine** artifact lifecycle. It is not
a Docker source-workspace mount, a multi-file transaction, or a distributed
agent-fabric feature.

## Lifecycle

1. While the exact authenticated `PodLease` is live, call
   `DetachedArtifactEscrow(stages).seal(lease, stage_id,
   allowed_output_paths=frozenset({"relative/file.txt"}))`.
   Only a successful independently verified terminal Docker receipt and
   manifest with one allowed regular file can create an escrow. The supervisor
   copies it using anchored no-follow descriptors, verifies file SHA-256,
   permissions and source revision again, fsyncs the private copy, and records
   stage, plan, pinned engine, child, parent, receipt and immutable manifest
   evidence in the private SQLite/WAL.
2. Settle/close the child and dispose of its writable stage. No later API
   reopens a child Gateway session or requires a live `PodLease`.
3. A distinct authenticated operator session calls `review(operator,
   escrow_id)`. Locus verifies the escrow file's **actual inode**, owner and
   SHA-256, recomputes the original source-workspace snapshot, rechecks parent
   principal and revocation, and authorizes a bounded file-read action. For
   small UTF-8 files the preview is a masked unified diff; binary or large
   files return hashes/modes without rendering raw bytes.
4. The operator approves this **exact** escrow manifest via the existing
   Gateway `ApprovalLedger`. `approve(operator, escrow_id)` requires the
   exact allowed, human-approved decision. Approval is one-use, expires within
   30–3600 seconds, and never writes the original.
5. `promote(operator, escrow_id)` obtains an independent current Gateway
   file-write authorization, re-attests source snapshot and file inode,
   then commits durable `promoting` intent. The prepared output is fsynced
   in the private escrow dir and its dev/inode sealed before atomic Linux
   `renameat2`: `RENAME_NOREPLACE` for additions and `RENAME_EXCHANGE`
   for modifications. No source-directory write occurs until the single
   atomic exchange/rename. Existing-file exchange retains the displaced
   original inode in private escrow until committed-state cleanup.

## Recovery, expiry and evidence

`python scripts/reconcile-artifact-escrow.py --ledger /absolute/private/runs.db`
performs bounded supervisor-side reconciliation; this command should run
at **native service startup and periodically under the same operator**. It
does not launch agents or perform an approval or reissue a promotion. An
interrupted promotion may only restore a file if the actual inode and SHA-256
match the durable prepared witness; foreign files, tampered names, unexpected
mounts and unverifiable races remain `uncertain`. A crashed post-commit
cleanup removes only the verified displaced original inode.

Escrow retention defaults to one day (range one minute to seven days).
Expired eligible records are transitioned and their private payload bytes
removed only after bounded inode/content verification. Unexpected entries
are left for operator review; never recursively delete by a guessed path.

The private files use a 0700 root/attempt directory and 0400 regular-file
payloads. This excludes a confined child but is **not a hardware-backed
immutability guarantee against a compromised same-UID supervisor**.

## Unsupported operations

Multiple file changes in one atomic publish, deletions, new source ancestor
directories, Windows/macOS, nested source mounts, direct worker writes to
original source, silent model approval, offline/missing Gateway policy and
remote/different-filesystem publication are not enabled. A failed/ambiguous
Docker action is ineligible for escrow. Application exit code must be zero
for this V1 verification contract.

Real Linux kernel acceptance includes the actual stage-only Docker worker,
systemd guardian restart, child closure, fresh human approval, direct source
integrity checks, process SIGKILL before/after promotion, exact-inode rollback,
expiry and replay denial. Standard AppSec/pentest and SBOM/SCA skips do not
count as successful independent assurance.
