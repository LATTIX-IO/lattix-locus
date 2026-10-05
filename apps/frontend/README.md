# Locus MVP Frontend

Next.js frontend for the Locus desktop app (and the hosted console).

## Navigation

One navigation, no modes (LOCUS-353): **Home** (task composer, running agents), **Activity** (runs, traces, artifacts), **Memory**, **Library** (skills, playbooks, workflows, agents, connections, knowledge, templates, guardrails, node library, releases) and **Settings** (one page: engines, connections, computer use, policies & autonomy, loop & Linear, updates, observability, appearance). See `FRONTEND.md` for the route map and the redirects from the old routes.

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

If the backend is unavailable, the UI says so (with a retry); it never substitutes placeholder data.

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

Library endpoints:

- `GET/POST workflow-definitions` + publish
- `GET/POST agent-definitions` + publish
- `GET node-definitions`
- `GET/POST guardrail-rulesets` + publish
