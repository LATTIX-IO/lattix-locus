# LOCUS-433 increment 15 — immutable launch handoff to fenced dispatch

## Threat

The Docker broker already checks the complete planned argv against an authorization snapshot before admission (increment 13). However, `SandboxManager.plan` returns a mutable `ExecutionPlan.command` list. A planner or wrapper retaining that object can change its fields **after the full validation** and before the `PodActionReceipts.execute_once` callback executes. The original broker copied `plan.command` inside the callback, after durable receipt preparation/fencing. The recorded authorization could therefore describe one sandbox command while the process launched a different command.

## Implementation

Immediately after all sandbox and Docker contract validations succeed, the existing `PodSandboxBroker.execute` copies the validated argv to an immutable `tuple[str, ...]` and separately captures the authorized backend from the already-selected strategy. The asynchronous/durable receipt dispatch callback converts **only the frozen snapshot** into its invocation argv; the broker still injects its own random Docker container name. It no longer reads mutable planner command or backend fields after validation.

This changes no Gateway PEP decision, filesystem mount policy, executable registry, container network setting, capability grant, application protocol, output adapter, or subprocess execution architecture. There remains one invocation path using the same kernel OS-fencing and at-most-once broker receipts. Logs and action receipts still do not expose stdout/stderr.

## Test-driven security and regression gates

- Six retained-planner mutation cases occur *after* successful launch-plan validation but *before* the receipt's invocation callback: add a privileged flag, replace with direct host execution, replace with host-network Docker invocation, clear argv, spoof the backend, and substitute the launcher's working directory. The actual subprocess must use the original vetted CLI bytes, the validated backend and captured directory, without a second executor.
- Three negative-path cases modify the plan *before* validation (unauthorized host bind, host network and direct host command); all must fail closed with **zero** process invocations and **zero** receipt rows.
- The positive case verifies a single Docker name injection, no-network/read-only/cap-drop, `shell=False`, suppressed stdout/stderr, durable receipt audit, and existing idempotency-key replay denial.
- Existing gateway, reservation, fencing/recovery, strict catalog, Docker image digest, Linux/Windows sandbox, typed result and policy regressions remain mandatory.
- CI acceptance: exact-head backend tests, Ruff/mypy, frontend, integration, Windows sandbox, DAST, performance, release/version and `required-gates`, plus review closure and no scope regressions.

## Exclusions and risk

The mitigation prevents **planner-object mutations after validation** from changing this broker's dispatch. It does not create trusted Docker daemon or image attestation, protect against privileged host compromise, prevent kernel/daemon-level attacks, or remove image/executable file TOCTOU risks outside this object boundary. The prior authorized command snapshot is still sourced from the trusted supervisor/lease inputs. Optional CodeQL, SBOM/SCA and AppSec jobs skipped by repo settings remain unverified.
