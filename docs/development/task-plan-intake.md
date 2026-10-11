# LOCUS-411 — Untrusted task-plan intake (increment 2)

Status: implemented **structural intake only**. This is a follow-on to [ADR-0002](../adr/ADR-0002-general-purpose-meta-harness.md), stacked onto PR #82, tracked under [LOCUS-411](https://linear.app/lattix/issue/LOCUS-411).

## Ownership and trust

A human/user-facing admission path owns `TaskIntent` (including `requester_id`, `area`, `goal` and acceptance criteria) and `TaskAuthority` (capability ceiling, R0–R3 ceiling and aggregate reservation). The LLM may only propose a bounded `{"contract_version":"1.0","nodes":[...]}` object. `compile_task_plan` refuses unexpected root/node/budget fields, duplicate keys, NaN, dangerous size/encoding, missing references, cycles, capability escalation and oversubscribed action/token/spend limits. Strings cannot be cast into budgets; booleans are rejected. The canonical SHA-256 revision digest **includes the trusted principal, area and parent authority** as well as the proposal; do not use it as a cryptographic signature.

Only deterministic DAG wave scheduling and subset checks are computed. An accepted proposal is **not authorized execution** and is not a user-approved plan revision. Untrusted node labels, proposed expertise and criteria have no authority.

## Minimal example

```python
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan

intent = TaskIntent("Prepare a referenced report", "personal", "principal-id", ("Citations checked",))
authority = TaskAuthority(frozenset({"search"}), "R2", TaskBudget(10, 4000, 300, 100000))
proposal = {
    "contract_version": "1.0",
    "nodes": [{
        "node_id": "research",
        "depends_on": [],
        "capabilities": ["search"],
        "risk_ceiling": "R1",
        "budget": {"actions": 3, "tokens": 1500, "wall_seconds": 120, "spend_microunits": 30000},
        "deliverables": ["referenced notes"],
        "acceptance_criteria": ["Every material claim has supporting citations"],
    }],
}
compiled = compile_task_plan(proposal, intent=intent, parent_authority=authority)
assert compiled.waves == (("research",),)
```

## Execution safety blocker — do not bypass

The current `locus_runtime.gateway.Capabilities` has tool/resource and budget scopes, but **no general per-run maximum action risk field**. The compiler's `TaskAuthority.risk_ceiling` therefore cannot reliably be enforced for any proposed pod just by supplying a `RuntimeRequest` to `AgentRuntime`: a tool action classified above the pod ceiling might still pass a broader grant. The next execution increment **MUST** first implement per-pod R-class enforcement at the actual PEP (with decision tests on model/tool/MCP/computer-use calls), and bind child gateway sessions plus approved plan revision and independent verification receipts. Do not construct or dispatch a pod merely because `validate_task_plan` or `eligible_nodes` passed. See LOCUS-432, LOCUS-415, LOCUS-412, LOCUS-413 and LOCUS-433.

## TDD / checks

```sh
python -m pytest -q tests/unit/test_task_graph_contract.py tests/unit/test_task_plan_compiler.py
ruff check locus_runtime/task_plan_compiler.py tests/unit/test_task_plan_compiler.py
```

The contract tests cover admission, budget, capability, graph, schema and context-binding failures. Integration with Deep Agents, real model providers, independent verifier, durable work queue, signed artifacts and restart recovery **is not shipped** by this increment.
