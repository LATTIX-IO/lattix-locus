# CLAUDE.md — Lattix xFrontier

@AGENTS.md
@.ai-memory/repo-profile.md

<!-- Local, gitignored: actual Obsidian vault path + global/identity memory import. -->
@CLAUDE.local.md

## Notes for Claude Code
- Honor repo-local rules over globals. Prefer existing conventions in `apps/backend`, `apps/workers`, `frontier_runtime`.
- When a change is driven by a memory, cite the memory id in your final response.
- Do not repurpose Claude's auto `MEMORY.md` as the cross-tool store — the Nexus vault schema (`50-Memory/MEMORY_PROTOCOL.md`) is authoritative.
- `AGENTS.md` carries the shared Lattix engineering standard (bundle `2026.05.05`). Do not drift that section locally — update it upstream in `lattix-monorepo` and re-merge. See `.github/agent-standards/README.md`.

## Repo standards
`docs/ARCHITECTURE.md` · `DESIGN.md` · `FRONTEND.md` · `PRODUCT_SENSE.md` · `QUALITY_SCORE.md` · `RELIABILITY.md` · `SECURITY.md` · `THREAT-MODEL.md` · `PLANS.md` · `WORKFLOW.md`

Shared prompts live in `.github/prompts/`; path-scoped rules in `.github/instructions/`.
