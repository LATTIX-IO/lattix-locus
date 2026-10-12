# LOCUS-433 increment 21 — fail-closed Docker planner metadata contract

## Threat and required invariant

The hardened Docker strategy builds the full container launcher in the validated Docker `argv`. Its trusted `SandboxManager.plan` returns an empty metadata dictionary and no host-side network route (`network_name=None`). The broker previously trusted `plan.metadata["launcher_cwd"]` whenever it was an absolute path. A compromised or defective planner could therefore change the **host-side Docker CLI working directory** to `/etc` or another unauthorized location while its validated Docker argv remained unchanged. That value is also used by the bounded Docker cleanup process on timeout. Additional metadata and non-None network selectors were likewise not explicitly ruled out.

The broker now requires, for `HARDENED_DOCKER` only:

- `plan.metadata == {}`, with no host launcher-cwd override or other unrecognized keys;
- `plan.network_name is None`;
- the complete preplanner-captured Docker argv contract continues to match.

Deviations fail with a fixed `AdmissionDenied` **before** Gateway authorization, receipt write-ahead preparation, child process dispatch or timeout-cleanup processing. For native sandbox strategies, existing launcher-directory metadata handling is preserved. The selected Docker launcher runs with `cwd=None`, not a planner-supplied directory.

## Test-driven scope and acceptance

- Planner-injected absolute, relative, or empty host launcher directories reject before any subprocess and any durable receipt.
- Unrecognized network selectors (`host` or an empty string) and arbitrary planner keys including Docker endpoint overrides reject with zero side effects.
- The unmodified Docker planner executes once with `cwd=None`, `shell=False`, no-network launch flags, suppressed stdout/stderr, and the original receipt audit sequence; same-key replay remains denied.
- Native/non-strict planning remains reachable; this increment must not impose Docker-specific empty-metadata constraints on Windows AppContainer and its own launcher directory.
- Update the preexisting receipt-contract test that previously treated a changed Docker launcher directory as a different replay identity. Because the override was never authorized, the correct outcome is now **pre-receipt admission denial** rather than allowing the first altered planner action to reach the receipt ledger.
- All preceding catalog, image pinning, launch contract, receipt, strict Docker backend, immutable broker and canonical identity tests remain required.

## Trust boundary and deployment

The validation is an independent postplanner check of fields not included in Docker's trusted argument contract. It does not authenticate the container daemon, verify signed image contents, alter Gateway permissions, add new subprocess paths, or loosen host isolation. No remote network access is introduced. Existing Docker environment minimization remains unchanged.

PR #105 is a dependency; after it merges, this increment must be reconciled onto `main` preserving only broker, tests, one previous regression and documentation. Require exact-head backend pytest, Ruff/mypy quality, integration, Windows sandbox, DAST, frontend, performance, release/version and required-gates; all actionable review findings must be resolved. Repository-disabled CodeQL/SBOM/AppSec are unverified, not passes.
