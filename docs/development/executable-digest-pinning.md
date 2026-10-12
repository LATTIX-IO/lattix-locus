# LOCUS-433 — optional executable digest pinning (increment 10)

This increment provides an **opt-in administrator-pinned executable content digest** for the existing immutable tool catalog. It does not add a process runner, policy evaluator, output collector, or new tool authority. All executions still pass through the existing admitted PodLease, native Gateway PDP, OS-confining sandbox, and durable action-receipt/OS-fencing controls.

## Registration and invocation contract

A trusted supervisor may supply `executable_sha256` to a `BrokerTool` as exactly 64 lowercase hexadecimal characters. An absent digest preserves existing catalog compatibility and is **not integrity-attested**. The optional pin is included in the canonical catalog revision digest, so changing an executable pin changes the catalog fingerprint.

Before dispatch, the broker first verifies canonical executable and working-directory paths (increment 9). When a digest is configured, the broker opens the executable read-only and, where supported, requests nonblocking, close-on-exec, and no-follow behavior. It requires a regular file, rejects files above 128 MiB, streams at most 1 MiB per read, checks total bytes and file metadata before/after measurement, and compares the SHA-256 against the administrator pin using constant-time comparison.

A missing file, unreadable file, unexpectedly large file, mutated measurement, or digest mismatch fails closed **before** any action receipt, Gateway effect authorization or subprocess invocation. The digest check records no executable bytes, raw arguments, or binary content to the receipt ledger.

## Acceptance tests

- An approved digest permits one authorized sandbox execution and the original receipt audit sequence.
- A substituted executable with the same file length is rejected without a receipt or subprocess.
- A wrong pinned digest or noncanonical digest format is rejected.
- File-size policy bounds are enforced before process dispatch.
- Canonical catalog identity changes when the pin changes.
- Unpinned legacy descriptors continue working under the existing policy path.
- Binary bytes are absent from the public registry representation and result/receipt.
- Latest-head CI must pass backend/unit tests, static analysis and typing, frontend, integration, Windows sandbox, DAST, performance, release/version and required-gates, plus any other enabled mandatory checks.

## Security limits

This is a **best-effort preflight measurement**, not a cryptographic signature, trust-chain validation, secure-boot proof, or race-free executable file-descriptor binding. In particular, replacing the file after measurement is still a possible TOCTOU attack; obtaining stronger identity guarantees requires platform-specific descriptor execution and immutable/mounted artifact handling in a later increment. Do not use the SHA-256 pin as a substitute for signed updates, provenance, least privilege, or the native Zero Trust execution controls. Optional skipped CodeQL, SBOM and AppSec jobs are not verified checks.
