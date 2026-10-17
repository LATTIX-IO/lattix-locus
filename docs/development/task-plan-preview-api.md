# LOCUS-461 — authenticated read-only TaskPlan preview (staged)

This introduces a guarded backend adapter for **structural, non-executable**
TaskPlan previews. Source: `locus_runtime.task_plan_review` from PR #125
(LOCUS-460). The endpoint is never a reviewed plan receipt or launch.

## Endpoint

`POST /task-plans/preview` accepts exactly `project_id`, `goal`,
`acceptance_criteria`, `proposal`; nested proposer JSON can contain only
strict `contract_version` and `nodes` fields accepted by
`compile_task_plan`. Duplicate JSON keys, oversized bodies and untrusted
identity/policy/grant fields are rejected before compilation.

A valid signed identity is required even in normally permissive profiles.
Static shared bearer tokens and actor-only headers cannot establish review
ownership. Authenticated `principal_id` and `tenant` come from the existing
request authn context; for local-native authenticated operator bootstrap,
tenant `local` is server-derived and cannot be overridden.

## Server source of truth and limitations

A privileged administrator supplies
`LOCUS_TASK_PLAN_REVIEW_REGISTRY_PATH`, a locally secured JSON file using
schema `locus.task-plan-context-registry/1.0`.
The client cannot choose this path or write to it; if it is unavailable,
symlinked, ambiguously defined, or improperly permissioned, return 503.
Example admin-controlled synthetic fixture (not installed by this PR):

```json
{
  "schema_version": "locus.task-plan-context-registry/1.0",
  "projects": [{
    "tenant_id": "local",
    "project_id": "research-1",
    "allowed_principals": ["user:local/operator"],
    "area": "personal",
    "classification_floor": "internal",
    "effective_classification": "internal",
    "capabilities": ["sources.read"],
    "risk_ceiling": "R2",
    "budget": {"actions": 5, "tokens": 3000, "wall_seconds": 300, "spend_microunits": 100000},
    "policy_epoch": 1,
    "revision": 1
  }]
}
```

The project configuration is a **trusted preview envelope**, not a Gateway
capability grant. Its policy fingerprint is derived from backend-owned
configuration and active ruleset *metadata*, not an attested effective PDP
bundle. Current published agent definition IDs/versions are similarly hashed
for staleness detection without exposing secrets. The result explicitly says
policy evaluation **not performed**, agent/engine bindings **not performed**,
human review **not recorded**, Gateway grant **not granted**, and
`execution_admitted=false`.

**Default OFF:** `LOCUS_TASK_PLAN_PREVIEW_ENABLED=1` and the configured
registry path are required. A missing project or unauthorized tenant returns
the same 404, and a missing server-owned registry returns 503.

Do not use this preview to create a run. No platform-wide project membership
registry, durable revision ledger, persistent CAS, signed receipt, real PDP
decision, per-node Gateway session or launch endpoint ships here. Those require
LOCUS-462/463, LOCUS-415 and LOCUS-433. The admin-managed file is an interim
read-only authority ceiling; production tenant management must eventually
replace it with server-owned persisted project ACL/policy data.

## Regression gates

`python -m pytest -q tests/unit/test_task_plan_preview_api.py`

Additionally test real signed JWT/OIDC authentication, local-native bootstrap,
different tenants/roles, 320–1280 responsive frontend consumers, route/shell
proof inventories, no policy egress and exact-head CI. Simulated JWT context in
unit tests is NOT evidence that deployed tokens were validated.
