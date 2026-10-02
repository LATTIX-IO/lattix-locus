# Frontend and Backend Audit Plan

Date: 2026-04-04

## Purpose

This document inventories what is wired correctly across the frontend and backend today, what is only partially implemented or inconsistent, what remains stubbed or mock-dependent, and which performance optimizations should be prioritized to improve user experience.

The emphasis is on the main operator paths:

- authentication and session gating
- inbox and workflow run lifecycle
- chat and follow-up workflow interactions
- graph execution and collaboration
- frontend/backend transport, state, and performance

## Audit Method

The audit was based on repository inspection of the active frontend and backend code paths, not on a full runtime validation pass. Command-line frontend validation is currently constrained in this shell because `npm` is unavailable.

Primary sources reviewed:

- `apps/frontend/src/lib/api.ts`
- `apps/frontend/src/components/app-shell.tsx`
- `apps/frontend/src/components/user-chat-workspace.tsx`
- `apps/frontend/src/components/navigation/user-console-sidebar.tsx`
- `apps/frontend/src/components/run-followup-composer.tsx`
- `apps/frontend/src/components/studio-full-canvas.tsx`
- `apps/backend/app/main.py`
- `docker-compose.local.yml`
- `docker/local/Caddyfile`
- `packages/contracts/README.md`

## What Is Wired Correctly

### 1. Frontend to backend API surface is centralized and broad

The frontend calls into a single API client layer in `apps/frontend/src/lib/api.ts`, which covers operator auth, runs, inbox, artifacts, builder flows, graph execution, collaboration, integrations, and platform metadata.

Why this matters:

- the wiring is understandable and discoverable in one place
- request headers and API base resolution are consistently applied
- the project already has a workable seam for future caching, retries, and telemetry

### 2. Auth and operator session gating are implemented end-to-end

Backend runtime profiles and auth policy are defined in `apps/backend/app/main.py`, including explicit `local-lightweight`, `local-secure`, and `hosted` modes.

Frontend route gating and operator-session redirects are implemented in `apps/frontend/src/components/app-shell.tsx`.

What is working:

- cookie-backed operator session model
- route protection for authenticated surfaces
- mode-based redirect logic for builder versus user experiences
- platform version retrieval and display

### 3. Workflow run and inbox flows are wired

The current user flows are connected end-to-end:

- run creation from frontend to backend
- inbox retrieval
- workflow run list retrieval
- workflow run detail retrieval
- workflow run event retrieval
- follow-up run creation using prior run context

Relevant files:

- `apps/frontend/src/lib/api.ts`
- `apps/frontend/src/app/inbox/page.tsx`
- `apps/frontend/src/components/user-chat-workspace.tsx`
- `apps/frontend/src/components/run-followup-composer.tsx`
- `apps/backend/app/main.py`

### 4. User chat workspace is now structurally aligned with the run model

The frontend now has a session-centered user workspace:

- left-side searchable session navigation
- center chat timeline and composer
- right-side details flyout
- separate execution graph tab

That creates a coherent frontend shell for the backend run/event model instead of scattering details across multiple unrelated pages.

### 5. Graph validation and execution are wired

Builder-mode graph validation and run flows exist on both sides, and collaboration state can sync graph data between participants.

What is working:

- graph validate endpoint
- graph run endpoint
- collaboration session join/read/sync model
- graph rendering in React Flow

Relevant files:

- `apps/frontend/src/lib/api.ts`
- `apps/frontend/src/components/studio-full-canvas.tsx`
- `apps/backend/app/main.py`

### 6. Local runtime deployment modes are explicit

The runtime profile distinction between lightweight local iteration and secure local deployment is clear in the backend and documented by the local compose/gateway setup.

What is working:

- direct lightweight mode for fast local development
- secure local mode with authenticated surfaces and gateway-based `/api` routing
- install/runtime assumptions documented and aligned with current bootstrap work

Relevant files:

- `apps/backend/app/main.py`
- `docker-compose.local.yml`
- `docker/local/Caddyfile`

## What Is Partially Wired or Inconsistent

### 1. Read and write API behavior is inconsistent about failure handling

The frontend mixes `safeFetch` and `strictFetch` in ways that blur whether a backend failure should break the UI or silently fall back.

Observed behavior:

- `safeFetch` always uses `cache: "no-store"` and returns fallback payloads even on backend errors or disconnects
- several write-like operations still use `safeFetch`
- other write operations correctly use `strictFetch`

Impact:

- users can receive optimistic or synthetic success when the backend is unavailable
- the UI can appear operational while masking actual data or persistence failures
- debugging production issues becomes harder because read and write semantics are not uniform

Relevant files:

- `apps/frontend/src/lib/api.ts`

### 2. Runtime behavior differs materially by environment profile

The platform behaves differently depending on whether it is running in `local-lightweight`, `local-secure`, or `hosted` mode.

Impact:

- developers may validate a workflow in a permissive local mode that behaves differently in secure local or hosted runtime
- auth, request header enforcement, and public-health behavior can diverge across environments

Relevant files:

- `apps/backend/app/main.py`

### 3. Contracts are conceptually shared but not actually shared in code

The frontend maintains independent TypeScript shapes in `apps/frontend/src/types/frontier.ts`, while the backend defines parallel Pydantic models in `apps/backend/app/main.py`. The contracts package is effectively a placeholder right now.

Impact:

- drift risk between API producer and consumer
- duplicated change work
- contract regressions discovered late instead of at build time

Relevant files:

- `apps/frontend/src/types/frontier.ts`
- `apps/backend/app/main.py`
- `packages/contracts/README.md`

### 4. Session and inbox data are fetched more than once in the current shell

The new inbox page fetches runs and inbox items server-side, while the user sidebar fetches them again client-side on pathname changes.

Impact:

- unnecessary duplicate requests
- slower perceived page transitions
- more backend churn for the most common user workflow

Relevant files:

- `apps/frontend/src/app/inbox/page.tsx`
- `apps/frontend/src/components/navigation/user-console-sidebar.tsx`

### 5. Operator session lookups are more frequent than necessary

`AppShell` refreshes operator session state on every pathname change.

Impact:

- repeated session fetches during normal navigation
- extra UI churn for a mostly stable piece of state
- increased backend load for low-value repeated reads

Relevant files:

- `apps/frontend/src/components/app-shell.tsx`

## What Is Not Fully Implemented or Still Needs Work

### 1. The system still depends heavily on fallback and mock behavior

The frontend intentionally returns fallback payloads for many reads and some writes, even when the backend is unavailable.

This is useful for demo continuity, but it weakens trust in the operator experience when the user expects live state.

Relevant files:

- `apps/frontend/src/lib/api.ts`
- `apps/frontend/src/lib/mock-data.ts`
- `apps/frontend/src/lib/api.spec.ts`

### 2. Several backend behaviors are stubbed or only simulate full functionality

Examples called out in the current codebase include:

- artifact version creation with minimal lifecycle depth
- graph/node operations that report success without full domain behavior
- integration test paths that simulate success messaging
- model/runtime execution that can degrade into simulated mode when providers are unavailable

Impact:

- the UI and API may look feature-complete while real operational behavior is still partial
- non-happy-path behavior is likely under-tested

Relevant files:

- `apps/backend/app/main.py`

### 3. Chat is still request/response, not a true streaming transport

The run/chat experience is event-based but not transport-stream driven. The UX suggests conversational interactivity, but the underlying transport is still dominated by request/response fetches.

Impact:

- slower perceived responsiveness during long model or orchestration runs
- weaker observability into partial progress
- less natural chat experience for operators

Relevant files:

- `apps/frontend/src/components/run-conversation-console.tsx`
- `apps/frontend/src/components/user-chat-workspace.tsx`
- `apps/backend/app/main.py`

### 4. Seeded demo state is still present in backend memory store defaults

The backend initializes seeded runs, run events, and run details in memory.

Impact:

- useful for demo and development bootstrap
- risky if demo-first defaults bleed into expectations for production-grade persistence and operational semantics

Relevant files:

- `apps/backend/app/main.py`

## Performance Optimization Opportunities

### Highest priority

#### 1. Eliminate duplicate fetches in the inbox/session shell

Current issue:

- `apps/frontend/src/app/inbox/page.tsx` fetches runs and inbox data server-side
- `apps/frontend/src/components/navigation/user-console-sidebar.tsx` fetches the same data again client-side

Optimization:

- lift the fetched data into shared page state or a query cache
- pass sidebar data from the page payload instead of refetching on mount/path change

Expected gain:

- fewer backend reads for the hottest user path
- faster navigation and better perceived responsiveness

#### 2. Stop using `no-store` universally

Current issue:

- `safeFetch` and `strictFetch` set `cache: "no-store"` on all requests

Optimization:

- classify endpoints into live, semi-static, and static
- keep `no-store` only for genuinely volatile data such as live run state or approvals
- use revalidation or client cache for published workflows, node definitions, settings, version metadata, and other stable reads

Expected gain:

- reduced request volume
- lower latency for repeat navigations
- better resilience when the backend is under load

#### 3. Move operator session and platform status to a shared cache/store

Current issue:

- operator session is reloaded on route change
- platform version is fetched separately in the shell

Optimization:

- create a single session store with TTL-based refresh
- hydrate it once at app-shell level and refresh in background

Expected gain:

- less shell churn
- lower auth/session traffic
- simpler route transition behavior

#### 4. Make run creation asynchronous from the user’s perspective

Current issue:

- workflow run creation can sit on a long synchronous request path

Optimization:

- return accepted/run-id quickly
- move execution to queued async orchestration
- expose status updates through polling or streaming

Expected gain:

- faster first response to the operator
- fewer long-held frontend requests
- better capacity under concurrent usage

### Medium priority

#### 5. Replace full-state collaboration polling with push or patch-based sync

Current issue:

- collaboration reads the full session graph on a 2.5 second interval
- collaboration writes push full graph snapshots after local edits

Optimization:

- move to websocket or SSE transport where practical
- sync graph deltas or patch sets instead of whole snapshots

Expected gain:

- lower bandwidth and CPU usage
- fewer merge/conflict edge cases
- better real-time collaboration feel

Relevant files:

- `apps/frontend/src/components/studio-full-canvas.tsx`
- `apps/backend/app/main.py`

#### 6. Parallelize graph execution where the DAG allows it

Current issue:

- graph execution iterates nodes in topological order sequentially

Optimization:

- detect independent branches in the DAG
- execute ready nodes concurrently with a bounded worker pool

Expected gain:

- lower end-to-end graph runtime for multi-branch workflows
- better utilization of worker/runtime capacity

Relevant files:

- `apps/backend/app/main.py`

#### 7. Reduce full-store serialization and persistence churn

Current issue:

- the backend performs broad serialization and persistence patterns across many mutations

Optimization:

- move toward append-only event logging or domain-level persistence boundaries
- persist only the entity or event stream that changed

Expected gain:

- lower write amplification
- better performance under high mutation rates
- cleaner auditability

Relevant files:

- `apps/backend/app/main.py`

### Structural priority

#### 8. Promote `packages/contracts` into a real shared contract source

Current issue:

- API contract shapes are duplicated between backend and frontend

Optimization:

- define shared schemas in `packages/contracts`
- generate or publish TypeScript and Python artifacts from the same source
- add contract tests on both sides

Expected gain:

- reduced drift
- safer refactors
- clearer platform boundary ownership

#### 9. Separate demo/fallback mode from production-grade live mode

Current issue:

- mock fallback and simulated runtime behaviors are intertwined with normal request flows

Optimization:

- make demo-mode an explicit runtime flag and UI state
- fail closed on operator-critical writes
- visually distinguish fallback data from live data

Expected gain:

- improved user trust
- easier debugging
- fewer silent data-integrity surprises

## Recommended Execution Plan

### Phase 1: Correctness and trust

1. Convert operator-critical writes from `safeFetch` to `strictFetch`.
2. Audit which reads are allowed to degrade to fallback and which must fail visibly.
3. Mark demo/fallback responses explicitly in the UI when they are used.
4. Add a simple frontend/backend contract checklist for run, inbox, approval, artifact, and auth payloads.

### Phase 2: Frontend request efficiency

1. Remove duplicate run/inbox fetches in the user shell.
2. Introduce a shared operator session cache.
3. Add cache policy tiers to the API client instead of unconditional `no-store`.
4. Reuse fetched stable metadata across routes instead of refetching on each mount.

### Phase 3: Backend execution efficiency

1. Split run creation into accept-now, execute-async behavior.
2. Add progress updates through a streaming or polling-friendly status model.
3. Reduce full-state persistence frequency.
4. Parallelize eligible graph branches.

### Phase 4: Contract and architecture hardening

1. Make `packages/contracts` the canonical schema source.
2. Generate or validate frontend and backend types from the same definitions.
3. Add contract tests that exercise the top user workflows.
4. Reduce demo-first seeded behavior in default runtime paths.

## Suggested Validation Work

Once Node tooling is available in the environment, validate this plan with:

- frontend unit tests and build
- backend unit/integration tests for run, inbox, and auth flows
- request tracing across `/workflow-runs`, `/inbox`, `/auth/session`, and collaboration endpoints
- browser profiling for the inbox/session shell to quantify duplicate fetch overhead

## Bottom Line

The platform is already meaningfully wired across frontend and backend for operator auth, workflow runs, inbox, graph execution, and collaboration. The main weaknesses are not total absence of integration, but rather:

- inconsistent failure semantics
- duplicated frontend request patterns
- heavy fallback/mock dependence
- environment-specific behavior divergence
- missed opportunities for async execution, caching, and incremental synchronization

The fastest wins for user experience are:

1. eliminate duplicate session/inbox fetching
2. stop treating all requests as `no-store`
3. cache operator session/platform metadata
4. make writes fail closed where operator trust matters
5. move long-running run execution off synchronous request paths