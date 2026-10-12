# LOCUS-433 increment 20 — canonical broker command admission grammar

## Scope and vulnerability

The administrator tool catalog already uses a strict ASCII identifier grammar, and the durable action-receipt ledger enforces a bounded ASCII idempotency key. The trusted direct `BrokerCommand` interface previously accepted any nonblank tool label up to 128 characters and any nonempty idempotency key. Consequently, embedded line breaks, Unicode lookalikes and path-like tool aliases could enter the broker's policy/audit pipeline, while malformed action keys were rejected only later by the ledger after sandbox planning and other work.

This increment makes the **direct broker constructor** reject those malformed identities before any sandbox selection, Gateway authorization, receipt preparation or external execution. It does not enlarge the tool registry or permit the LLM to select new commands.

## Admission contract

- A `tool` identifier must match the administrator catalog's case-sensitive ASCII grammar: `[A-Za-z][A-Za-z0-9_.-]{0,127}`. Unicode, path separators, shell-like controls, whitespace and colon aliases are rejected.
- An `idempotency_key` must match the durable receipt ledger's ASCII grammar: `[A-Za-z0-9_.:-]{1,160}`. This matches the existing receipt authority, including colon, dot, hyphen and underscore.
- `working_dir` must be a bounded, nonblank string of at most 4096 characters without NUL, carriage return or newline. The existing `_bounded_dir` still resolves and enforces the actual child read scope and active directory.
- The existing argv tuple length, 2048-character argument bounds, shell/interpreter denylist, bounded timeout, exact catalog digest grammar, Zero Trust Gateway PEP, OS-confining sandbox and durable at-most-once receipt remain unchanged.

## Unit, regression, and security acceptance

- Invalid tool identifiers containing control characters, slash/backslash, Unicode lookalikes, leading/trailing whitespace, digits at the start and overlong names fail at construction.
- Malformed/overlong/non-ASCII action keys fail at construction, matching the receipt's existing `_valid_key` acceptance set.
- NUL/CR/LF, empty and overlong working directories fail at construction.
- Legitimate existing tool names and maximum-length ASCII names, and the full ledger-compatible idempotency key forms, remain accepted.
- A valid direct broker execution still follows the **single** Gateway-authorized Docker sandbox with no network, suppressed stdout/stderr, the unchanged durable audit sequence and idempotency-key replay denial.
- The tests are deterministic, using existing mocked subprocess and receipt fixtures; no external services or additional executors are introduced.

## Delivery criteria and limitations

PR #104 must merge before this stacked increment can be reconciled onto `main`. When the parent merges, preserve the three-file incremental diff (broker, tests, documentation) and rerun exact-head CI: backend unit, Ruff/mypy/quality policy, integration, Windows sandbox, DAST, frontend, performance, release/version and required-gates. Resolve actionable security reviews, then merge with the expected SHA.

This guards parsing, canonical identity and audit-log integrity; it is not a substitution for the executor allowlist, path authorization, endpoint isolation, cryptographic provenance or the sandbox's kernel controls. Optional CodeQL, SBOM/SCA and AppSec jobs skipped by CI do not count as verification.
