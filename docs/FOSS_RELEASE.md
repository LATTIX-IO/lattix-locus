# FOSS release model

This repository is released under **AGPL-3.0-or-later**. Downstream users may use and commercialize the software, but if they modify covered code and provide it to users over a network, they must also provide the corresponding source under the AGPL.

This public repository is for **Lattix Locus**, an independent Lattix project. The Lattix Locus name, ideas, and product direction were developed independently by Lattix.

## Canonical public structure

The public repository now treats the following paths as canonical:

- `apps/frontend/`
- `apps/backend/`
- `apps/workers/`
- `packages/contracts/`
- `packages/data/`
- `deploy/infra/`
- `deploy/gitops/`
- `examples/agents/`
- `locus_runtime/`
- `locus_tooling/`

## What stays private

The following should remain outside the FOSS tree:

- private Lattix agent definitions
- internal prompts and workflow bindings
- environment-specific secrets and overlays
- customer-specific data or policies

These private assets are intentionally **not** part of the AGPL-licensed public repository unless and until they are added here.

## Agent asset loading

The public runtime now loads agent assets in this order:

1. public sample assets from `examples/agents/`
2. an explicit override from `LOCUS_AGENT_ASSETS_ROOT`

This keeps the local-first platform functional for public users while preserving a secure extension path for private deployments without requiring private repositories to exist inside the local checkout.

## Local-first security posture

The public migration keeps local-first hosting intact by:

- leaving `.env` local-only and gitignored
- defaulting backend seed assets to public demo agents
- preserving explicit external asset injection for private/internal agent content
- keeping path remaps limited to repo-level tooling and container working directories

## Next cleanup steps

- remove any remaining legacy `lattix_locus` or `lattix-locus-*` compatibility assumptions from code and docs
- audit `deploy/infra/` and `deploy/gitops/` contents for public-safe publication
- add package-level ownership and release automation for the new `apps/` and `packages/` structure
