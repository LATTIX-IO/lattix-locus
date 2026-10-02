# lattix-locus-gitops

GitOps control plane for deploying and promoting Lattix Locus Microsoft AI Foundry configuration.

## Scope

- Environment overlays and release manifests.
- CI/CD workflows for validate, plan, apply, evaluate, promote, rollback.
- Drift detection and export reconciliation jobs.
- Policy/eval gates for deployment safety.

## Structure

- `environments/dev/`: dev overlays and deployment values.
- `environments/stage/`: stage overlays and pre-prod controls.
- `environments/prod/`: production overlays and strict approvals.
- `pipelines/`: reusable pipeline templates and workflow definitions.
- `scripts/`: deployment helpers and reconciliation scripts.
- `releases/`: release bundles/changelogs per environment.

## Pipeline Expectations

1. Validate against `lattix-locus-contracts`.
2. Build a release bundle containing packaged chart, installer assets, promotion plan, and rollback manifest.
3. Plan and diff against target environment.
4. Apply infra/config changes.
5. Run evaluations and guardrail checks.
6. Promote with environment approvals.
7. Support rollback from the previous release bundle and Git history.

## Release automation

The root workflow `.github/workflows/release.yml` now performs the Phase 7 release path:

1. build container images,
2. run the tagged release e2e suite,
3. package the Helm chart and installer assets,
4. generate a versioned release bundle via `scripts/build_release_bundle.py`,
5. publish the bundle as both a workflow artifact and GitHub release assets,
6. gate promotion through `dev -> stage -> prod` GitHub environments using the existing Foundry secret validation and smoke scripts.

Manual rollback is handled by `.github/workflows/rollback.yml`, which:

- requires an explicit target environment and release tag,
- downloads the selected release's rollback metadata,
- validates environment secrets,
- re-runs the environment smoke gate before the operator applies rollback changes.

Each release bundle includes:

- `manifest.json` — version, repo, git SHA, images, packaged artifacts
- `promotion-plan.json` — ordered environment gates and approval expectations
- `rollback-plan.json` — previous-version target and rollback strategy metadata
- `RELEASE_NOTES.md` — operator-facing release summary

## Foundry Connection Setup

Environment overlays:

- `environments/dev/foundry.project.yaml`
- `environments/stage/foundry.project.yaml`
- `environments/prod/foundry.project.yaml`

Required GitHub Actions secrets per environment:

- `FOUNDRY_PROJECT_ENDPOINT_DEV`
- `FOUNDRY_API_KEY_DEV`
- `FOUNDRY_PROJECT_REGION_DEV`
- `FOUNDRY_PROJECT_ENDPOINT_STAGE`
- `FOUNDRY_API_KEY_STAGE`
- `FOUNDRY_PROJECT_REGION_STAGE`
- `FOUNDRY_PROJECT_ENDPOINT_PROD`
- `FOUNDRY_API_KEY_PROD`
- `FOUNDRY_PROJECT_REGION_PROD`

Populate secrets with GitHub CLI:

```powershell
gh secret set FOUNDRY_PROJECT_ENDPOINT_DEV --body "https://<endpoint>/api/projects/<project>"
gh secret set FOUNDRY_API_KEY_DEV --body "<api-key>"
gh secret set FOUNDRY_PROJECT_REGION_DEV --body "eastus2"
```

Repeat for `STAGE` and `PROD`.

## Smoke Test

GitHub Actions workflow: `.github/workflows/foundry-smoke.yml`

Run manually:

```powershell
gh workflow run foundry-smoke.yml -f environment=dev
gh run watch
```

Local smoke test fallback:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT_DEV="https://<endpoint>/api/projects/<project>"
$env:FOUNDRY_API_KEY_DEV="<api-key>"
$env:FOUNDRY_PROJECT_REGION_DEV="eastus2"
./scripts/validate_required_secrets.ps1 -Environment dev
./scripts/smoke_foundry.ps1 -Environment dev
```
