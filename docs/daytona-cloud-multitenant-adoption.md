# ADR-002: Daytona Adoption for Cloud Multi-Tenant Deployment

## Status

Accepted (future-state policy)

## Date

2026-02-23

## Context

Lattix Locus is currently optimized for **local-first** development and internal network execution, with Docker Desktop + Docker Compose as the canonical runtime baseline.

As we evolve to a **cloud-deployed, multi-tenant** platform, the team needs:

- consistent, reproducible developer environments,
- isolated per-tenant/per-branch execution environments,
- secure ephemeral workspaces for engineering and agent workflow operations,
- faster onboarding and fewer local-machine drift issues,
- stronger alignment between dev, staging, and production-like topologies.

## Decision

When Lattix Locus moves to cloud multi-tenant deployment, **Daytona will become the standard developer workspace platform**.

Docker Compose remains supported for local fallback and offline work, but the default team path for cloud-era development is Daytona-managed workspaces.

## Scope

This decision applies to:

- backend/frontend development,
- agent/workflow authoring and testing,
- integration validation (RAG/MCP/A2A),
- preview/test environments per branch/PR,
- multi-tenant cloud readiness workflows.

This decision does **not** replace production orchestration (Kubernetes, managed services, or platform runtime control plane). Daytona is for dev/test workspace standardization and acceleration.

## Why Daytona in the cloud multi-tenant phase

1. **Environment consistency**
   - Eliminates local machine drift across OS/tooling.
   - Codifies dev environments from repo configuration.

2. **Ephemeral isolation**
   - Per-branch/per-feature workspaces reduce cross-team conflicts.
   - Stronger separation when testing tenant-scoped behavior.

3. **Operational speed**
   - Faster onboarding and reproducible setup.
   - Easier debugging with shared, reproducible environments.

4. **Security posture alignment**
   - Centralized workspace policy controls.
   - Better controls for credentials and ephemeral secret exposure compared with ad hoc local setups.

5. **Multi-tenant development realism**
   - Closer parity with cloud networking, service dependencies, and tenant isolation models.

## Adoption gates (must be true before switching default)

Daytona becomes default only when all gates below are met:

- Cloud multi-tenant control plane is active for at least one non-prod environment.
- Baseline workspace template is validated by Platform + Security.
- RAG/MCP/A2A integration tests run successfully in Daytona workspaces.
- Secrets handling standard is implemented (no raw secrets in repo, workspace-scoped secret injection in place).
- Branch preview workflow and teardown automation are documented and tested.

## Operating model

### Current phase (local-first)

- Default: Docker Desktop + `docker-compose.local.yml` for lightweight iteration, or `docker-compose.yml` for the secure full stack.
- Daytona: optional experimentation only.

### Cloud multi-tenant phase (this ADR applies)

- Default: Daytona workspace for daily engineering flow.
- Docker Desktop: fallback for offline/local incident debugging.

## Security and tenancy requirements in Daytona workspaces

All Daytona workspace templates must enforce:

- tenant-aware configuration boundaries,
- least-privilege credential injection,
- short-lived secret/session material,
- local/private service boundary policy for non-prod integrations,
- auditability for workspace lifecycle events.

For A2A/MCP/RAG testing in Daytona:

- A2A signed-message and replay protections must remain enabled,
- MCP server allowlists and capability restrictions remain enforced,
- RAG trusted source and network restrictions remain enforced by policy.

## Implementation requirements

1. Create Daytona workspace profile(s):
   - `frontend-backend-dev`
   - `integration-rag-mcp-a2a`
   - `ci-preview`

2. Codify setup scripts:
   - dependency bootstrap,
   - environment validation,
   - health checks,
   - teardown cleanup.

3. Add parity checks:
   - verify key workflows run identically in Docker local vs Daytona cloud workspace.

4. Documentation updates:
   - onboarding guide,
   - incident/debug fallback path,
   - secrets and access runbook.

## Rollout plan

- **Phase 0 (now):** Docker default, Daytona optional pilot.
- **Phase 1:** 20–30% of engineering workflows on Daytona, collect performance/reliability metrics.
- **Phase 2:** Default to Daytona for cloud multi-tenant development; Docker as fallback.
- **Phase 3:** Enforce policy gates in CI to ensure all cloud-bound changes are validated in Daytona.

## Success metrics

- Onboarding time reduced by >=40%.
- Environment-related setup failures reduced by >=60%.
- Integration parity pass rate (RAG/MCP/A2A) >=95% across workspace templates.
- Branch preview availability >=99% for active PR windows.

## Risks and mitigations

- **Risk:** Tooling lock-in or workflow disruption.
  - **Mitigation:** Keep Docker fallback path and parity checks.

- **Risk:** Secret sprawl in new workspace model.
  - **Mitigation:** Enforce centralized secret injection and short-lived tokens.

- **Risk:** Performance regressions for local-heavy contributors.
  - **Mitigation:** Maintain documented hybrid model (Daytona default, Docker fallback).

## Consequences

- Positive:
  - reproducibility and cloud readiness improve,
  - stronger tenancy testing discipline,
  - easier collaboration and debugging.

- Trade-offs:
  - initial platform engineering overhead,
  - workspace policy governance needed,
  - dual-path support (Daytona + Docker fallback) during transition.

## Decision summary

For local-first today, Docker remains canonical.
For cloud multi-tenant tomorrow, Daytona becomes the standard developer workspace platform, with Docker retained as controlled fallback.
