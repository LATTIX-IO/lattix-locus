# Locus MVP Frontend

Dual-mode Next.js frontend for local-first orchestration operations and workflow building.

## Modes

- **User Mode** (Ops/day-to-day): inbox, workflows, artifacts, and shared settings.
- **Builder Mode** (Power-user): agent studio, workflow studio, templates, playbooks, observability, integrations, node library, guardrails, releases, and builder settings.

## Local development

```bash
npm install
npm run dev
```

App runs on `http://localhost:3000`.

If you see `'next' is not recognized`, your local dependencies are incomplete. Reinstall from this folder:

```bash
npm ci
```

## Backend integration

Set backend API base URL using environment variable:

```bash
NEXT_PUBLIC_API_BASE_URL=/api
```

If the backend is unavailable, the UI falls back to local mock data so workflows remain explorable in local-first mode.

## API coverage (frontend client)

The client layer (`src/lib/api.ts`) is wired for:

- `GET /workflows/published`
- `POST /workflow-runs`
- `GET /workflow-runs`
- `GET /workflow-runs/{id}`
- `GET /workflow-runs/{id}/events`
- `POST /artifacts/{id}/versions`
- `POST /approvals`
- `GET /inbox`

Builder mode endpoints:

- `GET/POST workflow-definitions` + publish
- `GET/POST agent-definitions` + publish
- `GET node-definitions`
- `GET/POST guardrail-rulesets` + publish
