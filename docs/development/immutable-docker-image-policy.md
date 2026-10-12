# LOCUS-433 increment 14 — strict content-addressed Docker image references

## Purpose

The gateway-mediated Docker broker currently accepts `SANDBOX_RUNNER_IMAGE` as a tag (default: `python:3.12.10-slim-bookworm`). Tags are mutable. Operators requiring stronger rollout reproducibility can now configure a **trusted supervisor-only**, opt-in `require_immutable_docker_image=True` admission mode on `PodSandboxBroker` or the frozen `SandboxedPodToolBroker` catalog.

This does not introduce a new executor, PEP, output adapter, policy language, image pull, or external network request. The existing Gateway, fenced receipts, OS isolation, timeout cleanup and exactly-once broker invocation remain unchanged.

## Admission contract

When strict mode is enabled and the chosen strategy is hardened Docker, the broker reads the selected container image reference **once before invoking the planner**. It requires an explicit repository-qualified lowercase `@sha256:` digest with exactly 64 hex characters and rejects mutable tags, incorrect casing, whitespace, malformed digest length, path traversal, repeated separators and an ambiguous final image tag. An example accepted reference is:

`registry.example:5000/locus/sandbox@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

This example is syntactic only and is not a real trusted artifact. Operators must supply an actual registry-issued digest separately.

Strict mode requires both a content-addressed format **and the exact image reference supplied by the trusted supervisor at catalog construction** via `pinned_docker_image`. Missing or malformed administrator pins reject catalog construction; a different valid digest selected at runtime is denied before launch. The approved reference is included in the canonical catalog identity and therefore in the downstream Gateway/receipt action identity. The image selected before planning becomes part of the already-existing exact Docker argv contract. A modified environment, substituted planner image argument, or other launch-plan mismatch fails **before** Gateway authorization, receipt preparation or process dispatch. The strict-mode setting is a frozen administrator catalog property and enters its canonical catalog revision digest, which is already bound to the durable Gateway/receipt action identity. Changing strict mode therefore changes that catalog identity. The default remains `False` for legacy compatibility; such deployments **do not** receive pinned-image guarantees.

## Definition of done and tests

- Reject mutable tags, absent or malformed SHA-256, uppercase hex, ambiguous tag+digest references, illegal path components and injected Docker options before any receipt or subprocess.
- Reject strict catalogs without an explicit supervisor-provided approved digest, and reject a different syntactically valid runtime image digest.
- Accept a syntactically valid content-addressed image with exactly one authorized, no-network, read-only-rootfs, cap-drop Docker launch.
- Preserve `stdout=DEVNULL`, `stderr=DEVNULL`, durable audit/fencing and idempotency-key replay denial.
- A planner-side environment switch to a different, valid digest cannot alter the originally approved image.
- Reject non-boolean strict flags at broker and administrator-catalog construction.
- Freeze the strict mode in the catalog and ensure changing the mode changes the catalog digest.
- Preserve existing tagged-image operation only when strict mode is disabled.
- Run prior Docker launcher policy, gateway, receipt, typed result, catalog and Windows sandbox suites, plus backend unit, Ruff/mypy, integration, DAST, performance, frontend, release/version and required-gates against the latest head.

## Limits

A Docker content digest identifies content according to the registry/daemon's digest machinery, but this increment **does not** independently verify the image contents, signatures, trusted publishers, transparency logs, admission attestation, or local Docker daemon integrity. Strict mode is opt-in until deployment policy and image distribution are migrated to pinned references. No change to the default production image is claimed. Disabled CodeQL, SBOM/SCA and AppSec jobs are skipped, not verification successes.
