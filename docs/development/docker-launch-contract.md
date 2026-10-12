# LOCUS-433 increment 13 — complete Docker launch-contract validation

## Security objective

The gateway-mediated Docker broker must verify the *entire* argument vector it is about to invoke, not merely the presence of key isolation flags. A Docker CLI that contains both `--user=1000:1000` and a later `--user=0:0`, or an extra host mount or capability, is not a valid confinement plan even though the earlier controls are still present.

This increment adds a deterministic, independent reconstruction of the approved Docker argument contract, based on the trusted `ExecutionSpec` and `SandboxPolicy`. The broker takes the immutable expected-argument snapshot **before** invoking `SandboxManager.plan`, because both its inputs expose mutable lists and dictionaries. The planner therefore cannot add a host-write root, change its network policy, or alter command/environment fields to redefine what the broker considers authorized. The broker compares the complete planned argument list to that frozen expected vector **before** Gateway authorization, receipt preparation, subprocess dispatch, or side effects. Any difference denies with a fixed error.

## Accepted Docker contract

- Fixed Docker `run --rm`, drop-all capabilities, no-new-privileges, read-only rootfs, non-root uid/gid, private IPC, and the validated seccomp profile.
- Exactly one no-network mode, plus policy-derived memory, CPU and PID limits.
- Exactly one controlled non-executable `/tmp` tmpfs mount.
- Canonical deduplicated writable/readable mounts supplied by the admitted child policy, with no arbitrary host mounts.
- The trusted working directory, approved sandbox image and exact, immutable tool argument vector.
- No untrusted environment variables, additional requested hosts, alternate entrypoint, privilege escalation, or duplicate Docker flags.

The validator does not decide policy or parse LLM-generated Docker options. It matches the actual sandbox command to one trusted representation of the admitted policy. The existing `PodSandboxBroker` remains the only subprocess execution path and still uses native Gateway decisions, durable action receipts, OS fencing, timeout containment and replay denial.

## Test-driven acceptance

- Unauthorized or conflicting `--user`, `--network`, seccomp, capabilities, PID/IPC namespace, `--privileged`, `--device`, `--entrypoint`, Docker environment, mounts, and `--tmpfs` variants are denied.
- Replaced required flags and duplicate mount destinations are denied.
- Every rejected launch leaves **zero** subprocess invocations and **zero** action receipts.
- A normal plan still executes once, remains status-only with suppressed output, carries the original Gateway/receipt audit events and refuses idempotency-key replay.
- All existing runtime, Docker, Windows sandbox, catalog digest, policy, receipt and network-denial regression suites remain required.

## Boundaries

This strengthens planner/output integrity, not Docker daemon integrity. It does not certify the Docker engine, container image signatures, seccomp contents, image filesystem, or race-free executable identity. A compromised host/container runtime or trusted control plane can still invalidate assumptions. It is not an additional enforcement engine.

Merge only after PR #97 is merged and the increment is reconciled to `main`. Require fresh latest-head backend, Ruff/mypy, integration, Windows sandbox, DAST, performance, release/version and `required-gates` results. Skipped CodeQL, SBOM/SCA and AppSec are not treated as executed checks.
