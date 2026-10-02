# Lattix xFrontier Design

xFrontier separates a control plane that owns definitions, versioning, and policy from a runtime that executes graphs under isolation. Keep domain logic pure and push IO, vendor SDKs, and model providers behind adapters, so the orchestration semantics stay testable without a live stack.

## Design principles

- **Control plane owns authority.** Definitions, versions, publication, rollback, activation, and security policy resolve in `apps/backend/`. The runtime executes; it does not decide what is allowed.
- **Fail closed at every boundary.** Auth, policy, capability, replay, parsing, and isolation decisions deny on uncertainty or dependency loss.
- **Isolation is explicit, never inferred.** Strategy selection is a declared policy per host platform. A weaker tier requires an opt-in flag, not a silent fallback.
- **Envelopes carry context.** Tenant, actor, budget, classification, and security events travel with the work, not in ambient state.
- **The event log is the audit spine.** Hash-chained, signed events are the source of truth for what happened. Do not add execution paths that bypass it.
- **Additive evolution.** New node types and cognitive columns extend the graph without changing existing semantics. Legacy graphs must keep validating and running.
- **Feature flags default safe.** Memory consolidation, hybrid retrieval, decay, dedup, graph projection, and non-native engines are all off until an operator turns them on.
- **Local-first is a real target, not a demo mode.** The lightweight stack must stay functional without the full security infrastructure — but must never be the default posture for anything non-local.

## Layer model

1. **Orchestration** — graph validation and execution, run lifecycle, checkpointing
2. **Guardrails** — filter chain (prompt render, DLP/redaction, capability enforcement) in `frontier_runtime/guardrails.py`
3. **Agent execution** — node executors and pluggable engines; A2A transport between workers
4. **Infrastructure** — Vault, OPA, Envoy, NATS, Postgres/pgvector, Redis, Neo4j, sandbox egress

## Extension points

| Extension point | Where | Contract |
| --- | --- | --- |
| Node type | `_execute_node` in `apps/backend/app/main.py` + `apps/frontend/src/lib/frontier-node-catalog.ts` + `frontier-node-schema.ts` | Backend executor, frontend catalog entry, and config schema must land together |
| Cognitive column | `frontier_runtime/cognitive.py` | Implement `observe` → `ColumnState` → `emit_message`; fuse through `ConsensusEngine` |
| Execution engine | `_run_framework_*` helpers | Optional import, reported through `/runtime/providers`; never a hard dependency |
| Guardrail filter | `frontier_runtime/guardrails.py` filter chain | `FilterContext` in, `FilterResult` out; no IO in the filter itself |
| Isolation strategy | `frontier_runtime/sandbox.py` | Implement a strategy class and register it against a `HostPlatform`; declare real `SandboxCapabilities` |
| Policy | `policies/*.rego` | Ships with a matching test in `policies/tests/` |
| Integration | `IntegrationDefinition` (`http`/`database`/`queue`/`vector`/`custom`) | Declare `permission_scopes`, `data_access`, `egress_allowlist`, and `execution_mode` |
| Memory tier | `apps/backend/app/platform_services.py` | Redis (short-term), Postgres+pgvector (long-term), Neo4j (world graph) |
| Agent asset | `examples/agents/` or `FRONTIER_AGENT_ASSETS_ROOT` | `agent.config.json` validated against `packages/contracts/templates/agent.config.schema.json` |

## Known design tensions

- **`apps/backend/app/main.py` is a monolith** (21,228 LOC, 139 routes). New work should extract cohesive routers and services rather than append. Extraction is the preferred refactor when touching a domain area substantially.
- **The control-plane store is in-memory with a Postgres snapshot**, not a database-backed repository. Definitions live in `InMemoryStore` and are serialized on mutation. Moving to a real repository boundary is the durable fix for the silent-persistence failure mode.
- **Microsoft Agent Framework is stated as a layer but is only a code emitter.** `generated_artifacts.py` writes `agent_framework` source as a downloadable artifact; nothing executes it. Either wire it as a real engine or describe it accurately as codegen.
- **The cognitive slice is 4 of 10+ planned columns.** Evaluation, Uncertainty, State, Decomposition, Prediction, and Adaptation are unimplemented; see `PLANS.md` and `docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md`.

## Design docs

- `docs/ARCHITECTURE.md`
- `docs/SYSTEM-ARCHITECTURE.md`
- `docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md`
- `docs/reactflow-node-specs-and-execution-design.md`
- `docs/isolation-layers.md`
- `docs/SANDBOXING.md`
- `docs/frontier-agent-schema.md`
- `THREAT-MODEL.md`
