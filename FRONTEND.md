# Lattix xFrontier Frontend Guidance

The xFrontier frontend is a single Next.js app (`apps/frontend/`) serving two audiences from one shell: **operators** who run and audit work, and **builders** who compose agents, workflows, and guardrails. Path-scoped rules in `.github/instructions/lattix-frontend.instructions.md` apply automatically and take precedence on the mechanics; this file covers what is specific to xFrontier.

## Stack

Next.js 16.2.1 (webpack, not turbopack) · React 19.2.3 with the React Compiler babel plugin · TypeScript 5.9.3 · Tailwind 4 · ReactFlow 11 · vitest 4 + Testing Library + jsdom · eslint 9 flat config.

Commands, run from `apps/frontend/`:

    npm run dev   # start dev server (separate terminal)
    npm run lint && npm test && npm run build

## Surface map

| Area | Routes | Purpose |
| --- | --- | --- |
| Operator | `/`, `/workflows`, `/workflows/[id]`, `/workflows/start`, `/runs/[id]`, `/artifacts`, `/inbox`, `/guardrails`, `/targets`, `/settings` | Run work, approve gates, inspect results |
| Builder | `/builder` and its `agents`, `workflows`, `nodes`, `guardrails`, `integrations`, `observability`, `playbooks`, `releases`, `templates`, `settings` children | Compose and version definitions |
| Auth | `/auth` | Generic OIDC portal pointing at the configured provider's sign-in/sign-up URLs |

Mode switching between operator and builder is a first-class control (`mode-switch.tsx`), not a hidden route difference.

## Rules specific to this app

- **Node changes are three-part.** Adding or changing a node type requires the backend executor, the catalog entry in `lib/frontier-node-catalog.ts`, and the config schema in `lib/frontier-node-schema.ts`. A change that lands only in the UI is incomplete.
- **Config forms are schema-driven.** Do not hand-roll a node config form. Extend `node-config-schemas.ts` / `frontier-node-schema.ts` so validation stays shared.
- **Security scope is UI-visible.** `security-scope-editor.tsx` and `classification-banner.tsx` exist so posture is legible. Never render a definition's actions without its scope and classification context.
- **Never re-implement authorization client-side.** The backend resolves security policy (`/agent-definitions/{id}/security-policy`, `/workflow-definitions/{id}/security-policy`). The UI reflects decisions; it does not make them.
- **Destructive actions are typed.** Use `typed-delete-button.tsx` for deletes and archives. Do not add a bare confirm.
- **Run progress streams over SSE** (`text/event-stream`). Handle stream drop explicitly — a disconnected stream must never render as a completed run. Show a reconnect or stale-data affordance.
- **Handle all four states.** Loading, empty, error, and retry — for every data surface. `loading.tsx`, `error.tsx`, and `not-found.tsx` exist at the app level; route-level surfaces still need their own.
- **API access goes through `lib/api.ts`.** One client, one error-mapping path. No ad-hoc `fetch` in components.
- **`lib/mock-data.ts` is for offline development only.** It must never become a fallback that hides a failed API call from the operator.
- **Cognition is inspectable.** `run-conversation-console.tsx` surfaces goal, evidence, assembly, and commitment state. New cognitive columns extend this view rather than adding a parallel one.

## Shared Lattix UX baseline

- **Accessibility:** target WCAG 2.2 AA. Semantic HTML, keyboard navigation, visible focus, labels; ARIA only when needed.
- **Navigation:** consistent shell — top bar, primary navigation, content region, utility zone. Owned by `app-shell.tsx` and `navigation/left-nav.tsx` + `nav-config.ts`.
- **Layout rhythm:** tokenized spacing on a 4px grid.
- **Design tokens:** semantic tokens for color, typography, spacing, radius, elevation, and motion. Tokens live in `app/globals.css`; do not inline raw values.
- **Responsive:** document breakpoints and information-priority changes.
- **Motion:** tokenized and purposeful. Never encode business or security state in animation alone.
- **Observability:** preserve correlation and request identifiers across UI-triggered platform actions. Never emit secrets, tokens, session IDs, or sensitive payloads to client logs, analytics, or session replay.

## Testing

- Interaction tests over implementation-detail snapshots.
- Cover approval gates, security-scope editing, node validation, error states, and the API boundary.
- Do not weaken an existing test to make a change pass.
- Run `npm test` before handoff. Note that the Python suite is currently red at collection (see `QUALITY_SCORE.md`); the frontend suite is independent of that.
