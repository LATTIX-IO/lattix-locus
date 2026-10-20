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
   remains **uncertain/non-replayable** even if cleanup fails. Completed
   application runs report only exact exit code and timing. An application
   nonzero exit (for example a failed test suite with code 7) is a completed
   tool invocation with `StageDockerOutcome.exit_code=7` and a durable
   `succeeded` **dispatch receipt**, not a claim of task/test success.
   The caller/independent verifier must evaluate that exit code.
   Docker-reserved status **125/126/127** remains uncertain: a daemon failure,
   uninvokable entrypoint or missing executable cannot be distinguished
   reliably from an application deliberately returning one of those codes.
   No CLI output, source writeback, artifact approval or promotion occurs.

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
  3 = an action lock is owned by a live broker or a newly prepared intent is
  inside its launch grace window.
- **Mandatory OS-supervised guardian (LOCUS-467):** New `StageDockerBroker`
  admission requires a fresh, `ready` guardian registered to its **same**
  supervisor-private SQLite ledger and local Docker socket. Every check
  independently reattests the Linux boot ID, process creation tick, PID,
  systemd `INVOCATION_ID` and `NOTIFY_SOCKET`, ledger inode, Docker socket
  inode and a monotonic heartbeat younger than five seconds. The exact
  guardian epoch is sealed into the launch authorization and rechecked
  immediately before Docker process creation and throughout execution.
  Absence, guardian death, daemon outage, PID reuse, stale/blocked heartbeat
  or epoch swap fails closed and cancels live pods. There is no environment
  variable permitting production bypass.
- **Linux user-service installation:** The operator must install the same
  pinned Python environment and existing ledger with
  `python scripts/install-stage-guardian.py --ledger /absolute/private/runs.db`.
  The installer validates private mode 0700 state, writes mode 0600 systemd
  config, enables `locus-stage-guardian.service` with `Type=notify`,
  `WatchdogSec=10s` and `Restart=always`, and verifies a live registered
  heartbeat before returning success. On hosts without an active
  `systemctl --user` service manager, installation fails closed; for
  persistent operation beyond login, the operator must configure
  `loginctl enable-linger` with authorized host administration. No Docker
  child receives access to the guardian or its private ledger.
- The separate watcher repeatedly checks the Docker daemon itself and
  reconciles uncertain launches. It records `blocked` whenever the daemon
  is unavailable or an unexpected container requires manual review;
  a prior `absent-observed` is never treated as final removal evidence.
  The watcher respects the action OS fence and a 10-second prepared-receipt
  grace to avoid preempting live broker preparation.
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
- **Residual risk and rollout:** A killed supervisor does not synchronously
  kill its Docker daemon RPC, and a container can briefly exist while the
  independent guardian restarts or is between polls. Continual scans and a
  mandatory health check bound the risk during supported operation, but
  there is no proof of instantaneous teardown. The native UI/service must
  be configured against the same specific ledger/daemon and pass full real
  systemd-restart plus delayed-create tests before promoting this worker
  into default production operation. Absent that host qualification, the
  worker remains unavailable; no fallback to a writable original bind.
  Logical replay and premature budget release always remain denied.
- This broker is *not* the vendor CLI/MCP adapter, artifact promotion verifier,
  or multi-agent native-plus-Codex demonstration. Child subprocess output is
  intentionally suppressed. The user must separately approve promotion.
- Windows/macOS stay denied. Draft #107/#108 are not revived.
- PR #131 (read-only artifact inspection) remains a separate independent
  artifact preflight, not a dependency that grants writable host access.

## Required verification

Synthetic tests: `pytest -q tests/unit/test_stage_docker_launch.py tests/unit/test_stage_docker_recovery.py tests/unit/test_stage_guardian_admission.py`
(unit cases run only as an unprivileged Linux process).

Real acceptance uses the repo's **Stage-only Docker kernel acceptance**
workflow on an internal branch. It runs the unprivileged tests against the
local Engine with a digest-pinned, pre-pulled BusyBox image, then proves no
locus-named containers remain. Tests verify a successful stage write and
original-host byte/directory/permission integrity during success, timeout
cancellation, revocation and a real forced supervisor SIGKILL followed by a
fresh-process reconciliation, independent watchdog cleanup, watchdog-death
admission denial, and real systemd-managed watchdog restart. A skipped real-
Engine or systemd job is not acceptance. Reviewers must
also inspect latest-head CI/CodeQL and any skipped AppSec/SBOM gates.

Release-Impact: minor
