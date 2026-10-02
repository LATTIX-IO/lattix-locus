# Contributing to Lattix Locus

Thanks for helping improve Lattix Locus.

By contributing to this repository, you agree that your contributions are submitted under the repository's **AGPL-3.0-or-later** license unless explicitly stated otherwise in writing by the project maintainers.

## Ground rules

- Keep changes small, reviewable, and well-tested.
- Do not commit secrets, customer data, or proprietary prompt content.

## Local development

### Root platform

- `make bootstrap`
- `make up`
- `make test`
- `make policy-test`

### Frontend

Work from `apps/frontend/`:

- `npm ci`
- `npm run lint`
- `npm run build`
- `npm test`

### Backend

Work from `apps/backend/` for the legacy FastAPI backend service.

## Pull requests

Please include:

- problem statement
- implementation summary
- test evidence
- rollout or compatibility notes

## Reporting security issues

Please do **not** open public issues for vulnerabilities. Follow the process in `SECURITY.md`.
