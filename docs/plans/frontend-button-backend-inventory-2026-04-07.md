# Frontend Button and Backend Integration Inventory

Date: 2026-04-07

## Goal

Review the application frontend, inventory the user-facing actions that are expected to interact with backend state, and define the plan required to make every meaningful button and persisted workflow reliably backend-backed and operational.

This document focuses on controls that either:

- mutate backend state
- trigger execution
- depend on backend state to be trustworthy
- appear operational to a user but are currently dead, partial, or misleading

Purely local presentation controls such as tab switches, panel collapse buttons, search filters, auto-layout, and clipboard actions are intentionally excluded unless they imply persistence or backend action.

## Review Method

- Reviewed the frontend API client in `apps/frontend/src/lib/api.ts`
- Reviewed interactive pages and components in `apps/frontend/src/app` and `apps/frontend/src/components`
- Reviewed matching backend routes in `apps/backend/app/main.py`
- Re-checked the inventory against the newer frontend/backend changes that landed after the initial pass
- Preserved earlier validation notes at the bottom of this document where they still reflect the original review run

## Executive Findings

### 1. Core operator and run lifecycle flows are wired correctly

These flows use strict backend mutations and appear structurally sound:

- local login, register, and logout
- start workflow
- start task
- send follow-up message
- rename session
- archive run
- submit approval decisions
- save and publish workflow definitions
- save and publish agent definitions
- save playbooks
- validate and run graph tests
- collaboration join and sync
- save and delete runtime provider credentials

### 2. The major false-success mutation paths have been corrected

The most important backend-backed write flows now use strict failure semantics instead of silent fallbacks.

Corrected areas:

- guardrail save, publish, and delete
- platform settings save
- integration save, test, and delete
- node delete

Current impact:

- these controls now surface backend failures instead of fabricating success
- node deletion now fails closed with an explicit `501` until custom node lifecycle support actually exists

### 3. Several previously dead controls are now either wired or intentionally constrained

Resolved or improved:

- release-page `Promote` buttons
- guardrail editor `Risk group filter`

Still intentionally constrained:

- node-library custom node save/publish remains unavailable because backend lifecycle support does not exist yet

### 4. The playbook template routing bug has been fixed

Playbook template instantiation now routes into the playbook builder surface instead of the workflow builder route.

### 5. Node management is not feature-complete end-to-end

The frontend now makes the incomplete state clearer, but the backend still only exposes:

- list node definitions
- delete node definitions as an explicit `501 read-only` response

There is no corresponding frontend API or backend route for create, update, publish, activate, or rollback of node definitions.

### 6. Release management is now wired to backend revision activation

The release workspace now loads revision history and calls backend activation and rollback routes for workflow, agent, and guardrail definitions.

### 7. Regression coverage is materially stronger than the initial review snapshot

The frontend test suite now includes direct coverage for:

- guardrail save payloads and publish failures
- integration save, test, and delete failure handling
- fail-closed typed deletion UX
- workflow, agent, and guardrail release actions
- shared and builder settings save-failure surfacing

## Inventory

| Surface | Primary actions | Frontend path(s) | Backend/API dependency | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Authentication | Login, register, sign out | `apps/frontend/src/components/auth/local-auth-panel.tsx`, `apps/frontend/src/components/app-shell.tsx` | `/auth/login`, `/auth/register`, `/auth/logout` via `strictFetch` | Wired | Good failure semantics. |
| App shell chrome | Feedback, operator menu | `apps/frontend/src/components/app-shell.tsx` | `mailto:` for feedback plus session/logout state | Wired | The earlier dead `Docs` and `Notifications` buttons are no longer rendered. |
| Workflow catalog | Open, Start | `apps/frontend/src/app/workflows/start/page.tsx` | `/workflows/published`, `/workflow-runs` | Wired | Start action is strict and error-aware. |
| Task kickoff | Start task, Open run | `apps/frontend/src/components/task-kickoff-composer.tsx` | `/workflow-runs` | Wired | Uses strict run creation. |
| Inbox/session sidebar | Open session, rename, cancel | `apps/frontend/src/components/navigation/user-console-sidebar.tsx` | `/workflow-runs`, `/inbox`, `PATCH /workflow-runs/{id}` | Wired | Rename path is strict and refreshes local list. |
| Run workspace | Send follow-up, archive, approve/request changes | `apps/frontend/src/components/user-chat-workspace.tsx`, `apps/frontend/src/components/run-followup-composer.tsx`, `apps/frontend/src/components/run-archive-button.tsx` | `/workflow-runs`, `/workflow-runs/{id}/archive`, `/approvals`, stream/events routes | Wired | Strongest end-to-end user flow in the app. |
| Run console | Refresh, archive, follow-up, approve/request edits | `apps/frontend/src/components/run-conversation-console.tsx` | run detail/events plus `/approvals` | Wired | Good operational coverage. |
| Workflow builder | Save draft, publish, validate, run test, collaboration sync | `apps/frontend/src/app/builder/workflows/[id]/page.client.tsx`, `apps/frontend/src/components/studio-full-canvas.tsx` | workflow save/publish, graph validate/run, collab, memory, observability | Wired | Save/publish mutations use strict fetch paths. |
| Agent builder | Save draft, publish, save and return | `apps/frontend/src/app/builder/agents/[id]/page.client.tsx`, `apps/frontend/src/components/studio-full-canvas.tsx` | agent save/publish, graph validate/run, collab, memory, observability | Wired | Good mutation semantics. |
| Playbook builder | Save | `apps/frontend/src/app/builder/playbooks/[id]/page.client.tsx`, `apps/frontend/src/components/studio-full-canvas.tsx` | `/playbooks` via strict save | Wired | Save path is correct. Publish lifecycle is handled elsewhere. |
| Template catalog | Instantiate agent/workflow/playbook template | `apps/frontend/src/app/builder/templates/page.tsx` | template instantiate routes | Wired | Playbook template instantiation now routes to `/builder/playbooks/{id}`. |
| Builder library actions | Open, publish, unpublish, archive | `apps/frontend/src/components/builder-library-actions.tsx` | workflow/agent/playbook lifecycle routes | Wired | Uses strict lifecycle mutations. |
| Security scope editor | Save agent/workflow policy overrides | `apps/frontend/src/components/security-scope-editor.tsx` | underlying agent/workflow save routes plus policy reads | Wired | Save behavior depends on strict parent save callbacks. |
| Guardrail editor | Save draft, publish | `apps/frontend/src/components/guardrail-editor.tsx` | `/guardrail-rulesets`, `/guardrail-rulesets/{id}/publish` | Wired | Uses strict mutation semantics and persists the ruleset config payload. |
| Guardrail editor secondary controls | Risk group filter, blocklists section | `apps/frontend/src/components/guardrail-editor.tsx` | Guardrail config payload | Wired | Filter now changes the visible control list, and blocklist fields are persisted in `config_json`. |
| Integrations manager | Save integration, test, delete | `apps/frontend/src/components/integrations-manager.tsx` | `/integrations`, `/integrations/{id}/test`, `/integrations/{id}` | Wired | Mutation calls now fail loudly instead of returning fallback success payloads. |
| Builder settings workspace | Save policy envelope, save/remove runtime backends | `apps/frontend/src/components/builder-settings-workspace.tsx` | `/platform/settings`, runtime provider routes | Wired | Platform settings and runtime provider mutations both use strict backend semantics. |
| Organization settings | Save changes, reset hybrid profile | `apps/frontend/src/app/settings/page.tsx` | `/platform/settings` | Wired | Save failures are surfaced in the shared settings shell. |
| Node library | Delete node, save draft, publish node package | `apps/frontend/src/app/builder/nodes/page.tsx`, `apps/frontend/src/components/typed-delete-button.tsx` | `/node-definitions` read/delete only | Constrained | The UI now states that custom node save/publish is intentionally disabled, and deletion fails closed with backend `501` because lifecycle support does not exist yet. |
| Releases page | Promote workflow, promote agent, rollback revisions | `apps/frontend/src/app/builder/releases/page.tsx`, `apps/frontend/src/components/releases-workspace.tsx` | definition activation and rollback routes | Wired | Revision history, promote, activate, and restore are now connected to the backend. |

## Findings With File Evidence

### Current Open Gap

1. Node-definition lifecycle remains intentionally incomplete.

- `apps/frontend/src/app/builder/nodes/page.tsx` now presents custom node save/publish as intentionally unavailable rather than pretending lifecycle support exists.
- `apps/backend/app/main.py:18136` returns `501` for node deletion and explicitly documents that custom node deletion is not supported.

This is an honest UI/backend contract improvement, but it still leaves a real product gap if custom nodes are meant to be first-class definitions.


### Closed Since The Initial Pass

1. Strict mutation semantics now cover the previously unsafe write paths.

- `apps/frontend/src/lib/api.ts` now uses `strictFetch` for guardrail, platform settings, integration, runtime provider, graph run, and node-delete mutation helpers.
- The UI surfaces backend errors instead of synthesizing success responses.

2. Release management is now operational.

- `apps/frontend/src/app/builder/releases/page.tsx` now renders `ReleasesWorkspace`.
- `apps/frontend/src/components/releases-workspace.tsx` loads revisions and calls activate/rollback endpoints.

3. The playbook template routing bug is fixed.

- `apps/frontend/src/app/builder/templates/page.tsx:92` now routes playbook template instantiation to `/builder/playbooks/${created.id}`.

4. The guardrail filter and policy fields are now functional.

- `apps/frontend/src/components/guardrail-editor.tsx` filters the visible controls by selected group.
- Guardrail blocklist and policy-text fields are included in the saved `config_json` payload.

5. Regression coverage now protects the hardened UI contract.

- `apps/frontend/src/components/guardrail-editor.spec.tsx` covers persisted guardrail payloads and publish-error surfacing.
- `apps/frontend/src/components/integrations-manager.spec.tsx` covers integration save/test/delete behavior.
- `apps/frontend/src/components/typed-delete-button.spec.tsx` covers exact-name confirmation and fail-closed delete UX.
- `apps/frontend/src/components/releases-workspace.spec.tsx` now covers workflow, agent, and guardrail release actions.

## Recommended Plan

### Phase 1: Decide whether custom nodes are a real supported product surface

Objective: either finish node-definition lifecycle support or keep the current read-only posture intentionally narrow.

Actions:

1. If custom nodes should ship:
   - add backend create, update, publish, archive, activate, and rollback routes
   - add corresponding frontend API helpers and lifecycle UI
   - replace the disabled messaging in the node library with real mutations

2. If custom nodes are not yet a product promise:
   - keep the current disabled/read-only messaging
   - document the scope explicitly in product and builder documentation

Definition of done:

- the node library either supports full lifecycle management or clearly remains a read-only catalog/editor prototype

### Phase 2: Expand release and node lifecycle coverage

Objective: keep the newly wired lifecycle surfaces trustworthy over time.

Actions:

1. Keep the new frontend regression coverage for release activation and rollback across workflow, agent, and guardrail entities.
2. Keep backend contract tests for node-definition read-only behavior until full lifecycle support exists.
3. If node lifecycle is implemented, add end-to-end tests that cover save, publish, and delete/restore semantics.

Definition of done:

- release and node lifecycle surfaces cannot silently regress back into aspirational UI

### Phase 3: Keep the mutation contract hard-failing and observable

Objective: preserve the gains from the strict-fetch conversion and avoid reintroducing false-success behavior.

Actions:

1. Keep write paths on `strictFetch` unless a read-style fallback is explicitly justified.
2. Add or maintain tests that assert backend failures surface through settings, integrations, guardrails, and release controls.
3. Keep disabled UI states paired with explicit backend behavior like the node-definition `501` response.

Definition of done:

- no backend-backed write surface can silently claim success after a failed request

### Phase 4: Add operational verification coverage

Objective: ensure button wiring stays correct over time.

Actions:

1. Add a frontend integration test matrix for:
   - auth
   - run creation
   - follow-up send
   - approval submit
   - workflow/agent/playbook save and publish
   - guardrail save/publish
   - integration save/test/delete
   - platform settings save

2. Add backend contract tests for routes that are surfaced by buttons.

3. Add a simple "button inventory" review checklist to PR review:
   - does the control call a real API helper?
   - does that helper use strict mutation semantics?
   - does the UI surface backend failure clearly?

4. Add an end-to-end smoke flow that validates:
   - login
   - start task
   - send follow-up
   - approve run
   - save/publish a workflow
   - save a runtime backend

Definition of done:

- there is automated coverage for every critical backend-backed button path
- no future placeholder button reaches production unnoticed

## Suggested Work Order

1. Decide whether custom node lifecycle is a real near-term product requirement
2. If yes, implement backend and frontend node-definition lifecycle support end-to-end
3. If not, preserve the current read-only posture and document it clearly
4. Preserve release activation/rollback and node-definition read-only coverage as the lifecycle evolves
5. Keep strict mutation-path tests in place for settings, integrations, guardrails, and delete flows

## Validation Notes

- Frontend test run: `34` files passed, `160` tests passed
- Frontend typecheck: `npx tsc --noEmit` passed
- Backend pytest: not runnable in the current shell because this machine only exposes Python `3.9.6`, while the repo requires Python `>=3.12` and does not have `pytest` installed in the active interpreter
- Production build: `next build` starts successfully after the transient lock is cleared, but it still did not produce a final success or failure result within the observed shell window
