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
