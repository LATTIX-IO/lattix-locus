# Architecture

Lattix Locus now uses the canonical repository shape described in `THREAT-MODEL.md`: `apps/backend/` is the control-plane surface, `apps/workers/` is the worker/runtime surface, and the old `lattix_locus/` package has already been removed from the working tree.

## Current state

The repository is organized around four layers: orchestration, guardrails, agent execution, and infrastructure.

- `apps/backend/` manages workflow state, execution control, publication, rollback, and authenticated operator-facing APIs.
- `apps/workers/` provides worker runtime helpers, A2A transport, service templates, and sandbox-facing execution boundaries.
- `locus_runtime/` and `locus_tooling/` hold shared runtime and CLI/installer primitives.
- `docker-compose.yml`, `docker-compose.local.yml`, `helm/`, `envoy/`, and `policies/` define runtime infrastructure and controls.

## Historical migration note

Some docs still refer to migration history from the removed `lattix_locus/` package. Treat that material as historical context, not as evidence of a second live backend.

## Target state

The target architecture is:

- `apps/backend/` as the only canonical backend/control-plane surface
- `apps/workers/` as the worker/runtime surface
- shared primitives extracted into `locus_runtime/` / `locus_tooling/` or deleted if no longer needed

That means new backend/control-plane features should land in `apps/backend/` or `apps/workers/`, and future cleanup should remove stale legacy assumptions from docs, tooling, and release guidance rather than reviving a deleted package.

The distribution path now includes:

- a public bootstrap installer flow
- a local `*.localhost` gateway for developer-friendly access
- Helm values for enterprise ingress and federation metadata

## Cortical column zero-trust runtime

The cortical assembly runtime is part of the canonical backend/runtime architecture, not a separate trusted control plane. It treats every column, message, runtime step, and graph projection as untrusted until the backend admits it through identity, tenant, capability, policy, replay, and persistence controls.

### Zero-trust column assumptions

- A column is not trusted because it is in the same process, container, Docker network, or cluster namespace.
- Column messages are data-bearing security events. They must carry tenant, assembly, source column, target column, nonce, timestamp, and trusted subject metadata before they can mutate causal state.
- Column kinds are least-privilege roles. Goal columns read input and emit goal beliefs, evidence columns retrieve, evaluation columns score or veto, synthesis columns propose commitments, and uncertainty columns emit blockers. Unknown kinds deny by default.
- Assembly definitions are admitted before execution. Admission bounds columns, iterations, messages, allowed column kinds, required goal/evidence/evaluation/synthesis participation, tenant, provider, model, and tool policy.
- Commitment is a gated outcome, not a direct runtime shortcut. Evidence and evaluation participation, unresolved blockers, confidence thresholds, high-risk human approval, and the audited decision trail are checked before a ready commitment is accepted.
- Causal graph projection is a derived view. Persisted causal state remains the source of truth, projection is tenant-checked and size-bounded, and projection failure must not corrupt persisted state.

### Tenant isolation model

Tenant identity flows through signed runtime bearer claims, authenticated backend request context, and admitted cognitive message metadata. The runtime rejects cross-tenant assembly replay, tenant-mismatched cognitive messages, and causal graph projection requests made with the wrong tenant context. Memory, causal state, and projection helpers must not rely on caller-supplied scope names alone; they use the authenticated actor, tenant claim, collaboration membership, or internal-service identity appropriate to the operation.

### Signed message flow

1. A runtime sender builds a cognitive `AgentEvent` or `Envelope` from a `ColumnMessage`.
2. The sender signs the request with the shared A2A runtime signature headers: `X-Locus-Subject`, `X-Locus-Nonce`, `X-Locus-Timestamp`, `X-Correlation-ID`, and `X-Locus-Signature`.
3. Backend middleware verifies the profile-specific auth requirements, signed runtime headers, timestamp freshness, and nonce replay status.
4. The cognitive admission handler validates the message shape, tenant/assembly ownership, known source and target columns, and semantic replay marker.
5. Accepted messages can be recorded as replay markers and emitted into audit/observability paths. Replayed or conflicting messages fail closed before causal state mutation.

### Policy gate flow

Every cortical execution step runs through the shared column runtime gate before model calls, retrieval, tool calls, memory writes, or commitment publication. The gate evaluates authenticated context, tenant ownership, column capability, assembly admission policy, runtime provider/model allowlists, tool/retrieval/network allowlists, budget counters, and audit emission. Denials use stable reason codes where exposed through backend audit events.

### Deployment profile contract

Runtime profile behavior is part of the architecture contract:

- `local-lightweight` keeps quick local development usable and allows public minimal health plus unauthenticated local workflows where explicitly supported.
- `local-secure` requires authenticated operator access and signed/internal service identity for protected surfaces, while reporting unsafe secure-profile settings as degraded.
- `hosted` requires authenticated operator access, A2A runtime headers, signed messages, replay protection, egress allowlists, and MCP local-server policy unless remote MCP usage is explicitly confirmed. Unsafe hosted settings are blocked or reported unhealthy.

### Rollback and monitoring

For behavior-changing cortical runtime releases, rollback should restore the previous application image/configuration, keep persisted causal state intact, and verify replay markers still prevent duplicate message and commitment mutation. Operators should monitor authenticated health details, `secure_profile` status, structured audit events for cognition admission/policy/commitment/projection decisions, runtime security counters, projection failure status, and redaction markers on persisted/audited payloads.

---

## Architecture standard

This section is the Lattix-standard architecture record for this repo. `DESIGN.md` holds design principles and extension points; `RELIABILITY.md` holds dependencies and failure modes; `THREAT-MODEL.md` and `SECURITY.md` hold the security view.

### Layers

| Layer | Responsibility | Where |
| --- | --- | --- |
| 1 — Orchestration | Graph validation and execution, run lifecycle, checkpointing | `apps/backend/app/main.py`, `apps/backend/app/graph_compiler.py` (LangGraph) |
| 2 — Guardrails | Prompt render, DLP/redaction, capability enforcement, policy gates | `locus_runtime/guardrails.py`, `policies/` via OPA |
| 3 — Agent execution | Node executors, A2A transport, tool invocation; every model call through the gated model client | `_execute_node`, `locus_runtime/model_client.py`, `apps/workers/runtime/` |
| 4 — Infrastructure | Isolation, secrets, policy, transport, storage, tracing | `locus_runtime/sandbox.py`, Vault, OPA, Envoy, NATS, Postgres/pgvector, Redis, Neo4j, Jaeger |

### Canonical surfaces

| Surface | Owns |
| --- | --- |
| `apps/backend/` | Control plane: 139 REST routes, definitions, versioning, publish/activate/rollback, security policy resolution, run lifecycle, SSE run streaming, audit |
| `apps/workers/` | Worker runtime in four layers — L1 orchestrator and workflow specs, L2 event bus/envelopes/registry/policy/security, L3 agent loader, plus A2A network and JWT |
| `locus_runtime/` | Shared primitives: cognitive columns, guardrails, events and hash chain, security (OPA, Vault, capability, replay), sandbox, conversation, memory helpers, install |
| `locus_tooling/` | `lattix` CLI and the installer |
| `apps/frontend/` | Next.js operator and builder UI |
| `packages/contracts/` | Public schemas: agent config, envelope, workflow, URL manifest |
| `policies/` | OPA policy source and tests |
| `helm/`, `deploy/`, `docker/`, `envoy/` | Deployment surfaces |

### Runtime profiles

`local-lightweight` (unauthenticated, laptop only — the unset default), `local-secure` (pinned by `docker-compose.yml`), `hosted` (pinned by the Helm chart, requires signed A2A headers). Pin the profile explicitly for anything that is not a developer machine.

### Data and state

- **Control-plane definitions** — in-memory store snapshotted to Postgres. See `RELIABILITY.md` for the silent-failure caveat.
- **Runtime state** — `.locus/runtime-state.json` (approvals, events, replay tokens) via `locus_runtime/persistence.py`
- **Short-term memory** — Redis
- **Long-term memory** — Postgres + pgvector
- **World graph** — Neo4j, feature-flagged
- **Checkpoints** — LangGraph Postgres checkpointer

### Node type taxonomy

Fourteen executable node types: `trigger`, `agent`, `prompt`, `tool-call`, `retrieval`, `memory`, `guardrail`, `human-review`, `output`, `manifold`, plus the cognitive slice `goal`, `evidence`, `assembly`, `commitment`. Each requires a backend executor, a frontend catalog entry, and a config schema — added together, never separately.

### Architectural constraints

- New control-plane features land in `apps/backend/` or `apps/workers/`; the removed `lattix_locus/` package is not revived.
- `apps/backend/app/main.py` is a 21,228-LOC monolith. Prefer extracting a cohesive router or service over appending to it.
- Cognitive columns are additive. Existing `locus/agent` semantics and legacy graphs must keep validating and running.
- One execution engine: native. The LangChain, LangGraph, Semantic Kernel and AutoGen chat adapters and the LangGraph/MAF code scaffolds were removed (LOCUS-352); stored engine settings resolve to native.
- Model providers are reached only through `locus_runtime/model_client.py` (and the Deep Agents runtime behind the same gate). `tests/unit/test_model_client_bypass.py` fails on a provider client built anywhere else (LOCUS-376).
