# LOCUS-433 — Immutable direct broker configuration (increment 19)

## Problem

The immutable administrator tool registry prevents ordinary reassignment of its trusted tool descriptors, executable-pin policy, approved Docker image reference and receipt delegate. However, its underlying `PodSandboxBroker` remained a normal mutable Python object. Trusted components using that broker directly could accidentally reassign `_require_immutable_docker_image`, `_pinned_docker_image` or `_receipts` after constructor validation and before an action's Gateway/receipt handoff.

This increment aligns the single direct broker with the same configuration immutability convention used by the administrator catalog. It does not introduce new authorization, process dispatch, or tool registration pathways.

## Implementation and trust boundary

`PodSandboxBroker` is now a frozen, slotted dataclass with an explicit existing constructor. The constructor performs the existing exact-boolean and approved-image reference validation, then assigns `_receipts`, `_require_immutable_docker_image`, and `_pinned_docker_image` exactly once using `object.__setattr__`. Its instance has no mutable `__dict__`, and its representation does not print the approved image reference or receipt object.

An application requiring an administrator configuration revision must instantiate a new broker; the live broker cannot be changed with ordinary assignment. This is defensive program-integrity protection, **not** protection against hostile privileged in-process Python, reflection, `object.__setattr__`, or compromise of the referenced mutable receipt storage. Existing native Zero Trust Gateway authorization, immutable catalog, sandbox policy, frozen Docker launch argv, OS fencing and replay semantics remain the enforcement boundaries.

## Unit, security, and regression acceptance

- Attempted ordinary reassignment of the strict-image flag, pinned image reference, or receipt delegate raises `FrozenInstanceError`, with no process invocation and no durable receipt created.
- Unexpected instance attributes and mutable instance dictionaries are unavailable.
- `repr(broker)` excludes the approved image reference and receipt internals.
- A strict frozen broker still runs one approved, no-network, cap-dropped, read-only Docker command with `shell=False` and suppressed stdout/stderr; the original write-ahead receipt audit and same-key replay denial remain intact.
- A legacy non-strict broker still executes with the existing capability and sandbox policy, with immutable receipt delegate.
- Previous Docker backend selection, executor allowlisting, approved-image checks, catalog identity, launch-contract digest, cross-revision replay, timeout and Windows/DAST regressions remain unchanged and must pass.

## Deployment and merging

This increment is stacked on PR #103 until increment 18 merges into `main`. Once #103 merges, reconcile the increment to `main`, confirm the diff is exactly one broker module, one test module and this documentation file, and require all enabled exact-head CI checks plus resolved security reviews before lifting draft and SHA-protected merge. Disabled CodeQL, SBOM/SCA and AppSec checks remain unverified and cannot be represented as passing.

No new claims of race-free executable identity, signed container attestation, Docker daemon integrity or isolation guarantees are made.
