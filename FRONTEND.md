# Lattix Locus Frontend Guidance

The Locus frontend is a single Next.js app (`apps/frontend/`) for the one person who installed Locus: one shell, one navigation, no user/builder modes (LOCUS-353). The same person runs and audits work and composes agents, workflows and guardrails. Path-scoped rules in `.github/instructions/lattix-frontend.instructions.md` apply automatically and take precedence on the mechanics; this file covers what is specific to Locus.

## Stack

Next.js 16.2.1 (webpack, not turbopack) · React 19.2.3 with the React Compiler babel plugin · TypeScript 5.9.3 · Tailwind 4 · ReactFlow 11 · vitest 4 + Testing Library + jsdom · eslint 9 flat config.

Commands, run from `apps/frontend/`:

    npm run dev   # start dev server (separate terminal)
    npm run lint && npm test && npm run build

## Surface map

| Area | Routes | Purpose |
| --- | --- | --- |
| Home | `/home` (`/` redirects here) | Start a task; see what is running and what waits on you |
| Activity | `/activity` (`?session=` opens a run), `/activity/traces`, `/artifacts`, `/workflows/start`, `/workflows/[id]` | Runs, traces and outputs |
| Memory | `/memory` | Memory layers and a run's memory, as the backend reports them |
| Library | `/library` and its `skills`, `playbooks`, `workflows`, `agents`, `connections`, `knowledge`, `templates`, `guardrails`, `nodes`, `releases` children | What the agents can use; compose and version definitions |
| Settings | `/settings?section=engines\|connections\|computer-use\|policies\|loop\|updates\|observability\|appearance` | The one settings page |
| Auth | `/auth` | Web/hosted profiles only. The desktop app never shows it: the loopback operator is signed in |

There are no modes and no role gating in the UI; the backend authorizes every call. The navigation is `navigation/nav-config.ts` (`PRIMARY_NAV`). Old routes (`/inbox`, `/runs/:id`, `/tasks/:id`, `/playbooks`, `/guardrails`, `/targets`, every `/builder/*`) redirect in `next.config.ts` and stay working as links.

## Rules specific to this app

- **Node changes are three-part.** Adding or changing a node type requires the backend executor, the catalog entry in `lib/locus-node-catalog.ts`, and the config schema in `lib/locus-node-schema.ts`. A change that lands only in the UI is incomplete.
- **Config forms are schema-driven.** Do not hand-roll a node config form. Extend `node-config-schemas.ts` / `locus-node-schema.ts` so validation stays shared.
- **Security scope is UI-visible.** `security-scope-editor.tsx` and `classification-banner.tsx` exist so posture is legible. Never render a definition's actions without its scope and classification context.
- **Never re-implement authorization client-side.** The backend resolves security policy (`/agent-definitions/{id}/security-policy`, `/workflow-definitions/{id}/security-policy`). The UI reflects decisions; it does not make them.
- **Destructive actions are typed.** Use `typed-delete-button.tsx` for deletes and archives. Do not add a bare confirm.
- **Run progress streams over SSE** (`text/event-stream`). Handle stream drop explicitly — a disconnected stream must never render as a completed run. Show a reconnect or stale-data affordance.
- **Handle all four states.** Loading, empty, error, and retry — for every data surface. `loading.tsx`, `error.tsx`, and `not-found.tsx` exist at the app level; route-level surfaces still need their own.
- **API access goes through `lib/api.ts`.** One client, one error-mapping path. No ad-hoc `fetch` in components.
- **No silent fallbacks.** Reads throw on failure (`strictFetch`); only "not found" is a normal answer (`strictFetchOrNull`), and a 401 from `/auth/session` means signed out. Never substitute placeholder data for a failed call, and never ship demo figures: show real data, an honest empty state, or the error with a retry.
- **Widening goes through the desktop confirmation path.** On the desktop app every capability-widening call goes through `lib/api.ts`, which routes it to the shell's native dialog (`confirm_action`, LOCUS-357; `confirm_browser_tier`, LOCUS-350). Never send a widening change as a plain `fetch`. New widening endpoints are classified in `request_security._SHELL_PROOF_RULES` and mirrored in `lib/desktop-confirmation.ts` and the shell's `shell_actions.rs`.
- **One design system (P31).** Build screens from `components/ui/*` (shadcn/ui on Radix, MIT) and lucide icons. The shadcn token names map onto the HSL tokens in `app/globals.css`. Add a missing primitive to `components/ui` first, then use it.
- **Cognition is inspectable.** The run views must surface goal, evidence, assembly, and commitment state. (The unused `run-conversation-console.tsx` was removed in LOCUS-353; the Activity run view is where this belongs next.)

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
