# LOCUS-433 — disposable pod workspace snapshot (stage-0)

Status: **supervisor staging primitive, not yet a Docker/CLI runner**.

## Scope

The existing Docker broker directly mounts principal-approved writable host
workspace directories. PR #107 correctly identified nested control paths, but
its read-only tmpfs masks for initially absent paths can cause the Docker
runtime to create directories on the *original host bind source*. PR #107 and
dependent seccomp PR #108 must remain held pending their own review.

This independent increment builds a disposable, supervisor-owned filesystem
snapshot **outside the original workspace** with an initial-state SHA-256
manifest digest. Work performed on this snapshot cannot write the original
path unless an independent execution component mistakenly reintroduces a host
writable bind. This module deliberately does NOT mount, run, or authorize a
pod. No output from this stage is automatically promoted.

## Security invariants

1. Source files are opened via anchored directory descriptors and O_NOFOLLOW
   per component, then inode/device and modification metadata are checked.
   Race or inconsistency rejects the snapshot and removes partial outputs.
2. Symlinks, hardlinked files, special files, case-aliased sibling names and
   unsafe mount-option characters are rejected rather than followed.
3. The .git, .locus, SSH/GPG/cloud/Kube directories (at every nesting level),
   .config/gcloud, and common secret-key paths are excluded. They cannot be
   copied or indirectly promoted as part of the initial snapshot.
4. Bounds default to 4,096 files, 64 MiB copied and nesting depth 24. The
   stage directory and regular files are owner-only (0700 / 0600); executable
   files remain executable only by the snapshot owner.
5. The disposable snapshot uses a separate source root and destination root.
   Creating the stage inside the original workspace is refused. Success and
   error paths remove the temporary stage. The source is never modified.
6. Hosts without anchored O_NOFOLLOW directory support are denied; in
   particular this is NOT ready for Windows. A Windows handle-based staging
   backend must preserve equivalent no-reparse semantics before acceptance.

The snapshot digest commits to its initial paths and contents. It is **not a
signed attestation** and does not certify a subsequent subprocess, isolation
mechanism or final generated artifacts. There is deliberately no writable
host-bind promotion or automatic checkout update.

## Integration gate still required

A separate trusted sandbox-broker increment must atomically bind the trusted
snapshot identity to a child PodLease, OCI image and scoped, independently
validated mount contract. Its sandbox must never mount the original workspace
writable. A supervised and independently verified artifact promotion operation
must be introduced before task outputs affect authoritative user files.

Runtime acceptance additionally requires real Docker Engine/kernel smoke
for absent protected mounts, nested submounts, cancellation and cleanup, plus
Windows/macOS host-specific validation. Unit tests here exercise only the
staging filesystem itself. No production Docker control is claimed.

## Developer check

    pytest -q tests/unit/test_pod_workspace_staging.py

Release-Impact: patch
