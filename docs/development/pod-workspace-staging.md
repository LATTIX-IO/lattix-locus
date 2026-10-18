# LOCUS-433 — disposable pod workspace snapshot (stage-0)

Status: **supervisor staging primitive plus fail-closed Docker pod-broker hold; not yet a live writable Docker/CLI runner**.

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


## Immediate fail-closed broker policy

This PR also closes the known writable-host-bind hazard **on the trusted
PodSandboxBroker launch path** before the staged mount controller exists:
when the selected backend is HARDENED_DOCKER and the admitted child carries
any writable roots, the broker refuses execution *before* invoking a planner,
opening an action receipt or spawning a process. Docker read-only commands
remain available. Supported non-Docker sandbox tiers keep existing behavior.

This is an intentional compatibility restriction: Docker-backed coding tools
with writable host workspaces are **disabled** until a separate trusted
per-pod staging lifecycle, mount contract, and verified artifact promotion
are installed. No environment variable or model-supplied tool setting bypasses
this restriction. It is not a claim that all older SandboxManager callers or
harness entrypoints have been converted to PodSandboxBroker; LOCUS-432 must
audit and mediate those separately.

The negative test exercises the actual Gateway-backed broker setup and
verifies zero Docker spawns, zero durable action receipts and no creation of
the previously missing control paths. The prelaunch denial does not need a
Docker daemon to be evaluated; any future path that actually mounts staged
writable directories **does** require supported daemon/kernel tests before
the block is lifted. No container writable mount is introduced by this PR.


## Read-only host bind hardening

The broker's legacy Docker planner also emits host read-only binds with
Docker -v, which can create missing host source directories if another process
renames a checked source. After the existing independent **exact argv check**,
the trusted broker now rewrites verified read-only source mounts to
--mount type=bind,source=...,target=...,readonly,bind-recursive=readonly.
Missing sources, unsafe mount characters or unverified bind destinations are
refused before Gateway/receipts/launch. Only verified Docker launcher options
are rewritten, never user tool arguments following the pinned image.

Docker explicitly fails on a missing --mount bind source rather than creating
a directory as -v does. Explicit recursive read-only requires supported
Engine/kernel semantics; unsupported installations fail rather than
silently leaving nested submounts writable. This is a defense-in-depth
change to PodSandboxBroker; **SandboxManager direct callers remain separately
in scope for LOCUS-432**. CI tests validate rewritten argv and denial before
receipts, not a live daemon's mount behavior. A supported Docker Engine smoke
is still a prerequisite for declaring the full path release-ready.


## Staging ancestry and cleanup review gate

Only the trusted host default temporary directory is resolved to account for
macOS /var aliases. An explicitly selected staging parent must pass a
component-by-component O_NOFOLLOW directory-open check.

Textual containment is insufficient for case-insensitive filesystems and
bind aliases. Source and destination roots are pinned by open descriptors.
The entire destination ancestor chain is compared to the source directory's
device/inode identity before stage creation; the resulting stage is checked
against the pinned parent. Alias matches deny before source mutation.

Cleanup retries after restoring directory-owner access only *inside the
disposable stage*, never chmod-following symlinks or touching external
ancestors. This covers caller-modified 000/0500 permissions. Cleanup failures
are propagated, not counted as successful.

Threat boundary: privileged host daemons or hostile same-UID processes that
concurrently rename/rebind the staging parent after verification are not
handled by these path-based preflight controls. Executable pods require a
trusted staging root plus supported OS runtime isolation. No real Docker
daemon or macOS security attestation is claimed by this incremental PR.
