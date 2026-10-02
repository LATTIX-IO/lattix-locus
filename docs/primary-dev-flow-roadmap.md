# Roadmap: xFrontier as your primary code-development flow

> Date: 2026-06-13. Goal: gpt-oss-20b on your local repos, a multi-agent team that
> challenges/moderates to ship high-quality, secure, performant, lean code from a
> spec — chain-of-thought, memory-backed, wired to GitHub, Linear, Notion,
> Obsidian, Azure.

## Status — what works today

| Capability | State |
| --- | --- |
| gpt-oss-20b drives the harness | ✅ via Ollama (`:11434`); native tool-calling confirmed |
| Local repos / git | ✅ `LocalDirectExecutor` + `Workspace` (clean diffs, host-side git) |
| SWE agent (implement + test + submit) | ✅ `SweAgent`, hardened by real-run trajectories |
| **Agent team** (architect, SDET, code/security/perf reviewers, moderator) | ✅ shipped in `examples/agents/`, published, visible in the modeler |
| **TeamFlow** (plan→implement→review panel→moderate→bounded fix loop) | ✅ `frontier_runtime/harness/team.py`, deterministic tests |
| Trigger a team on a local repo | ✅ `frontier-evals team --repo <path> --spec @spec.md` |
| Lossless trajectories (chain-of-thought capture substrate) | ✅ `trajectory.py` (JSONL, per-agent) |
| SWE-bench Verified benchmark path | ✅ wired + Docker exec proven; needs a runner endpoint |
| **Azure Cloud Engineer agent** | ✅ shipped (`examples/agents/azure-cloud-engineer-agent`) |
| **Spec sources** (inline / file / **Linear**) | ✅ `integrations.py` `SpecSource`; Linear via injected fetcher |
| **GitHub delivery** (open PR on approve, merge on re-approve, CI status; policy-configurable) | ✅ `GitHubDelivery` + `DeliveryPolicy`; remote ops behind `GitHubClient` (gh/git impl + fakes) |
| **DevFlow** (spec → team → delivery) | ✅ `integrations.py` `DevFlow`, deterministic tests |

## What remains, sequenced by dependency & impact

### Phase 1 — Repo binding & triggering (small)
- `WorkspaceManager`: clone/worktree-per-run from a git URL or local path, retention +
  cleanup (planned in `production-harness-plan.md` M2.2). Today the team runs in-place
  on a local folder; add isolated worktrees so concurrent runs don't collide.
- A platform run entrypoint (backend) that launches a TeamFlow run and streams events
  (reuses the existing run/SSE machinery; M4.3 headless `exec`). Then "trigger from the
  UI" works, not just the CLI.

### Phase 2 — Chain-of-thought, surfaced (small/medium)
- Per-agent reasoning is already captured in trajectories. Expose it:
  - For gpt-oss specifically, parse the **harmony `analysis` channel** as the CoT and
    record it as `annotation` lines (harmony path is `model_profiles` `gpt-oss-harmony`;
    the encoder/`/completions` client is the build — `production-harness-plan.md` M3.4).
  - Emit CoT + tool steps as run events so the UI shows each agent's thinking live
    (the run console already renders agent events).

### Phase 3 — Memory-backed runs (medium) — Postgres + Neo4j cortical columns
Authoritative memory is the **Postgres + Neo4j cortical-column system**; Obsidian is an
optional parallel mirror, never the source of truth.
- **Recall**: before a run, retrieve relevant prior decisions/patterns/repo-rules from
  `PostgresLongTermMemoryStore` (pgvector) + the Neo4j cortical columns and inject into the
  architect + implementer + reviewer context.
- **Persist**: after a run, write decisions/patterns back into the columns and store the
  trajectory (for SFT/RL). The moderator's verdict becomes evidence in the
  goal/evidence/synthesis columns (`frontier_runtime/cognitive.py`).
- **Obsidian (optional, parallel)**: a one-way mirror that maps the vault folder structure
  into the memory system and writes run summaries back, in parallel — toggleable, additive,
  and clearly secondary to the Postgres/Neo4j store.

### Phase 4 — Live connector wiring (decisions resolved)
The orchestration seams (`SpecSource`, `GitHubClient`, delivery policy, the Azure agent)
are built. What remains is wiring each to a real, authenticated transport through the
platform's **MCP gateway** + integrations, gated by the capability/OPA layer.

| System | Role (per your decisions) | Build |
| --- | --- | --- |
| **Linear** (primary spec) | issue → spec; sync status In Progress → In Review on PR open → Done on merge | back `LinearSpecSource.fetcher` with the Linear MCP server; add a status-writer in delivery |
| **Notion** (standards/docs) | the team *reads* standards/conventions and applies them | a `StandardsSource` injected into the architect + reviewers' context; Notion MCP fetch |
| **GitHub** (delivery) | open PR on approve (diff + CI); merge on re-approve; **policy in `/builder/settings`** | back `GhCliGitHub` with `gh` + a token; surface `DeliveryPolicy` in settings (schema below) |
| **Azure** (cloud platform mgmt) | the Azure agent designs/manages Azure services | `az` CLI in a gated MCP tool (read-then-confirm-write); Azure agent already shipped |
| **Obsidian** (optional parallel) | mirror folder structure into the AI memory system, *alongside* the authoritative store | a parallel memory mirror (see Phase 3); never the source of truth |

**Settings (`/builder/settings`)** — add a delivery-policy block consumed by
`DeliveryPolicy.from_settings`: `auto_open_pr`, `auto_merge_on_reapprove`, `target_branch`,
`branch_prefix`, `merge_method`. Backend `PlatformSettings` field + the settings UI form.

### Phase 5 — Quality scaling (medium)
- Best-of-N implementations with execution + LLM-judge verification (DeepSWE's +17-pt
  lever; `production-harness-plan.md` M6) feeding the moderator.
- SWE-bench Verified scoring on a runner to track the team's real quality over time.

## Decisions (resolved 2026-06-13)
1. **Azure** = cloud platform management (design/manage/operate Azure services). → Azure
   Cloud Engineer agent (shipped) + `az` CLI as a gated MCP tool.
2. **Linear** = primary spec source; **Notion** = standards/docs the team reads;
   **Obsidian** = optional parallel memory mirror.
3. **PR policy** = on approve, open PR (show diff + CI); on re-approve of an open PR, merge
   to target branch — configurable in `/builder/settings` (`DeliveryPolicy`).
4. **Memory authority** = Postgres + Neo4j cortical-column system; Obsidian is an optional
   parallel add-on.

## Build order from here
1. **GitHub live + settings** — back `GhCliGitHub` with a token; surface `DeliveryPolicy`
   in `/builder/settings`. (Closes the output side of the flow end-to-end.)
2. **Linear live** — back the spec fetcher with the Linear MCP server + status sync.
3. **Memory recall/persist** — Postgres/Neo4j cortical columns into the team context.
4. **Notion standards** + **Azure `az` tool** + **Obsidian parallel mirror**.

## Try the team now (local, gpt-oss-20b)
```bash
python -c "import sys; sys.path.insert(0,'apps/evals'); from frontier_evals.cli import cli; cli()" \
  team --repo /path/to/your/repo --spec @spec.md \
  --test-command "python -m pytest -q" \
  --api-base-url http://localhost:11434/v1 --model gpt-oss:20b --provider ollama \
  --trajectory-dir eval-results/team-run
```
