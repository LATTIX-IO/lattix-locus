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
- Each authorized Docker container bears a daemon-visible StageLease-derived
  proof label. The supervisor's durable launch record binds that proof to the
  exact image, stage identity, receipt/action and random container name. On
  recovery the controller independently compares labels, image, readonly
  rootfs, network, privilege and mount metadata; it can terminate **only**
  a matching container ID, then verifies daemon-reported absence.
- **Restart barrier:** Before resuming staged child dispatch, the trusted
  supervisor invokes `python -m locus_runtime.pod_stage_recovery --ledger /absolute/private/runs.db`.
  The directory must be supervisor-owned and mode 0700. A nonzero exit status
  means the service must stay fail-closed: 2 = manual review required;
  3 = an action lock is owned by a live broker. No application startup
  integration is implied by the CLI; the operator/service must install this
  barrier before enabling live child dispatch.
- The recovery process opens the same SQLite ledger, takes the same nonblocking
  kernel action fence, validates original StageLease/receipt audit seals and
  interrogates the explicitly local Docker daemon. A missing daemon, mismatched
  proof, unexpectedly mounted host path or failed container removal is always
  **needs-review**, never treated as successful cleanup.
- Every recovered or uncertain action remains non-replayable and its reservation
  remains held. A daemon report of no matching container is recorded as
  **absent-observed**, not as proof that a late request from a still-active
  Docker client can never appear. The original stage is neither removed nor
  promoted. Separate operator-approved completion remains necessary.
- **Residual risk:** A killed supervisor does not automatically kill its Docker
  CLI child; an in-flight daemon RPC may create a container after an initial
  no-container observation. The recovery CLI is a startup barrier and
  observation, not a continuously running guardian. Integrate a durable
  on-host watchdog or daemon-backed create/start lifecycle and prove delayed
  creation and restart races before asserting unattended crash containment.
  Real SIGKILL tests are necessary but alone cannot eliminate this gap.
- This broker is *not* the vendor CLI/MCP adapter, artifact promotion verifier,
  or multi-agent native-plus-Codex demonstration. Child subprocess output is
  intentionally suppressed. The user must separately approve promotion.
- Windows/macOS stay denied. Draft #107/#108 are not revived.
- PR #131 (read-only artifact inspection) remains a separate independent
  artifact preflight, not a dependency that grants writable host access.

## Required verification

Synthetic tests: `pytest -q tests/unit/test_stage_docker_launch.py tests/unit/test_stage_docker_recovery.py`
(unit cases run only as an unprivileged Linux process).

Real acceptance uses the repo's **Stage-only Docker kernel acceptance**
workflow on an internal branch. It runs the unprivileged tests against the
local Engine with a digest-pinned, pre-pulled BusyBox image, then proves no
locus-named containers remain. Tests verify a successful stage write and
original-host byte/directory/permission integrity during success, timeout
cancellation, revocation and a real forced supervisor SIGKILL followed by a
fresh-process reconciliation. A skipped real-Engine job is not acceptance. Reviewers must
also inspect latest-head CI/CodeQL and any skipped AppSec/SBOM gates.

Release-Impact: minor
