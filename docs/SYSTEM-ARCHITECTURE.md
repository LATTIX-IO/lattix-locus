# System Architecture

This document describes the implemented architecture of Lattix xFrontier as a working system rather than as a product pitch. It focuses on how the control plane, memory system, execution isolation, multi-agent runtime, and frontend interact in practice. Zero trust is an important cross-cutting property of the design, but it is not the center of the story. The center is a local-first orchestration platform that separates authoring, coordination, execution, memory, and review into explicit layers and surfaces.

## 1. System shape

At a high level, xFrontier is organized into five cooperating planes:

1. **User interface plane**
   The Next.js frontend in `apps/frontend/` provides the builder, run console, settings, collaboration, artifacts, and operator-facing screens.
2. **Control plane**
   The FastAPI backend in `apps/backend/app/main.py` is the canonical API surface. It owns route classification, auth enforcement, workflow and agent definitions, run management, memory APIs, collaboration state, observability summaries, and operator settings.
3. **Runtime and orchestration plane**
   Shared runtime primitives in `frontier_runtime/` and worker runtime code in `apps/workers/runtime/` manage staged execution, approvals, agent discovery, event envelopes, middleware, A2A dispatch, and sandbox planning.
4. **Execution plane**
   Agents and tools execute through bounded runtime contracts. Agent-to-agent work happens through envelopes and event topics, while tool execution is mediated by the sandbox subsystem instead of direct unconstrained host execution.
5. **State and memory plane**
   Short-term state, durable state, long-term memory, consolidation queues, and world-graph projection are split across Redis, PostgreSQL/pgvector, Neo4j, and local persisted state depending on the class of data.

The system is intentionally not a monolith with one undifferentiated memory or one undifferentiated agent runtime. It is a composed platform with explicit seams between authoring, orchestration, execution, and persistence.

## 1.1 Cognitive MVP layer

xFrontier now includes a shipped **cognitive MVP** layered on top of the existing graph execution model. The current implementation is intentionally additive and bounded: it introduces lightweight cognitive primitives without replacing the rest of the runtime.

The MVP adds four graph-native node types:

- `frontier/goal`
- `frontier/evidence`
- `frontier/assembly`
- `frontier/commitment`

Those nodes map to a lightweight runtime in `frontier_runtime/cognitive.py` with:

- column state for goal, evidence, and synthesis reasoning
- a bounded assembly runtime
- a weighted-support consensus path
- commitment output containing decision, confidence, blockers, dissent, and next actions

This is not yet the full target-state columnar system. Evaluation, uncertainty, prediction, decomposition, and adaptation loops remain future work.

## 2. Canonical control plane

The backend is the canonical control plane. That matters because it means the UI does not talk directly to workers, and workers do not become a second ad hoc API surface. The backend is where definitions are created, published, activated, archived, rolled back, and executed.

Core responsibilities of the backend include:

- workflow, agent, guardrail, integration, template, and playbook management
- graph validation and graph execution initiation
- request classification through explicit route access rules
- authentication and operator session handling
- memory reads, memory clearing, consolidation triggers, and world-graph projection triggers
- collaboration session join/read/sync/permission APIs
- observability and audit reporting

This control-plane bias is visible in `apps/backend/app/request_security.py`, where every route is assigned one of four categories:

- `public-minimal`
- `authenticated-read`
- `authenticated-mutate`
- `internal-only`

That route inventory is validated at startup so new endpoints cannot silently appear without an access classification. Architecturally, this makes the backend less of a generic web service and more of a policy-aware control boundary.

## 3. How memory works

Memory in xFrontier is tiered, scoped, and selectively promotable. It is not a single chat transcript bucket.

### 3.1 Short-term memory

Short-term memory is optimized for active execution and session continuity.

- The backend keeps an in-process view of session memory in `store.memory_by_session`.
- Redis is the primary external short-term store through `RedisMemoryStore`.
- Redis entries are keyed by session bucket and trimmed to a bounded working set.
- An optional write-ahead log on disk preserves durability if Redis is unavailable.

This tier is used for active context injection and recent execution continuity. It is the memory layer most closely associated with a live run or current collaboration session.

### 3.2 Long-term memory

Long-term memory is stored in PostgreSQL through `PostgresLongTermMemoryStore`.

Each entry carries at least:

- `bucket_id`
- `session_id`
- `memory_scope`
- `source`
- `task_id`
- `content`
- `metadata`

When available, pgvector embeddings are added so the platform can support semantic recall, similarity search, and ranking instead of relying only on exact filters. Embeddings are optional at runtime, but the store is designed to benefit from them when the OpenAI embedding client is configured.

### 3.3 Consolidation pipeline

xFrontier separates raw remembered events from promoted knowledge.

When entries are appended, the backend can enqueue them into `frontier_memory_consolidation_queue`. Consolidation is then responsible for:

- grouping candidate memories by bucket and scope
- summarizing repeated or important items into a smaller durable representation
- suppressing near-duplicate summaries
- optionally projecting the result into the world graph

This gives the system a distinction between:

- raw memory items
- consolidated memory summaries
- graph-level topic relationships

That is an architectural distinction between *what happened recently* and *what the platform should retain as durable knowledge*.

### 3.4 Hybrid retrieval

The retrieval path is intentionally hybrid. When enabled, a memory request can blend:

- short-term entries
- long-term entries
- world-graph context from Neo4j

The backend ranks the merged set using a bounded token budget, overlap scoring, runtime-role bonuses, and age decay. In practice, this means the system does not treat all stored context as equal. It builds a bounded execution context from multiple memory tiers and then injects only the most relevant subset into the active run.

### 3.5 World-graph projection

Neo4j is used as a knowledge projection layer, not as the primary transactional store.

Consolidated memory can be projected into a graph shaped around:

- knowledge owners
- knowledge memories
- topics
- evidence relationships

This gives the platform a structural memory model in addition to sequence-based memory. Session logs and semantic recall answer “what happened” and “what sounds related”; the graph adds “what entities and topics now relate to each other over time.”

### 3.6 Memory scopes and authorization

Memory is explicitly scoped. Supported scopes include:

- `run`
- `session`
- `user`
- `tenant`
- `agent`
- `workflow`
- `global`

The scope label is not trusted by itself. The backend normalizes the scope, validates that the bucket matches the scope prefix, and then authorizes access based on actor identity, tenant claim, collaboration membership, or internal-service identity as appropriate.

This is one of the most important architectural traits of the memory system: memory is not just persisted; it is partitioned and access-checked as first-class runtime behavior.

## 4. How isolation exists

Isolation in xFrontier is layered. The architecture assumes that orchestration, policy, transport validation, and the sandbox all contribute to containment. No single isolation mechanism is expected to carry the whole burden.

### 4.1 Execution isolation

Tool execution is mediated through `SandboxManager` in `frontier_runtime/sandbox.py`. It chooses the strongest available isolation strategy for the current environment:

1. **Kernel sandbox** on Linux or macOS
   Uses `bubblewrap` or `sandbox-exec` for direct host-level confinement.
2. **Hardened Docker**
   Uses a stripped-down container posture with read-only root filesystem, dropped capabilities, seccomp, resource caps, explicit mounts, and optional network disablement.
3. **Kubernetes runtime isolation**
   Emits pod metadata for gVisor or Kata-backed runtime classes in hosted deployments.

Across all modes, the same policy model describes:

- allowed executables
- allowed read paths
- allowed write paths
- whether network is allowed
- allowed destination hosts
- CPU, memory, PID, and timeout budgets

This is significant because the rest of the runtime does not need to know whether a tool is being isolated by host kernel namespaces, Docker, or a Kubernetes runtime class. It asks for an execution plan against a declarative policy, and the sandbox layer materializes the appropriate isolation backend.

### 4.2 Filesystem isolation

Filesystem isolation is deny-by-default and allowlist-based.

- root filesystems are read-only in hardened modes
- writable mounts are explicit
- sensitive subpaths such as `.git`, `.ssh`, `.gnupg`, `.aws`, `.azure`, and `.kube` are re-protected even when a parent path is writable

The design intent is that tool execution receives only the minimum working set required for the task rather than ambient host filesystem access.

### 4.3 Network isolation and mediated egress

The sandbox distinguishes between no-network and mediated-network execution.

- If network is disabled, the execution environment uses namespace-level isolation such as `--network=none` or `--unshare-net`.
- If network is enabled, access is still constrained by explicit host allowlists and is expected to flow through the sandbox egress gateway.

That means network access is a capability of the execution request, not a default property of being “inside the system.”

### 4.4 Runtime and service isolation

Worker runtime isolation is also logical, not just container-level.

Runtime envelopes pass through middleware that can:

- validate envelope shape
- resolve authenticated actor, tenant, subject, and session context
- enforce tenant consistency
- authorize memory access
- stop delivery when a security block is raised

The event bus only delivers envelopes that survive middleware checks. This means a message can be dropped before any agent handler sees it if the runtime context is inconsistent with policy.

## 5. How the multi-agent ecosystem exists

xFrontier’s multi-agent model is ecosystem-based rather than single-loop. Agents are not just prompts in a list; they participate in a controlled message fabric with explicit discovery, routing, and security semantics.

### 5.1 Agent registry and discovery

Agent discovery begins with the registry layer. `frontier_runtime/agents.py` builds a registry from discovered agent records and ensures a baseline catalog of roles such as:

- `research`
- `code`
- `review`
- `coordinator`

This provides a consistent role vocabulary even when local assets are incomplete.

### 5.2 Layered worker runtime

The worker runtime is separated into layers:

- **Layer 1**: workflow engine and orchestrator behavior
- **Layer 2**: contracts, event bus, middleware, policy, registry, security, and reporting
- **Layer 3**: dynamic agent loading and execution surfaces

That layered layout matters architecturally because it keeps orchestration concerns, message integrity concerns, and concrete agent loading concerns from collapsing into one implementation module.

### 5.3 Envelope-based communication

Inter-agent communication happens through typed envelopes rather than ad hoc direct calls. An envelope carries:

- identity and correlation metadata
- causality and timing information
- topic routing data
- budget constraints
- payload data
- error and security event traces

The event bus fan-outs envelopes by topic, runs middleware before delivery, and stops propagation when policy blocks or budget exhaustion occur.

This creates a message-driven ecosystem where agents are coordinated by envelope flow instead of unstructured side effects.

Alongside the existing envelope/event model, the cognitive MVP introduces a small internal message family for bounded reasoning artifacts:

- `belief_update`
- `evidence_claim`
- `synthesis_proposal`
- `dissent`
- `commitment`

In the current shipped slice these are internal cognitive artifacts and regression-tested runtime outputs, not yet a standalone external transport contract.

### 5.4 Budgeted cooperation

Agent cooperation is bounded by:

- time budgets
- token budgets
- max tool-call counts
- allowed tool sets
- allowed memory scopes
- allowed collaboration counts

The orchestration layer therefore has a practical definition of “collaboration.” It is not unlimited recursive delegation. It is a bounded execution graph with explicit controls over how many agents can participate and how much cost or time they can consume.

### 5.5 A2A transport

When execution crosses service boundaries, the runtime uses agent-to-agent HTTP transport with:

- bearer JWTs
- signed runtime headers
- subject identity
- nonce-based replay protection
- correlation propagation

The A2A helper in `apps/workers/runtime/network/a2a.py` enforces stricter transport rules in `local-secure` and `hosted` profiles and requires HTTPS for hosted mode. This keeps service-to-service traffic part of the architecture rather than an implementation afterthought.

### 5.6 Human-in-the-loop as a first-class participant

The orchestration model includes approvals and review checkpoints as normal workflow outcomes, not exceptions. Approval requests are stored durably, workflows can pause awaiting decision, and the run console surfaces approval state alongside traces, artifacts, and guardrail events.

This means the multi-agent ecosystem is not just “many machine agents.” It includes the operator as a controlled participant in the same execution story.

## 6. How the user interface connects and interacts with the system

The frontend is a structured operator and builder console, not a thin skin over a chatbot.

### 6.1 Application shell and session model

The Next.js app shell fetches the operator session and platform version state before rendering protected routes. It distinguishes between:

- public auth routes
- authenticated user routes
- builder routes
- admin-capable surfaces

The shell enforces a fail-closed UX pattern: if the operator session is not resolved or not authenticated, protected views do not render their data surfaces.

### 6.2 API integration model

The frontend talks to the backend through `apps/frontend/src/lib/api.ts`. That client:

- chooses `/api` or an internal base URL depending on environment
- attaches request identity headers where configured
- retries transient backend failures
- exposes typed methods for every backend surface the UI uses

This makes the UI a real client of the control plane, not a bundle of hard-coded fetches.

### 6.3 Builder interaction

The builder UI centers on graph-based composition.

The `StudioFullCanvas` component lets operators:

- assemble workflow graphs from typed nodes and links
- validate graph wiring before execution
- run the graph through the backend
- inspect runtime engine selection and hybrid routing
- inspect memory counts
- manage collaboration state
- view observability summaries

The builder is therefore both a design tool and a runtime workbench. It is tightly connected to backend graph validation, runtime policy, memory endpoints, and collaboration APIs.

### 6.4 Collaboration loop

The builder also participates in real-time-ish collaboration through session APIs:

- join a collaboration session
- poll session state and participants
- sync graph edits against a version number
- detect conflicts
- apply role-based editing posture such as owner, editor, or viewer

This makes the UI part of the control plane’s versioned state model rather than a purely local editing surface.

### 6.5 Run console

The run console is the operational view of execution. It combines:

- workflow event timelines
- graph snapshots
- agent reasoning traces
- artifact outputs
- approval actions
- guardrail findings
- cognitive artifacts such as commitment, blockers, dissent, and column-state confidence when present
- ATF and observability summaries

In architectural terms, this page is where backend state, runtime traces, agent outputs, governance signals, and human review converge into one operator workflow.

### 6.6 Frontend to runtime connection pattern

The browser never directly owns runtime execution. Instead, the interaction chain is:

1. the UI submits or reads state through the backend API
2. the backend validates, records, and orchestrates
3. the runtime and worker layers execute envelopes, tools, and agents
4. results are persisted back into control-plane state
5. the UI reads the updated run, event, artifact, memory, and observability surfaces

That separation is what keeps the UI cleanly connected to the system without becoming part of the trusted execution substrate itself.

## 7. Cross-cutting trust model

Zero trust is best understood here as a design style that shows up repeatedly rather than as a slogan.

Examples of that style include:

- route-level access classification instead of implied trust
- authenticated operator sessions before rendering protected UI
- internal-only APIs for memory maintenance actions
- signed A2A transport instead of local-network trust
- memory-scope authorization instead of bucket-name trust
- explicit sandbox capabilities instead of default host access
- middleware-enforced tenant consistency before message delivery

The important point is not that the system says “zero trust.” The important point is that most major boundaries require explicit identity, scope, or capability checks before work proceeds.

## 8. End-to-end execution narrative

An end-to-end run typically looks like this:

1. An operator uses the frontend to create or open a workflow in the builder.
2. The builder saves definitions through the backend control plane.
3. The operator validates or runs the graph.
4. The backend resolves runtime policy, validates graph structure, selects routing strategy, and initializes execution state.
5. Agent and tool steps execute through the runtime layers, using envelopes, middleware, budgets, and sandbox planning.
6. Memory is read from the appropriate scope and tier, then bounded hybrid context is injected where needed.
7. New outputs and important events are appended to short-term memory and may be promoted into long-term memory or consolidation queues.
8. If configured, consolidated memory is projected into the world graph.
9. Artifacts, traces, approvals, and observability summaries are persisted by the control plane.
10. The run console reads those surfaces back and presents them to the operator for review, follow-up, or approval.

This is the real shape of the platform: a local-first, layered orchestration system where UI, control plane, runtime, memory, and isolation all remain distinct enough to reason about independently, but connected enough to behave like one coherent product.
