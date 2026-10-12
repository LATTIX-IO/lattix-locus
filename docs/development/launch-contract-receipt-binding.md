# LOCUS-433 increment 17 — verified Docker launch contract in durable action identity

## Objective

Increment 13 validates the entire Docker CLI against the supervisor-authorized sandbox policy. Increment 15 freezes that validated command across durable dispatch. Increment 16 HMAC-binds the supervisor-approved Docker image **reference** to the Gateway action and receipt. However, a launch using the same image and admitted tool can still differ in host mounts, memory/CPU/PID ceilings, seccomp profile, launcher working directory, or execution timeout without changing the previous strict-image authorization identity.

This increment binds the **verified launch contract** to the existing action fingerprint and durable receipt HMAC. No new PEP, executor, filesystem permissions, network egress or output collector is introduced.

## Deterministic identity contract

After the existing full Docker argument-vector comparison succeeds and the broker copies `plan.command` to an immutable tuple, strict-image Docker mode computes a SHA-256 hash of canonical, sorted JSON containing:

- `backend`: the concrete selected sandbox strategy
- `argv`: the entire validated Docker command (without the broker-generated random container name)
- `launcher_cwd`: the validated host-side launcher working directory, or null
- `timeout_seconds`: the actual bounded subprocess deadline

The hash input has the domain separator `locus.sandbox-launch-contract.v1\0`. The broker passes the 64-character lowercase hex as `authorization.args.sandbox_launch_contract_digest`, next to `sandbox_image_ref_digest` and the optional existing `catalog_digest`.

Existing native Gateway authorization and `PodActionReceipts.execute_once` bind these canonical args to the process-authorized action. Reuse of the same durable idempotency key with altered resource limits, mounts, command, working directory or timeout is rejected as a different action before another subprocess is invoked. A retry of the identical already dispatched action remains non-replayable. Non-strict trusted direct broker calls continue to use their historical authorization shape.

## Test-driven definition of done

- Verify the receipt's digest by independently reconstructing canonical data from a mocked single subprocess invocation, removing only the per-dispatch `--name` nonce.
- Assert no raw argv, registry image URI, host paths, random container name or subprocess output enters the newly added authorization args.
- An otherwise identical action with a different timeout and the same idempotency key is rejected, with one original subprocess and one receipt.
- An otherwise identical action with different authorized memory limits and the same idempotency key is rejected, with no second subprocess.
- A changed host-side launcher working directory or additional read mount under a reused idempotency key is rejected before another process or receipt.
- The unchanged same-action replay barrier, HMAC receipt fingerprint, and legacy non-strict authorization shape remain valid.
- Update the earlier strict-image action-identity regression to account for the additional digest without weakening its image and catalog assertions.
- Preserve exact pre-planner policy checks, frozen argv, native Zero Trust Gateway, receipt write-ahead/fencing, Docker containment, timeout cleanup, and no-output transport.

## Scope boundaries

This is a **non-reversible identifier of the verified launch contract**, not a Docker runtime attestation, image-signature verification or proof that kernel/daemon confinement was achieved. The digest is not a replacement for checking the complete command, nor does it prove filesystem identity under TOCTOU. The supplied environment remains governed by the existing minimal environment controls but is not part of this particular digest. Runtime-generated container names are intentionally omitted for deterministic retry identity.

The PR must pass the latest-head backend unit suite, Ruff/mypy quality policy, integration, Windows sandbox, DAST, frontend, performance, release/version and required-gates, with all actionable reviews resolved before merge. Optional skipped CodeQL, SBOM/SCA and AppSec checks remain unverified.
