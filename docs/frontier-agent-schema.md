# Lattix xFrontier — Canonical Agent Schema (Proprietary)

## Purpose

This document defines the **canonical Frontier agent schema**.  
This schema **is the agent**.

It is not a file-authoring spec and not an adapter-specific spec. It is the runtime contract that fully defines an agent for:

- backend configuration,
- ReactFlow graph semantics,
- framework integrations,
- tool/data integration,
- memory and guardrails,
- execution, observability, and enforcement.

Any Frontier agent—from a simple inline chatbot to a fully automated multi-agent AI coworker—must be representable by this schema.

---

## Canonical definition model

Frontier agent definitions are persisted and operated via backend canonical objects.

## AgentDefinition (canonical)

```json
{
  "id": "agent-id-or-uuid",
  "name": "Agent Name",
  "version": 1,
  "status": "draft|published|archived",
  "type": "form|graph",
  "config_json": {
    "meta": {},
    "runtime": {},
    "reasoning": {},
    "graph_json": { "nodes": [], "links": [] },
    "knowledge": {},
    "integrations": {},
    "mcp": {},
    "a2a": {},
    "tools": {},
    "memory": {},
    "guardrails": {}
  }
}
```

### Status lifecycle

- `draft`: editable, not production-routable
- `published`: eligible for runtime execution
- `archived`: immutable historical reference

---

## Required schema domains inside `config_json`

To be considered a complete, executable Frontier agent, `config_json` should contain these domains.

1. **meta**
   - business purpose, ownership, tags, capabilities, audit labels
2. **runtime**
   - model defaults, execution mode, framework routing hints
3. **reasoning**
  - agent reasoning strategy and policy controls (deliberation depth, planner/critic toggles, reasoning budget)
  - MUST support safe reasoning operation without requiring exposure of raw internal chain-of-thought in outputs
4. **graph_json**
   - ReactFlow-executable topology (`nodes`, `links`)
5. **knowledge**
  - RAG/retrieval policy (sources, chunking/index profile, top-k, ranking strategy)
6. **integrations**
   - external tool/data connectivity policy and references
7. **mcp**
  - Model Context Protocol server policies, allowed endpoints, and capability scopes
8. **a2a**
  - agent-to-agent communication policy, trust boundaries, and allowed peers/topics
9. **tools**
  - tool registry references, execution profile, and authorization constraints
10. **memory**
   - scope defaults, retention, and access boundaries
11. **guardrails**
  - safety policies, platform signal configuration, stage-aware tripwire behavior

The backend can infer defaults, but these domains define the complete agent contract.

---

## Graph contract (ReactFlow-backed execution)

### GraphPayload shape

```json
{
  "nodes": [
    {
      "id": "agent-node-1",
      "type": "frontier/agent",
      "title": "Agent Runtime",
      "x": 610,
      "y": 90,
      "config": {
        "agent_id": "agent-id-or-uuid",
        "model": "gpt-5.2",
        "temperature": 0.2
      }
    }
  ],
  "links": [
    {
      "from": "prompt-node",
      "to": "agent-node-1",
      "from_port": "prompt",
      "to_port": "prompt"
    }
  ],
  "input": {
    "message": "Run task",
    "vars": { "ticketId": "INC-123" },
    "runtime": {
      "session_id": "session:abc",
      "engine": "native"
    }
  }
}
```

### Primitives

- `GraphNode`: `id`, `type`, `title`, `x`, `y`, `config`
- `GraphEdge`: `from`, `to`, optional `from_port`, `to_port`

### Executable node taxonomy

### Current implemented node families

- `frontier/trigger`
- `frontier/prompt`
- `frontier/agent`
- `frontier/workflow`
- `frontier/tool-call`
- `frontier/retrieval`
- `frontier/memory`
- `frontier/guardrail`
- `frontier/human-review`
- `frontier/manifold`
- `frontier/output`

### Target enterprise-ready expansion

To support enterprise-grade orchestration without proliferating vendor-specific node types, the canonical node language should expand to include these generic families:

- `frontier/router`
- `frontier/transform`
- `frontier/iterator`
- `frontier/error-handler`
- `frontier/event`
- `frontier/data-store`
- `frontier/wait`

These are general-purpose node families that can later be specialized into concrete variants such as rules routing, MCP-backed tools, SQL-backed state updates, or queue-backed event coordination.


See [enterprise-node-taxonomy.md](enterprise-node-taxonomy.md) for the detailed proposal.

---

## Runtime integration contract (frameworks/tools/data)

The canonical schema is framework-agnostic. Runtime adapters consume it.

### Supported runtime engines

- `native`
- `langgraph`
- `langchain`
- `semantic-kernel`
- `autogen`

### Runtime policy controls

- `default_runtime_engine`
- `allowed_runtime_engines`
- `allow_runtime_engine_override`
- `enforce_runtime_engine_allowlist`

### Required runtime metadata output

Each run must emit:

```json
{
  "runtime": {
    "requested_engine": "langgraph",
    "selected_engine": "langgraph",
    "executed_engine": "native",
    "mode": "compatibility|native",
    "allow_override": true,
    "allowed_engines": ["native", "langgraph"],
    "node_mapping": {
      "frontier/agent": "framework.llm_node"
    }
  }
}
```

This guarantees observability and auditability regardless of adapter implementation.

---

## Guardrail contract (security and enforcement)

Guardrails are first-class schema components, not optional add-ons.

### Core guardrail keys

- `stage`: `input|output|tool_input|tool_output`
- `tripwire_action`: `allow|reject_content|raise_exception`
- `blocked_keywords`, `required_keywords`
- `min_length`, `max_length`
- `detect_secrets`
- `reject_message`

### Guardrail stage semantics (must be explicit)

Guardrails are stage-scoped and may evaluate different payloads:

- `input`
  - evaluates initial user/task input before agent/tool execution
  - intended for prompt-injection, unsafe intent, and policy-precheck controls
- `output`
  - evaluates model-generated content before returning/publishing
  - intended for quality/safety/compliance and data leakage controls
- `tool_input`
  - evaluates outbound tool/API request payloads
  - intended for command injection, exfiltration intent, and scope/policy checks
- `tool_output`
  - evaluates inbound tool/API responses
  - intended for sanitization, policy filtering, and sensitive-data suppression

### Platform signal controls (OSS detectors by default)

Signal detection is platform-native. Users configure **how** to enforce, not **which detector library** to assemble.

Canonical control keys:

- `enable_platform_signals`
- `platform_signal_enforcement`: `off|audit|block_high|raise_high`
- `platform_signal_detect_prompt_injection`
- `platform_signal_detect_pii`
- `platform_signal_detect_command_injection`
- `platform_signal_detect_exfiltration`

Default detector posture:

- open-source heuristic detectors enabled by default
- open-source enrichment detectors enabled by default when available in platform runtime

Examples of supported OSS signal engines:

- Presidio (`presidio_analyzer`)
- additional platform-vetted OSS detectors for injection/exfiltration/anomaly signals

Signal findings are enforcement inputs and can block/reject/raise by policy.

> Implementation note: legacy runtime keys using `foss_*` may exist during migration. Canonical schema naming is `platform_signal_*`.

---

## Memory contract (multi-dimensional)

Memory is schema-defined and scope-aware.

### Memory config keys

- `action`: `append|read|clear`
- `scope`: `run|session|user|tenant|agent|workflow|global`
- optional identifiers: `session_id`, `user_id`, `tenant_id`, `agent_id`, `workflow_id`

### Memory outputs

- `memory_state`
- `context`
- `memory_items`

### Memory policy expectation

The schema must support both:

- short-lived execution memory,
- long-lived operational memory with access boundaries.

---

## Standalone capability depth requirements

For `AgentDefinition` to be a true standalone model of an agent, schema depth must cover all capabilities required for independent operation:

1. **Reasoning/process controls**
  - planning strategy, critique/review loops, bounded reasoning budgets, reflection toggles
2. **Model/runtime controls**
  - provider/model defaults, failover strategy, execution mode, framework adapter policy
3. **Knowledge/RAG controls**
  - retrieval sources, ranking policy, grounding constraints, citation requirements
4. **MCP controls**
  - approved MCP servers, capability scope allowlists, connection security settings
5. **A2A controls**
  - trusted peers, protocol policies, message contract scope, signing/auth requirements
6. **Tool controls**
  - explicit tool definitions, auth references, egress constraints, risk tiering
7. **Guardrail controls**
  - stage-specific safety policies and platform signal enforcement profile
8. **Memory controls**
  - scope model, retention policy, privacy/RBAC boundaries, redact-on-read/write options

If any of the above are externalized without schema representation, the agent is only partially defined.

---

## Validation contract (publish and run safety)

To be publishable/executable, an agent graph must pass:

- at least one `frontier/trigger`
- no missing edge endpoints
- no self-loops
- DAG cycle checks
- required per-node config checks
- required port connectivity checks
- published guardrail ruleset resolution where specified

Selected required per-node fields:

- `frontier/prompt`: `system_prompt_text`
- `frontier/agent`: `agent_id`, `model`
- `frontier/tool-call`: `tool_id`
  For staged MCP usage, `mcp_connection_id` is an optional companion field that resolves an approved MCP connection record at runtime. When this is present, the runtime fills `mcp_server_url` from the approved staged record instead of depending on a hand-entered endpoint alone.
- `frontier/retrieval`: `source_type`
- `frontier/memory`: `action`, `scope`
- `frontier/guardrail`: `tripwire_action`
- `frontier/human-review`: `reviewer_group`
- `frontier/output`: `destination`, `format`

---

## Complexity tiers (all represented by same schema)

The same canonical schema supports multiple agent complexity profiles.

### Tier A — Inline chatbot (minimal)

Typical graph:

- `trigger -> prompt -> agent -> output`

Minimal requirements:

- single model runtime profile
- basic guardrail policy
- session memory optional

### Tier B — Assisted operator agent

Typical graph:

- `trigger -> prompt -> agent -> retrieval/tool-call -> guardrail -> output`

Adds:

- retrieval/data sources
- tool policy boundaries
- stronger guardrail stages

### Tier C — Fully automated AI coworker

Typical graph:

- multi-agent collaboration with memory, guardrails, human review, and integration orchestration

Adds:

- multi-step workflows
- cross-system tool/data integration
- policy-gated autonomy and approvals
- full runtime observability contract

---

## Canonical example (full agent object)

```json
{
  "id": "customer-success-coworker",
  "name": "Customer Success Coworker",
  "version": 12,
  "status": "published",
  "type": "graph",
  "config_json": {
    "meta": {
      "description": "Handles customer issue triage and follow-up workflows.",
      "owners": ["@ops", "@cx"],
      "tags": ["support", "operations", "guardrails"]
    },
    "runtime": {
      "model_defaults": {
        "provider": "openai",
        "model": "gpt-5",
        "temperature": 0.2
      },
      "engine_policy": {
        "default_runtime_engine": "native",
        "allowed_runtime_engines": ["native", "langgraph", "autogen"],
        "allow_runtime_engine_override": false,
        "enforce_runtime_engine_allowlist": true
      }
    },
    "reasoning": {
      "strategy": "plan-execute-review",
      "reasoning_budget": {
        "max_steps": 8,
        "max_deliberation_ms": 12000
      },
      "self_review": true,
      "expose_internal_reasoning": false
    },
    "graph_json": {
      "nodes": [],
      "links": []
    },
    "knowledge": {
      "retrieval_mode": "hybrid",
      "sources": ["kb://default"],
      "top_k": 6,
      "citation_required": true
    },
    "integrations": {
      "sources": ["kb://default"],
      "tools": ["tool/http", "tool/crm"],
      "egress_allowlist": ["api.internal.local"]
    },
    "mcp": {
      "enabled": true,
      "allowed_servers": ["https://mcp.notion.com/mcp"],
      "capability_allowlist": ["read", "query"]
    },
    "a2a": {
      "enabled": true,
      "trusted_agents": ["orchestration-agent", "compliance-control-mapper"],
      "require_signed_messages": true
    },
    "tools": {
      "default_timeout_ms": 30000,
      "high_risk_patterns": ["delete", "send", "execute", "write", "admin"],
      "require_human_approval_for_high_risk": true
    },
    "memory": {
      "default_scope": "session",
      "allow_scopes": ["session", "user", "tenant"]
    },
    "guardrails": {
      "default_ruleset_id": "core-guardrails",
      "tripwire_default_action": "reject_content",
      "enable_platform_signals": true,
      "platform_signal_enforcement": "block_high",
      "platform_signal_detect_prompt_injection": true,
      "platform_signal_detect_pii": true,
      "platform_signal_detect_command_injection": true,
      "platform_signal_detect_exfiltration": true
    }
  }
}
```

---

## Non-goals for this document

- File-based authoring schema design
- Legacy seed/bootstrapping conventions
- Framework-specific DSL lock-in

Those may exist operationally, but they do not define the canonical agent contract.

---

## Contract decision

Frontier standardizes on this principle:

1. The backend canonical agent object is the source of truth.
2. ReactFlow graph semantics are part of the canonical agent schema.
3. Framework adapters execute the schema; they do not own it.
4. Guardrails and memory are schema-defined, platform-enforced runtime concerns.

This ensures the agent definition remains stable, secure, and portable across simple and highly complex runtime patterns.
