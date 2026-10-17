# Live run-state review adapter

Production backend source of truth:
- `Running`: execution active; wait for server stream or authoritative fetched state.
- `Needs Review` plus required+pending and matching artifact ID/version: operator can accept outcome or request changes. **This is not an action authorization grant.**
- `Blocked`: terminal guardrail refusal; never show outcome approval even if a malformed/stale `approvals.pending` flag is present. Display under Home "Needs attention", not "Running now".
- `Done`, `Failed`, `Archived`: terminal outcomes, no pending approval actions.
- `stream_closed`: connection rollover; reconnect from cursor. Missing terminal frame: reconnect and poll. Never infer success from dropped stream.

The frontend `getOutcomeReviewGate` only hides disallowed actions. Backend PR #123 enforces the same version-pinned review state under a lock. Do not merge or enable this UI ahead of backend enforcement if the two contracts diverge.

The canonical `TaskIntent`/`TaskPlan` compiler in `locus_runtime` validates structural DAG admission. `POST /workflow-runs` currently launches a prompt and does **not** bind an approved compiled TaskPlan revision. The prototype plan-review surface must not be exposed as a production authorization gate until the backend provides plan creation, user confirmation, signed/replay-resistant admission and Gateway binding. This PR reconciles outcome/lifecycle only.

Tests: `cd apps/frontend && npm test -- src/lib/run-outcome-review.spec.ts && npm test -- src/components/home-screen.spec.tsx`.
