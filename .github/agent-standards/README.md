# Lattix Agent Standards — consumer record

This repository **consumes** the Lattix-wide agent standards bundle. It does not author it.

- Canonical source: `LATTIX-IO/lattix-monorepo`
- Installed bundle: see `VERSION` in this directory
- Sync tool: `scripts/sync-agent-standards.ps1` in the monorepo

## Generated files — do not hand-edit

Every file below carries a `BEGIN LATTIX GENERATED AGENT STANDARDS` header naming its canonical source. Edit the source in `lattix-monorepo` and re-sync; local edits are overwritten and will show as drift.

- `.github/instructions/*.instructions.md` (4)
- `.github/prompts/*.prompt.md` (18)

## Locally merged files — safe to edit

These carry the shared standard **plus** xFrontier-specific content, so they are not byte-identical to the monorepo source and are not managed as generated files:

- `AGENTS.md` — xFrontier repo rules + the shared Lattix principal-engineer standard
- `.github/copilot-instructions.md` — xFrontier Nexus memory guidance + the shared desloppify workflow

When the shared portions change upstream, re-merge those sections by hand rather than overwriting the whole file.

## Refreshing the bundle

`lattix-xfrontier` is **not** registered in the monorepo's `.gitmodules`, so `sync-agent-standards.ps1` cannot target it automatically. Until it is registered, refresh manually from a monorepo checkout that has already been synced:

```
cp <monorepo>/<any-synced-child>/.github/instructions/*.instructions.md .github/instructions/
cp <monorepo>/<any-synced-child>/.github/prompts/*.prompt.md .github/prompts/
```

Then update `VERSION` to match `<monorepo>/.github/agent-standards/VERSION` and re-merge the shared sections of `AGENTS.md` and `.github/copilot-instructions.md`.

To make this automatic instead, add `lattix-xfrontier` as a submodule in the monorepo's `.gitmodules`; the `Agent Standards Sync` workflow then picks it up like every other child repo.

## Repo-specific standards

The bundle is only half the contract. The following are authored per repository and are **not** synced — they hold this repo's own engineering standards and quality expectations:

`ARCHITECTURE.md` (here: `docs/ARCHITECTURE.md`) · `DESIGN.md` · `FRONTEND.md` · `PLANS.md` · `PRODUCT_SENSE.md` · `QUALITY_SCORE.md` · `RELIABILITY.md` · `SECURITY.md` · `WORKFLOW.md`
