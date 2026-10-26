# LOCUS-459 A — typed TaskPlan review projection (non-executable)

## Purpose

This is the first independent, safe backend implementation increment for the
Home -> Plan -> Run -> Outcome journey. It presents a **structurally valid
TaskPlan proposal** in a deterministic version-bound format. It is not a plan
authorization, executable approval, grant, persisted signed receipt or launch.

Source contracts: `locus_runtime.task_graph_contract` and
`locus_runtime.task_plan_compiler`. This module does not fork the compiler,
authorize any endpoint, instantiate AgentRuntime, or use the Open Design
simulator.

## Trust ownership

The untrusted model/client may submit only the bounded `contract_version`
and `nodes` document accepted by `compile_task_plan`.

`TrustedReviewContext` must be built by an authenticated server adapter from
the verified actor, validated intent, tenant, immutable project ID and its
classification floor, current policy digest/epoch, current catalog digest,
and server-allocated monotonically increasing review revision. A caller
constructing the Python dataclass is **not** evidence of any such checks.
Do not expose this constructor or its values as model-callable inputs.

The preview binds the full existing compiler digest to that trusted
context via a canonical SHA-256 digest, which can detect a stale display.
The digest is **not cryptographically signed**, is not a bearer token and
must never be accepted by dispatch or the Gateway as authority.

## Projection fields

`build_plan_review(...).to_dict()` returns:
- schema `locus.task-plan-review/1.0`, revision, plan/review digests;
- actor, area, goal/criteria, tenant/project namespace, classification and floor;
- policy and catalog snapshot digests/epoch;
- parent requested capability/risk/budget ceiling, per-node requested
  authority/budget, named deliverables/verification, deterministic DAG waves;
- explicit `agent_definition: null`, `engine: null`,
  `policy_decision: not_evaluated` on each node;
- admission: structural passed, policy **not performed**, no human review,
  no Gateway grant, `execution_admitted: false`.

`matches_current_review` is for UI staleness only: it cannot approve or
launch anything. Any changed proposal, actor, area, tenant/project, policy,
catalog, classification, or monotonic revision yields a different binding.

## Deliberately absent

No authenticated API route, version database, tenant membership checks,
resource namespace grants, per-node agent/engine resolution, actual policy
evaluation, receipt signing/expiry, plan approval, idempotent launch, Gateway
session or PEP integration. Those depend on LOCUS-415/433 and later LOCUS-459
increments. Do not expose the user-visible action as "Approved" or "Start
run" based on this preview. The live `POST /workflow-runs` path is unchanged.

## Tests and acceptance

```sh
python -m pytest -q tests/unit/test_task_plan_review.py
ruff check locus_runtime/task_plan_review.py tests/unit/test_task_plan_review.py
```

The bounded negative suite covers stale revisions, actor/tenant/namespace
and classification changes, malformed trusted context, proposed extra
authorization fields, graph cycles and oversubscribed budgets. CI and
independent security review are required.

Future production integration must extend authorization at real enforcement
boundaries, not clone simulated policy decisions into React.
