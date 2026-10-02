# Lattix xFrontier — React Flow Node Specs & Execution Design

## Purpose

Define a production-ready React Flow architecture where **canvas configuration is the source of truth** for backend execution of:

- multi-agent workflows,
- individual agents,
- guardrail pipelines,
- tool/API orchestration,
- human-in-the-loop review.

This spec is the canonical blueprint for:

- React Flow node contracts (ports, config, UI behavior),
- graph serialization and validation,
- runtime execution mapping,
- collaboration and whiteboard ergonomics,
- self-built alternatives to paid Pro examples/features.

---

## React Flow capability baseline (from docs)

We standardize on `@xyflow/react` with custom nodes and typed handles.

Core capabilities we rely on:

- Built-in interactivity: drag, zoom, pan, multi-select, connect/disconnect edges.
- Custom nodes and handles (`nodeTypes`, `<Handle />`, `sourceHandle` / `targetHandle`).
- Built-in components: `MiniMap`, `Controls`, `Background`, `Panel`.
- Edge and handle validation (`isValidConnection`, handle class states, custom edge types).
- Performance guidance: memoized node/edge components, memoized callbacks/options, avoid broad store subscriptions.
- Advanced patterns: sub flows, multiplayer sync strategy, whiteboard interactions.

Reference docs used:

- https://reactflow.dev/learn
- https://reactflow.dev/learn/concepts/built-in-components
- https://reactflow.dev/learn/customization/custom-nodes
- https://reactflow.dev/learn/customization/handles
- https://reactflow.dev/learn/advanced-use/performance
- https://reactflow.dev/learn/advanced-use/multiplayer
- https://reactflow.dev/learn/advanced-use/whiteboard
- https://reactflow.dev/pro (for awareness of paid examples we will replicate in-house)

---

## Core principles

1. **React Flow first**  
   Node UX, handles, edge semantics, and viewport behavior are implemented in React Flow as the editing source of truth.

2. **Graph is declarative; backend is executor**  
   Canvas defines what to run. Backend adapters define how to run.

3. **Strict typed ports + aliases**  
   Canonical ports are enforced; legacy aliases are normalized for backward compatibility.

4. **Safety by default**  
   Guardrails are first-class nodes, with published ruleset selection and stage-aware behavior.

5. **Pro-aware, self-built**  
   We do not depend on paid code examples. We implement equivalent capabilities ourselves where needed.

---

## React Flow graph model

## Node model

```json
{
  "id": "node-uuid",
  "type": "frontier/agent",
  "title": "Agent Runtime",
  "x": 580,
  "y": 120,
  "config": {
    "agent_id": "ai-architect-eng-agent",
    "model": "gpt-5.2",
    "temperature": 0.2
  }
}
```

## Edge model

```json
{
  "from": "node-a",
  "to": "node-b",
  "from_port": "response",
  "to_port": "result"
}
```

## Runtime payload model

```json
{
  "schema_version": "frontier-graph/1.0",
  "nodes": [],
  "links": [],
  "input": {
    "message": "User prompt",
    "currentUser": "alice",
    "currentTenant": "acme",
    "vars": {
      "ticketId": "INC-123"
    },
    "runtime": {
      "provider": "openai",
      "model": "gpt-5.2",
      "session_id": "agent:uuid"
    }
  }
}
```

`schema_version` is a required backend contract field for persisted and executed React Flow graphs. The current canonical version is `frontier-graph/1.0`. Backends should default missing values to the canonical version for backward compatibility, but save/publish/run paths must reject unknown or future schema versions unless an explicit migrator has been added.

---

## Canonical port contract (v2)

Two-plane contract for all nodes:

- **Control plane**: `in` (`flow`) and `out` (`flow`)
- **Data plane**: named typed ports (`prompt`, `query`, `result`, `response`, etc.)

### Canonical matrix

- `frontier/workflow`
  - inputs: `in`, `payload`
  - outputs: `out`, `result`
- `frontier/trigger`
  - outputs: `out`, `payload`
- `frontier/prompt`
  - inputs: `in`, `context`
  - outputs: `out`, `prompt`
- `frontier/agent`
  - inputs: `in`, `prompt`, `context`, `retrieval`, `memory`, `tool_result`, `guardrail`
  - outputs: `out`, `response`, `retrieval_query`, `tool_request`, `state_delta`, `memory`, `guardrail`
- `frontier/retrieval`
  - inputs: `in`, `query`, `filters`
  - outputs: `out`, `documents`, `grounding_context`
- `frontier/tool-call`
  - inputs: `in`, `request`, `auth_context`, `context`
  - outputs: `out`, `result`, `status`, `guardrail`
- `frontier/memory`
  - inputs: `in`, `read_query`, `write_payload`
  - outputs: `out`, `memory_state`, `context`
- `frontier/guardrail`
  - inputs: `in`, `candidate_output`, `context`
  - outputs: `out`, `approved_output`, `violations`, `decision`
- `frontier/human-review`
  - inputs: `in`, `candidate`
  - outputs: `out`, `approved`, `feedback`
- `frontier/manifold`
  - inputs: `in_a`, `in_b`, `in_c`, `in_d`
  - outputs: `out`, `data`
- `frontier/output`
  - inputs: `in`, `result`
  - outputs: `out`

### Target enterprise expansion

The current implemented matrix is a strong baseline, but the target enterprise-ready node language should also add these generic families:

- `frontier/router`
- `frontier/transform`
- `frontier/iterator`
- `frontier/error-handler`
- `frontier/event`
- `frontier/data-store`
- `frontier/wait`

These are intentionally generic families, not vendor-specific node types. See [enterprise-node-taxonomy.md](enterprise-node-taxonomy.md) for the proposed target taxonomy, builder placement, and phased rollout.

### Alias compatibility

Legacy ports (`output`, `data`, `approved`, `tool_output`, etc.) are normalized to canonical names in both:

- edge rendering/validation (frontend),
- execution payload mapping (backend).

---

## Node specifications (React Flow + runtime)

For each node type, this section defines UI contract, handle contract, required config, and runtime behavior.

## 1) `frontier/trigger`

- **React Flow node UI**
  - widgets: `trigger_mode`, schedule/webhook/event fields.
  - no required inbound data edge.
- **Handles**
  - output `out:flow`, `payload:data`.
- **Required config**
  - conditional by mode (e.g., schedule requires cron).
- **Runtime**
  - initializes run envelope (`run_id`, timestamps, trigger metadata).

## 2) `frontier/prompt`

- **React Flow node UI**
  - widgets: objective/style/audience/safety + `system_prompt_text`.
  - multiline editor supports runtime variables.
- **Handles**
  - input `in`, `context`; output `out`, `prompt`.
- **Required config**
  - `system_prompt_text`.
- **Runtime**
  - composes canonical system prompt payload for downstream agents.

## 3) `frontier/agent`

- **React Flow node UI**
  - widgets: `agent_id`, `model`, `temperature`, `execution_mode`, `system_prompt`.
  - accepts variable expressions in string fields.
- **Handles**
  - input `in` + `prompt` required via connection or inline prompt fallback.
  - output `response`, `tool_request`, `retrieval_query`, etc.
- **Required config**
  - `agent_id`, `model`.
- **Runtime**
  - loads prompt/context/retrieval/memory/tool outputs, executes model, emits typed outputs.

## 3b) `frontier/workflow`

- **React Flow node UI**
  - widgets: `workflow_id`, `handoff_mode`, `entry_message`, `output_binding`.
  - intended mainly for playbook-level orchestration and subflow composition.
- **Handles**
  - input `in`, `payload`; output `out`, `result`.
- **Required config**
  - `workflow_id`.
- **Runtime**
  - invokes a saved workflow definition as a child execution unit and returns the bound output downstream.

## 4) `frontier/tool-call`

- **React Flow node UI**
  - widgets: `tool_id`, HTTP method, timeout, retries.
  - optional tool guardrails (`tool_input_guardrail`, `tool_output_guardrail`).
- **Handles**
  - input `in`, `request` required.
  - output `result`, `status`, `guardrail`.
- **Required config**
  - `tool_id`.
- **Runtime**
  - enforces egress and approval policy, executes tool adapter, validates and emits result.

## 5) `frontier/retrieval`

- **React Flow node UI**
  - widgets: `source_type`, `top_k`, thresholds.
- **Handles**
  - input `query` required.
  - output `documents`, `grounding_context`.
- **Required config**
  - `source_type`.
- **Runtime**
  - validates source allowlist, executes retrieval pipeline, returns normalized docs/context.

## 6) `frontier/memory`

- **React Flow node UI**
  - widgets: `action`, `scope`, `session_id`.
  - supports variable values (e.g., `var.currentUser`, `{{var.sessionId}}`).
- **Handles**
  - input `write_payload` (append), `read_query` (read).
  - output `memory_state`, `context`.
- **Required config**
  - `action`, `scope`.
- **Runtime**
  - reads/writes scoped memory store with run/session/user/tenant semantics.

## 7) `frontier/guardrail`

- **React Flow node UI**
  - widgets include `ruleset_id` selectable from **published guardrail rulesets**.
  - also stage/action/message/keyword/length settings.
- **Handles**
  - input `candidate_output` required for meaningful checks.
  - output `approved_output`, `violations`, `decision`.
- **Required config**
  - `tripwire_action`.
  - if `ruleset_id` set, it must exist and be published.
- **Runtime**
  - merges selected ruleset config with node-local config (node wins).
  - applies stage-aware policy:
    - `allow`
    - `reject_content` (emit `reject_message`)
    - `raise_exception` (fail path/run)

## 8) `frontier/human-review`

- **React Flow node UI**
  - widgets: reviewer group, approvals/SLA policy.
- **Handles**
  - input `candidate`; output `approved`, `feedback`.
- **Required config**
  - `reviewer_group`.
- **Runtime**
  - emits approval task and blocks/resumes path on decision.

## 9) `frontier/output`

- **React Flow node UI**
  - widgets: destination + format.
- **Handles**
  - input `in` and `result` required.
- **Required config**
  - `destination`, `format`.
- **Runtime**
  - persists/emits final output artifact/event payload.

## Planned enterprise node families

The following families are part of the target node language but are not yet implemented as first-class builder/runtime nodes:

- `frontier/router`
  - deterministic branching and path selection.
- `frontier/transform`
  - deterministic payload shaping, mapping, and templating.
- `frontier/iterator`
  - for-each, batching, chunking, and paginated traversal.
- `frontier/error-handler`
  - retry, fallback, compensation, and escalation behavior.
- `frontier/event`
  - async publish/subscribe, callback wait, queue/topic coordination.
- `frontier/data-store`
  - explicit CRUD and state mutation against business systems.
- `frontier/wait`
  - delay, timeout, and resume-at-time orchestration.

These should be introduced as generic families first, then specialized into narrower execution profiles later.

---

## React Flow interaction spec

- **Connection rules**
  - `isValidConnection` enforces port type compatibility.
  - handle aliases normalized before compatibility checks.
- **Edge lifecycle**
  - create via drag/connect.
  - delete via edge double-click/context menu.
  - reconnection must preserve type validity.
- **Node lifecycle**
  - add via context menu.
  - drag/move updates serialized coordinates.
  - node config edits update graph state immediately.
- **Viewport/UI**
  - `MiniMap`, `Controls`, `Background`, `Panel` always available.

---

## Runtime variable interpolation spec

String configs support runtime variable resolution:

- exact token: `var.path.to.value`
- inline templates: `{{var.path.to.value}}` and `${var.path.to.value}`

Context sources include:

- `input.*`
- `runtime.*`
- `vars.*`
- node results (`nodeResults.*`)
- canonical aliases (`currentUser`, `currentTenant`, `sessionId`, etc.)

Rules:

- exact token returns native type where possible,
- inline templates stringify object/list values as JSON,
- unresolved tokens remain literal (fail-open for authoring ergonomics).

---

## Validation and publish gates

## Design-time validation

- required node config fields,
- required inbound connections on required ports,
- edge source/target existence,
- no self-loop unless future loop semantics introduced,
- DAG cycle detection (unless dedicated loop node is introduced).

## Publish-time validation

- same graph structural checks as design-time,
- guardrail ruleset references must resolve to published rulesets,
- block publish on invalid graph.

## Release promotion semantics

- **Publish** creates an immutable released revision and updates the published listing for the definition.
- **Activate** promotes one published revision to the runtime-active revision used by execution paths.
- First publish may auto-activate for backward compatibility.
- Later publishes do **not** need to change live runtime behavior until an explicit activate action occurs.
- Rollback restores authoring state as a draft; runtime activation should be treated as a separate promotion decision.

---

## Self-built equivalents for Pro-oriented features

We may want Pro-level UX, but we will implement ourselves using OSS React Flow + custom code:

1. **Lasso selection**
   - custom overlay + geometric hit-testing for nodes/edges.
2. **Eraser mode**
   - pointer path collision detection against node bounds/edge paths.
3. **Rectangle/shape annotations**
   - annotation nodes (non-executable types) persisted in graph.
4. **Freehand drawing**
   - capture polyline strokes and store as annotation nodes.
5. **Collaborative editing**
   - durable graph state sync with CRDT/server strategy.
   - ephemeral cursor/connection previews broadcast separately.
6. **Undo/redo timeline**
   - immutable graph patch history with bounded stack.
7. **Node grouping/subflows**
   - parent container node + z-index/layer rules + collapse semantics.

All of the above are implementation goals; none require paid templates/examples.

---

## Performance architecture (React Flow guidance applied)

- memoize custom node and edge components (`React.memo`),
- memoize callbacks/options (`useCallback`, `useMemo`),
- avoid broad subscriptions to full nodes/edges arrays in unrelated UI,
- hide collapsed subgraphs (`hidden`) for large canvases,
- reduce expensive CSS effects on high node counts,
- avoid unnecessary animated edges for large graphs.

---

## Collaboration state model

Durable (must sync reliably):

- nodes (`id`, `type`, `data/config`, `position`, dimensions),
- edges (`id`, `source/target`, handles, data).

Ephemeral (best-effort):

- cursors,
- transient connection previews,
- temporary drag ghost states,
- viewport position sharing.

Per-user local UI (never authoritative shared state):

- selected elements,
- local panels/inspectors open state.

---

## Backend execution architecture

1. **Graph compiler/validator**
   - compiles React Flow JSON into canonical executable DAG.
2. **Runtime adapter layer**
   - primary orchestrator + optional delegated runtimes.
3. **Node executors**
   - one executor per node type.
4. **Run state + events**
   - structured events for timeline/debugging.
5. **Policy/security layer**
   - secret indirection, allowlists, approvals, guardrail enforcement.

---

## Phased delivery

## Phase 1 (current MVP baseline)

- Trigger, Prompt, Agent, Workflow, Retrieval, Tool, Memory, Guardrail, Human Review, Manifold, Output
- Draft/save/publish flow
- Validate and execute with run logs

## Phase 2

- Router, Transform, Error Handler
- Graph schema migrations
- Undo/redo and richer whiteboard tools

## Phase 3

- Iterator, Wait, Event, Data Store
- Graph schema migrations for enterprise orchestration primitives
- Enterprise-safe deterministic payload contracts

## Phase 4

- Multiplayer collaboration hardening
- Advanced profiling and very-large-graph optimizations
- Enterprise governance and RBAC overlays

---

## Open decisions

1. Schema versioning and migration strategy (`schema_version`).
2. Runtime-mutable vs publish-locked config fields.
3. Standard typed payload schemas per port.
4. Canonical trace schema for UI timeline + observability backends.
5. Annotation node interoperability with execution graphs.
