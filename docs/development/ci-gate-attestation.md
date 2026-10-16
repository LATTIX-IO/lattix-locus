# LOCUS-455 — CI check coverage, blocked merges, and release gating

## Why
The GitHub Actions CI workflow previously ran on both `push` (every branch)
and `pull_request`, causing duplicate jobs for stacked development PRs.
Its `required-gates` aggregator treated *every* upstream `skipped` result
as acceptable and printed "All required CI gates passed", even when SAST,
AppSec/pentest and SBOM/SCA had never executed.

## Baseline change
- Push CI only for `main`; PR CI remains enabled for every PR. Manual
  `workflow_dispatch` remains available. Exact-head PR checks still govern
  merges; this does not cancel or fabricate old check results.
- Baseline required jobs must be `success`, not merely not-failed.
- `sast-codeql`, `lattix-cicd-pentest`, `sbom-sca` may be `skipped` only
  when their respective explicit repo variable is disabled. Such jobs are
  explicitly reported **NOT EXECUTED**.
- For a production/security release set
  `LATTIX_SECURITY_RELEASE_REQUIRED=true`: no skipped security gate is
  acceptable; all eleven checked job classes must actually be successful.
- Unknown jobs, missing jobs, failed/neutral/cancelled jobs, incorrectly
  skipped enabled scanners and malformed environment switches fail closed.
- Unit tests validate enable/disable matrices, strict mode, malformed
  statuses, missing jobs and workflow-trigger regression.

## Still requires human review
GitHub active main ruleset 14175229 requires code-owner review for protected
paths. Maintain legitimate independent review and repository audit; no
automated self-approval, silent CODEOWNERS weakening, or bypass is added.
PR #109 merged with a SHA-guarded normal merge API despite a previously
displayed `mergeable_state=blocked`; determine *actual* effective review
and merge authority before declaring a universal code-owner deadlock.

This increment is **not** complete production security assessment: DAST,
SAST and dependency scan coverage depend on enabled engines/credentials,
and protected Docker mounts need live supported-host validation.


## Exact-commit publication fencing (2026-10-13)

Both tagged server releases and release-producing desktop installer builds now
invoke the complete reusable CI workflow with `require_security: true`
before packaging or publishing. The reused workflow evaluates the caller's
commit and makes skipped CodeQL, AppSec/pentest, or SBOM/SCA jobs fatal.
An ordinary manual desktop artifacts-only build may skip this publishing gate
but cannot publish a GitHub release from that path.

The CI workflow also listens to `v*` tags for audit evidence; these runs
are not a substitute for the reusable, blocking release dependency. The
`required-gates` job checks out the repository and configures Python before
loading `locus_tooling.ci_gate_contract`. Any missing security engine,
disabled scanner, unavailable kit, or failed check must block publication.

Release operators must enable and configure the CodeQL and Lattix scanning
jobs before triggering a production tag. CodeQL/AppSec/SCA being unavailable
is an explicit release blocker, not an accepted baseline exception.
