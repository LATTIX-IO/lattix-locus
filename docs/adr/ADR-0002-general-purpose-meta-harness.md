# ADR-0002: General-purpose Locus meta-harness and ephemeral expert pods

Status: **Accepted product direction** (principal, 2026-10-10); **implementation pending**.
Owner: Locus product/runtime. First delivery: LOCUS-435, then LOCUS-411–414 and LOCUS-437.
Supersedes the restrictive reading of D-01; **retains** D-03, D-05, D-09, D-22, D-28, D-30 and the security invariants P6–P12. References: [product roadmap](../product/20-roadmap-and-decisions.md), [architecture modules](../ARCHITECTURE-MODULES.md), [LOCUS-435](https://linear.app/lattix/issue/LOCUS-435).

## Context

Locus is currently described as a single-principal local-first personal operator, with an effective coding-first execution path. The intended product is **one consistent Zero Trust execution system for arbitrary authorized knowledge-work tasks**, including software, research, documents, data, communication, operations and computer use. It must orchestrate existing model providers and **agent engines**, not replace them.

A persona that is conversationally persistent is not necessarily a continuously executing agent. We will compare ephemeral specialists against persistent persona workers under matched budgets on held-out cross-domain tasks; effectiveness is a hypothesis to test, not an assumed scientific guarantee.

## Decision

### 1. Task Orchestrator is a supervisor, not an unrestricted router agent

1. Admit a **TaskIntent**: source identity, goal, area, risk/data limits, budgets, deliverables and acceptance criteria.
2. Model-assisted ideation proposes a **versioned directed acyclic task graph** and a per-node quality rubric. A deterministic validator rejects cycles, missing dependencies, over-allocated budgets and widened capabilities.
3. The **Task Orchestrator** admits nodes and composes **ephemeral expert pods** from task capability requirements, available engines, scoped skills, and memory evidence; independent verifiers are separate roles.
4. Run independent, policy-admitted nodes in parallel, subject to globally enforced resource reservations, leases, capacity and per-device computer-use exclusivity.
5. A dependent node starts only after its required predecessor artifact is **independently verified** and versioned. An invalidated upstream artifact invalidates affected downstream work. Opt-in speculative work is separately accounted and never mistaken for verified execution.
6. A downstream pod may repair a dependency only inside its delegated scope and budget. Otherwise it emits a structured blocker and the supervisor re-plans or requests approval. Do not silently retry irreversible side effects.
7. After verification and quality escalation, the orchestrator synthesizes a result with artifacts, citations, partial outcomes, costs and audit evidence. A durable user-facing assistant identity can survive across runs, while expert execution actors expire.

A language model can **propose** plans, experts and repairs; deterministic scheduling, action authorization, dependency readiness and release promotion are enforced by code.

### 2. Authority and memory boundaries

Each pod has a fresh execution identity scoped to one run, a parent-attenuated envelope, capability ceiling, explicit memory projection, deadline and resource reservation. It cannot widen risk, access another area/tenant, promote its own durable memory, or inherit ambient tool/host/browser authority. Memory and task state persist independently of pod/process lifetime; they require provenance, expiry, contradiction/supersession and deletion semantics. Untrusted retrieved artifacts and tool outputs remain tainted.

The action gateway/PEP, policy engine and grant checks remain authoritative; the Task Orchestrator is **not** a second PEP. R4 actions are never delegable. This ADR does not grant permission to change the trust kernel or to route around model inference controls.

### 3. Provider and engine distinction

**Inference providers** serve model calls (Ollama, OpenAI, Anthropic and compatible APIs), while **agent engines** own an execution loop (Deep Agents runtime, Codex CLI, Claude Code, OpenCode). Both are selected with explicit capabilities and data policy; all external engine tools, egress and approvals remain mediated through the Locus gateway and sandbox. A provider's API compatibility does not establish native feature parity; a provider being supported does not mean its vendor agent engine is implemented.

### 4. Two noninterchangeable learning systems

**Personal/local alignment:** explicit reviewable, reversible per-principal settings and profile hypotheses: tone, workflow, quality criteria, interaction preferences. Data remains local unless the principal separately approves a sanitized export. Preferences cannot alter authorization, security policy, core trust state or capability grants.

**Shared product RSI:** opt-in, minimized deployment-wide failure/quality/usage telemetry; aggregate by OS, provider, Locus version, domain and relevant consent-safe cohorts. Validate prevalence, statistical uncertainty, representativeness and minimum sample counts. A single deployment's preference must not drive shared changes. Harness improvements use RRSI-inspired component/hypothesis edit history, bounded candidate edits, anti-leakage critic, variance/noise floors, domain nonregression guards, cost-adjusted selection, held-out/OOD suites and pruning. Candidates execute in isolated worktrees, propose PRs, and require explicit protected-path review. Signed releases use staged rollout, stop switches and rollback.

Raw prompts, local memories, paths, documents, tool output, secrets, screen content and identity do **not** leave by default. Fleet ingestion is never a prerequisite for offline desktop work.

### 5. Optional Savant integration

Native Locus skills, SKILL.md lifecycle, evals, and trust gating stand on their own. An organization may opt into Savant as one connector for governed skill versions and optionally sanitized skill intelligence. Savant cannot replace or bypass Locus admission, local revocation or authorization. Offline/missing/revoked Savant does not disable native skills. Tracks: LOCUS-388 / LOCUS-418.

### 6. Deployment boundary remains explicit

D-03 **single principal per local instance** and D-09 **P2P peer collaboration** remain in force. An **optional fleet feedback and update control service**, with its own threat model, authorization and organization consent, is a distinct future component. It is not a centrally hosted multi-user Locus runtime, cloud inference requirement, or default organization admin over personal data. Managed multi-tenant workspaces and hosted agent execution require separate ADRs and user-visible authorization decisions. No change to Helm/hosted support is implied.

## Executable contract (first increment)

`locus_runtime/task_graph_contract.py` v1.0 defines pure task intent, envelope, node and DAG admission types. It does **not** launch pods, verify evidence, accept grants, commit state or broker tools. The exact boundary and its negative tests live in `tests/unit/test_task_graph_contract.py`. Runtime admission, leases, evaluator, capability selection and persistence remain tracked by LOCUS-411–415 and LOCUS-433. Do not call the contract a deployed multi-agent runtime.

## Acceptance and release gates

| Gate | Required evidence | Issue |
|---|---|---|
| Structural DAG/authority contract | Invalid/cyclic/missing reference, R4 and capability/budget escalation denied; deterministically ordered parallel waves | LOCUS-411 |
| Task-specific ephemeral pods | Three distinct domains, cross-area denial and worker cancellation | LOCUS-412, LOCUS-437 |
| Dependency execution and recovery | Diamond graph, verified artifact gate, corrupted input repair, process restart, no duplicated side effect | LOCUS-413, LOCUS-433 |
| Independent domain quality | Code, research, documents, data, communication, business process and computer use with sealed holdouts | LOCUS-414, LOCUS-431 |
| Provider/engine parity | Capability/approval/usage matrix, real mediated tool calls, documented exceptions | LOCUS-415, LOCUS-416 |
| Memory/governance | Scoped provenance, deletion, contradiction and reversible principal-specific alignment | LOCUS-424, LOCUS-422 |
| Fleet RSI | Consent OFF, cohort bias and anti-overfit tests, adversarial telemetry, signed canary and rollback | LOCUS-421, LOCUS-436, LOCUS-419–420, LOCUS-423 |
| Skills connector | All native skill workflows succeed without Savant | LOCUS-418 |

This decision changes product **intent**; it does not claim an existing runtime capability, production security posture or test coverage not yet demonstrated.

## Consequences / alternatives

- Extends existing AgentRuntime and versioned ports (D-28); does not introduce a parallel agent framework.
- Keeps a single user-facing assistant but uses transient specialist workers and a durable supervisor.
- Preserves the existing coding RSI and personal assistant flows as task-domain specializations.
- No separate immortal persona agents by default. Use measured experiments to decide whether any persistent worker is warranted.
- No mandatory Savant or cloud control-plane coupling.
- Rollback: remove the new planner path behind a feature gate while retaining original verified execution; do not silently bypass the gateway or alter stored permissions.
