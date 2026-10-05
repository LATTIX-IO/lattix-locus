---
tracker:
  kind: linear
  provider:
    project_slug: "3b160e533200"
  active_states:
    - Todo
    - In Progress
    - Rework
  exclude_labels:
    - epic
    - agent:ineligible
    - agent:human-review-required
  terminal_states:
    - Closed
    - Cancelled
    - Canceled
    - Duplicate
    - Done
polling:
  interval_ms: 30000
workspace:
  # Set by `make symphony` (default: ../.symphony/workspaces/lattix-locus); override per machine.
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  timeout_ms: 120000
  after_create: |
    set -euo pipefail
    git clone --branch "main" "https://github.com/LATTIX-IO/lattix-locus.git" .
  before_run: |
    set -euo pipefail
    if [ -d .git ] && [ -z "$(git status --porcelain)" ]; then
      git fetch --all --prune
      if ! git pull --ff-only; then
        echo "WARN: git pull --ff-only failed; continuing without updating workspace" >&2
      fi
    fi
  after_run: |
    set -euo pipefail
    git status --short || true
agent:
  max_concurrent_agents: 2
  max_turns: 12
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    "Todo": 1
    "In Progress": 2
codex:
  command: bash "$SYMPHONY_CODEX_WRAPPER"
  turn_timeout_ms: 3600000
  read_timeout_ms: 30000
  stall_timeout_ms: 300000
  approval_policy: never
symphony:
  repo: "lattix-locus"
  path: "."
  remote: "https://github.com/LATTIX-IO/lattix-locus.git"
  default_branch: "main"
  linear_team_key: "FRONT"
  technologies:
    - python
    - typescript
    - react
    - docker
    - helm
    - rego
    - docs
---

# Symphony Workflow — lattix-locus

You are the coding agent for **Lattix Locus** (`lattix-locus`) running under Symphony. Symphony has selected this Linear issue and created an isolated per-issue workspace. Treat the workspace as the only place where commands and file edits may run.

## Issue context

- Issue: {{ issue.identifier }} — {{ issue.title }}
- State: {{ issue.state }}
- URL: {{ issue.url }}
- Attempt: {{ attempt }}

Use the issue description, labels, blockers, linked assets, and repository context to determine the smallest safe change. If required information is missing, leave a concise Linear comment or implementation note and stop only when genuinely blocked.

## Repository profile

- Linear team: `FRONT`
- Default branch: `main`
- Detected technology profile: python, typescript, react, docker, helm, rego, docs
- Surfaces: `apps/backend/` (control plane), `apps/workers/` + `locus_runtime/` (runtime), `apps/frontend/` (UI), `locus_tooling/` (CLI/installer), `packages/contracts/`, `policies/`, `helm/`

### Technology-specific execution spec

- `python`: 3.12+, hatchling. Typed, `ruff`-clean, `mypy --strict` on the gated scope. Domain logic pure; IO in adapters. Fail closed on auth, policy, parsing, and network.
- `typescript`/`react`: Next.js App Router, React 19 + React Compiler. Interaction tests over snapshots. No business rules duplicated from the server except for UX validation.
- `docker`/`helm`: compose and chart changes must stay renderable and must preserve the security contract asserted by `tests/unit/test_compose_auth_contract.py` and `test_helm_security_contract.py`.
- `rego`: every policy change ships with a matching test under `policies/tests/`.
- `docs`: factual and repo-specific. No fake contacts, no secrets, no unverified operational claims.

## Executable SDLC contract

`WORKFLOW.md` is not the engineering handbook and not a generic SDLC manual — that is `AGENTS.md`. This is the executable, agent-facing slice for one unattended issue run. Follow only process rules that can be acted on in this workspace.

### Linear state map

- `Backlog`: out of scope for autonomous execution; do not start implementation unless moved to an active state.
- `Todo`: queued and eligible only with `agent:eligible`. Before editing code, move or request movement to `In Progress` when tracker tooling is available.
- `In Progress`: active implementation. Keep work scoped, validated, and ready for PR handoff.
- `Human Review`: handoff state. Move here only after branch/PR, validation evidence, and final notes are complete.
- `Rework`: reviewer feedback requires another pass; re-read feedback, update the plan, revalidate, then return to `Human Review`.
- `Done`, `Closed`, `Cancelled`, `Canceled`, `Duplicate`: terminal. Do not make changes for terminal issues.

Most open `FRONT-*` issues are unrefined Epics with no leaf children. Do not start implementation from an Epic — request refinement into a Story/Task/Bug first.

### Blockers, follow-ups, and scope control

- Stop only for true blockers: missing required auth or permissions, unavailable required tooling, or ambiguity that would make execution unsafe.
- If blocked, leave a concise tracker note with what is missing, why it blocks delivery, and the exact unblock action.
- Do not expand scope for opportunistic cleanup. Recommend a separate follow-up issue for out-of-scope security, reliability, or tech-debt findings.
- For changes touching security posture, isolation, persistence, policy, the installer, or deployment surfaces, include rollback notes and threat assumptions in the handoff.

## Unattended GitHub contract

Use the local `git` command and the authenticated `gh` CLI for branch, pull-request, review, and check operations. Do not use GitHub MCP or app connectors in unattended runs — connector approval elicitations cannot be completed by the worker. If `gh auth status --hostname github.com` fails, record the blocker and stop without trying another external tool.

## Required execution flow

1. Re-read the issue and inspect current repository state before editing.
2. Confirm the issue is in an executable state, has `agent:eligible`, and has no exclusion label. If `Todo`, transition to `In Progress` when tracker tooling is available; if terminal or ineligible, stop without changing files.
3. Create or reuse a branch named from the issue identifier and short title. Never commit directly to `main`.
4. Keep changes scoped to the issue.
5. Prefer tests first for behavior changes. Preserve existing public APIs, node-type semantics, graph schemas, and CLI contracts unless the issue asks for an intentional change.
6. Never print, commit, or log secrets. `LINEAR_API_KEY` reaches Symphony through environment indirection only; do not read or echo it from `.env`.
7. Use the repo's existing tooling. Add dependencies only when justified by purpose, license, risk, and alternatives.
8. Commit with a Conventional Commit message referencing the issue identifier when practical.
9. Push the branch and open or update a pull request. The PR body declares one `Release-Impact: patch|minor|major` line per [docs/VERSIONING.md](docs/VERSIONING.md) (D-31). A new user-visible capability or setting, a changed default, a port contract version, a migration or a core-dependency major upgrade is at least `minor`: edit `VERSION`, run `lattix version sync` and add a `docs/release-notes/` fragment in the same PR. Never edit the PATCH digit; a `major` bump needs the principal.
10. Before handoff, run the repo-native validation gate plus the targeted checks below, and fix failures caused by the current change.
11. Sweep reviewer feedback on existing or updated PRs; address actionable comments or document justified pushback.
12. Move to `Human Review` only after code, tests, docs, PR metadata, validation evidence, and rollback notes are complete. `Done` means merged or explicitly marked complete by the workflow owner.

## Validation contract

Run the smallest relevant checks first, then broaden before handoff.

### Repo-native validation gate

- Windows/PowerShell runners: `.\precommit.ps1`
- Make targets (canonical): `make lint`, `make typecheck`, `make test`, `make policy-test`, `make helm-validate`
- Frontend, from `apps/frontend/`: `npm run lint`, `npm test`, `npm run build`

Prefer the documented aggregate gate over hand-assembled commands, but still run targeted tests for the changed behavior.

### Change-scoped requirements

| If you changed | You must run |
| --- | --- |
| `policies/**` | `make policy-test` |
| `helm/**` | `make helm-validate` |
| `docker-compose*.yml`, `docker/**`, `envoy/**` | `docker compose config --quiet` for both compose files |
| `apps/frontend/**` | `npm run lint`, `npm test`, `npm run build` |
| `install/**`, `locus_tooling/installer.py`, `locus_runtime/install.py` | the installer test set in `tests/unit/` |
| `locus_runtime/sandbox.py`, `security.py` | `tests/unit/test_sandbox_policy.py`, `test_tool_jail.py`, `test_biscuit_tokens.py`, `test_event_signing.py` |
| `locus_runtime/grants.py`, `gateway.py` | `tests/unit/test_biscuit_tokens.py`, `test_gateway_grants.py`, `test_gateway.py`, `apps/backend/tests/test_gateway_grants_api.py`, `tests/policy/test_gateway_opa.py` (real OPA) |
| a node type | backend executor tests **and** `apps/frontend` schema/catalog tests together |

### Known gate limitations — state these in the handoff

- `make typecheck` and CI cover only `locus_tooling/` and `locus_runtime/`. `apps/backend/` is **not** type-checked.
- CI runs `ruff check` but not `ruff format --check`. Run `ruff format` on files you touch.
- The Python suite passes only in the `pyproject.toml` `testpaths` order (`apps/backend/tests` before `tests`). If you reorder, expect cross-file state leakage — do not "fix" it by muting tests.

If validation fails, fix failures introduced by the current change. For clearly pre-existing unrelated failures, capture evidence in the handoff, reference or create a follow-up issue, and do not move to `Human Review` unless the required scope validation is green or the blocker is explicit.

## Reviewer feedback loop

- If a PR already exists for the issue or branch, inspect open review comments, failed checks, and bot feedback before adding new work.
- Treat actionable reviewer feedback as blocking until addressed in code/docs/tests or answered with concise, justified pushback.
- Re-run the relevant validation gate after feedback-driven changes and push before handoff.
- Do not return to `Human Review` while required checks fail or actionable comments remain unresolved.

## Linear handoff

If Symphony exposes the `linear_graphql` tool, use it for tracker updates instead of reading raw credentials. Add a short final comment with:

- summary of changes
- validation evidence — which gates ran and their results
- PR/branch link
- rollout or rollback notes for posture-impacting work
- blockers or follow-up risks
- any `memory-candidate` blocks proposed (see `AGENTS.md`)

## Safety posture

This workflow assumes a trusted internal agent runner with repository-scoped workspaces. Continue to fail closed for auth, policy, parsing, network, isolation, and deployment uncertainty. For changes to security posture, sandboxing, persistence, policy, or the installer, include threat assumptions and rollback guidance in the final handoff.
