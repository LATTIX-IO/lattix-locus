# LOCUS-433 increment 16 — Approved image identity in durable Gateway action receipts

## Threat and scope

Increment 14 permits trusted supervisors to require an **exact administrator-approved content-addressed Docker image reference**, and increment 15 freezes the validated command across the broker-to-receipt handoff. The immutable administrator catalog identity already includes the image pin, but a trusted `PodSandboxBroker` invocation can bypass the catalog wrapper and use strict image policy directly. Previously that direct broker action authorization did not identify the approved container image. An idempotency key for the same CLI tool could be presented under a different approved image reference without the receipt fingerprint distinguishing the two authorizations.

This increment binds the strict Docker image to the **existing** Gateway and durable receipt action identity. It does not add a new PEP, runner, output path, permission, data collector, or remote API.

## Admission and durable binding

For strict Docker execution only, the broker computes a domain-separated SHA-256 of the **entire approved Docker image reference**, including registry/repository and `@sha256:` digest. The domain is `locus.sandbox-image-ref.v1\0`. It supplies the lowercase hex digest as `authorization.args.sandbox_image_ref_digest` to the unchanged Gateway/receipt pipeline. Thus the existing Gateway action fingerprint and run-scoped HMAC receipt authorization capture the image reference without storing the raw registry string in the new structured args.

If the invocation additionally originates from the immutable administrator tool catalog, its existing `catalog_digest` remains alongside the image reference digest in the same canonical args mapping. If neither image strict mode nor a catalog identity applies, the prior authorization shape remains unchanged for compatibility.

A replay with an existing idempotency key under a **different** image reference, including the same image content digest hosted in a different registry/repository, must fail before launching another process. An ordinary same-image replay remains denied.

## Unit/regression/security acceptance

- Strict Docker execution exposes the domain-separated digest in the Gateway authorization; no raw image ref, argv or stdout/stderr in the newly added structured args.
- The durable action receipt fingerprint records this authorization and the sandbox still runs once with `shell=False`, `stdout=DEVNULL`, `stderr=DEVNULL`.
- A change to image digest or repository with the same idempotency key denies replay before any additional process.
- Ordinary same-image replay remains denied.
- Existing direct, non-strict broker invocations retain their authorization shape.
- Catalog identity and approved image reference can coexist in canonical Gateway authorization.
- Previous trusted-path, no-network Docker, timeout, OS-fencing, Windows sandbox, DAST, release, policy and backend suites remain mandatory.

## Limitations

A SHA-256 reference digest records the name/identity chosen by trusted supervisor configuration. It is **not** proof of a registry signature, supply-chain provenance, byte-for-byte local runtime image identity or Docker daemon integrity. The change does not eliminate host TOCTOU or replace native container isolation. Any skipped CodeQL, SBOM/SCA and AppSec jobs are unverified, not green.
