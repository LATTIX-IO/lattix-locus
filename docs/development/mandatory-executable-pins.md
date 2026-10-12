# LOCUS-433 — strict executable pin admission (increment 11)

This increment adds an opt-in strict admission policy to the existing trusted administrator tool registry. The supervisor may construct `SandboxedPodToolBroker(receipts, tools, require_pinned_executables=True)`. When enabled, **every** registered `BrokerTool` must carry a valid canonical SHA-256 executable digest. Missing pins reject construction before any action receipt, gateway authorization, or process invocation.

## Contract and invariants

- The strict-mode flag accepts only actual Python booleans. It defaults to `False` to retain compatibility with preexisting administrator catalogs. Do not interpret the default as binary integrity assurance.
- The catalog copies the supplied registry before validation, verifies exact `BrokerTool` instances/keys, and rejects any missing digests when strict mode is enabled.
- Both the strict-mode setting and the registered descriptor pins are included in a deterministic canonical catalog digest. Two otherwise identical catalogs with different enforcement modes therefore have different identities.
- The registry, execution delegate, and strict setting are stored in a frozen slotted dataclass. Application code cannot normally rebind them after registration; this is **not** a process-isolation boundary against malicious in-process Python.
- Dispatch retains the single `PodSandboxBroker.execute` path and the existing child capabilities, canonical paths, bounded digest preflight, native Zero Trust gateway, action receipts, OS fencing and replay denial.
- An executable modified after catalog construction and before dispatch fails digest preflight without producing a receipt or starting a process.

## Test-driven acceptance

- Strict mode refuses missing/partial digest catalogs before action admission.
- Invalid non-boolean strict flags fail closed.
- Valid strict catalogs execute once through the real sandbox plan and durable receipt, with subsequent replay denied.
- Binary substitution with a previously valid pin fails before subprocess/receipt.
- Supervisor-provided source map mutation cannot change the admitted strict catalog.
- Strict-mode flag is immutable; strict versus optional modes yield distinct digest identities.
- Legacy catalogs remain admissible only when strict mode is explicitly off or omitted.
- All existing gateway, receipt, OS fencing, canonical-path, digest preflight, typed-status, and Linux/Windows sandbox regression suites remain required.

## Limitations

SHA-256 comparison is not a signature, supply-chain provenance check, or remote attestation. The earlier digest preflight remains susceptible to a file replacement after the measurement and before execution (TOCTOU). This increment neither fixes that race nor adds another subprocess path, output collector, runtime plugin installation, or new LLM authority.

Before merging, require successful exact-head backend/unit tests, Ruff/mypy/policy, frontend, integration, Windows sandbox, DAST, performance, release-version, and required-gates, plus resolution of security review findings. Repository-skipped SAST, SBOM, and AppSec gates are not executed security checks.
