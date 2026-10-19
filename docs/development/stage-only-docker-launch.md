# LOCUS-466 — Stage-only Docker execution acceptance

Status: **new Linux-only StageDockerBroker, not a Codex worker or a product-wide execution fallback**.
The existing PodSandboxBroker ban on writable original host binds remains in force.

## Authenticated dispatch

1. A trusted supervisor binds an engine-pinned TaskPlan, admits the Gateway
   child PodLease, and captures a bounded workspace with `staged_workspace`.
2. `PodStageBindings.bind` seals the original workspace snapshot and the
   child/attempt/engine/capability identity into the shared SQLite ledger.
3. `StageDockerBroker.execute` checks the exact admitted tool/executable and
   stage workdir. It rejects children with any original-host writable Gateway
   scope, unsupported platforms, root supervisors, untrusted Docker binaries,
   non-local daemons, mutable OCI image tags, and unexpected stage mount aliases.
4. Before receipt creation, an independent second snapshot verifies both
   original source and staging copy still match the sealed manifest. The stage
   snapshot must not contain even excluded credential/control entries.
5. An outside-stage generation nonce is written into supervisor-only SQLite,
   bound to `statx` birthtime, mount ID, device/inode, trusted 0700 parent
   and private stage UID. It survives stage file writes without trusting the
   directory mtime/ctime. Unsupported kernel/FS witnesses fail closed.
6. The broker builds an immutable, fixed Docker command with one non-creating
   stage-only `--mount type=bind` (recursive submounts disabled), a pinned
   image digest, a local Unix socket, no network, dropped caps, no-new-privileges,
   seccomp, private IPC/PID/cgroup namespaces, read-only root filesystem,
   unprivileged host UID/GID, and bounded memory/CPU/PIDs.
7. Gateway prepares the process-exec action with the exact frozen launch
   fingerprint; StageLease links the one prepared receipt. The receipt's kernel
   lock and write-ahead `dispatching` state precede any Docker invocation.
8. Before actual subprocess spawn, the broker independently repeats the
   StageLease, original snapshot, generation and receipt checks. A safe empty
   Docker client config and explicit `unix://` host disable inherited
   contexts, cloud credentials and remote Docker routes.
9. Deadline and explicit cancellation attempt `docker kill`, and the action
   remains **uncertain/non-replayable** even if cleanup fails. Successful
   invocation reports only exit status/timing. No CLI output, source writeback,
   artifact approval or promotion occurs.

## Security and deployment boundaries

- Supported platform: unprivileged supervisor on Linux with working statx
  birthtime and mount-id support, trusted private 0700 staging parent, local
  Unix Docker Engine, kernel seccomp, and a daemon/image that honor recursive
  bind disable and user isolation. Unsupported cases deny; there is **no**
  host-process fallback.
- No writable original workspace mount is provided, ever. Staging sources,
  image, daemon endpoint, executable catalog and command are trusted inputs,
  not model-selected paths or Docker flags.
- A malicious same-UID process or root administrator that can modify the
  supervisor SQLite ledger, trusted Docker daemon, or image is outside the
  validated OS threat boundary. A private staging parent reduces pathname
  swap exposure; the kernel birth/mount witness catches inode recycling but
  does not attest a compromised daemon.
- A stopped client after an abrupt supervisor crash may leave an orphaned
  container. Durable `dispatching` receipt state prevents replay. Runtime
  restart cleanup/reconciliation and proven daemon teardown require
  independent acceptance; do not silently release reserved budgets.
- This broker is *not* the vendor CLI/MCP adapter, artifact promotion verifier,
  or multi-agent native-plus-Codex demonstration. Child subprocess output is
  intentionally suppressed. The user must separately approve promotion.
- Windows/macOS stay denied. Draft #107/#108 are not revived.
- PR #131 (read-only artifact inspection) remains a separate independent
  artifact preflight, not a dependency that grants writable host access.

## Required verification

Synthetic tests: `pytest -q tests/unit/test_stage_docker_launch.py`
(unit cases run only as an unprivileged Linux process).

Real acceptance uses the repo's **Stage-only Docker kernel acceptance**
workflow on an internal branch. It runs the unprivileged tests against the
local Engine with a digest-pinned, pre-pulled BusyBox image, then proves no
locus-named containers remain. Tests verify a successful stage write and
original-host byte/directory/permission integrity during success, timeout
and cancellation. A skipped real-Engine job is not acceptance. Reviewers must
also inspect latest-head CI/CodeQL and any skipped AppSec/SBOM gates.

Release-Impact: minor
