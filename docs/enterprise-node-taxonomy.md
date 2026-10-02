# Lattix xFrontier — Enterprise Node Taxonomy Proposal

## Purpose

Turn the current node-gap review into a concrete node-language proposal for enterprise-ready agents, workflows, and playbooks.

This document focuses on **generic node families** that can later be specialized into product/domain-specific variants. Example: one `tool-call` family can back REST, GraphQL, MCP, SQL, SaaS, or internal-service specializations.

---

## Current baseline

Implemented node families today:

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

This is a strong base for:

- model execution,
- external tool usage,
- retrieval,
- memory,
- policy enforcement,
- human approvals,
- workflow and playbook composition.

The main remaining gaps are in:

- deterministic routing,
- deterministic data shaping,
- iteration and batching,
- runtime recovery/fallback,
- asynchronous event coordination,
- stateful business-system mutation,
- time-based orchestration inside a graph.

---

## Design rule

We should add **general-purpose node families**, not one-off branded nodes.

Good pattern:

- `frontier/tool-call`
  - REST API
  - GraphQL API
  - MCP tool
  - SQL procedure
  - Slack action
  - CRM action

Bad pattern:

- separate top-level node families for every downstream product or vendor.

---

## Recommended target taxonomy

### Already present and should remain first-class

| Family | Purpose | Best fit |
| --- | --- | --- |
| `frontier/trigger` | Start a graph from manual, schedule, webhook, event, or feedback input. | Workflow, Playbook |
| `frontier/prompt` | Deterministic prompt construction and role framing. | Agent, Workflow |
| `frontier/agent` | Run an agentic reasoning/execution unit. | Agent, Workflow |
| `frontier/workflow` | Call a workflow as a subflow. | Playbook |
| `frontier/tool-call` | Generic external tool/API/MCP/integration call. | Agent, Workflow, Playbook |
| `frontier/retrieval` | Generic knowledge access and grounding. | Agent, Workflow |
| `frontier/memory` | Read/write scoped execution memory. | Agent, Workflow, Playbook |
| `frontier/guardrail` | Stage-aware safety and policy checks. | Agent, Workflow, Playbook |
| `frontier/human-review` | Human approval, rejection, feedback gates. | Workflow, Playbook |
| `frontier/manifold` | Join and merge multiple inbound branches. | Workflow, Playbook |
| `frontier/output` | Final publication/persistence/emission of results. | Agent, Workflow, Playbook |

### Missing high-priority generic families

| Family | Why it is missing | Best fit |
| --- | --- | --- |
| `frontier/router` | Needed for if/else, rules, classification, and path selection. | Workflow, Playbook, Agent |
| `frontier/transform` | Needed for deterministic data shaping without forcing an LLM/tool hop. | Agent, Workflow, Playbook |
| `frontier/iterator` | Needed for for-each, batch, chunk, list, and paginated processing. | Workflow, Playbook |
| `frontier/error-handler` | Needed for retry, fallback, compensation, and graceful degradation. | Workflow, Playbook, Agent |
| `frontier/event` | Needed for async publish/subscribe, queue, callback, and resume flows. | Workflow, Playbook |
| `frontier/data-store` | Needed for explicit business-state CRUD/upsert beyond retrieval. | Workflow, Playbook, Agent |
| `frontier/wait` | Needed for delay, SLA timers, timeout branches, and resume windows. | Workflow, Playbook |

### Optional later families

| Family | Use when needed | Best fit |
| --- | --- | --- |
| `frontier/input` | Collect structured runtime parameters or forms mid-flow. | Workflow, Playbook |
| `frontier/expression` | Safe deterministic expressions when `transform` is insufficient. | Agent, Workflow |
| `frontier/annotation` | Non-executable planning/documentation nodes. | All builders |
| `frontier/container` | Group/subflow boundaries with collapse behavior. | Workflow, Playbook |

---

## Concrete generic node proposals

## 1) `frontier/router`

### Router Purpose

Select one or more outbound paths based on deterministic logic.

### Router Specializations

- rules router
- classifier router
- severity router
- approval router
- tenant/segment router
- escalation router

### Router Ports

- inputs:
  - `in: flow`
  - `candidate: data`
  - `context: data`
- outputs:
  - `match_a: flow`
  - `match_b: flow`
  - `default: flow`
  - `decision: data`

### Router Config

- `router_mode`: `rules | classifier | threshold | expression`
- `rules_json`
- `default_route`
- `allow_multi_match`
- `decision_key`

### Router Rationale

`manifold` solves join/merge. It does not solve branch selection.

---

## 2) `frontier/transform`

### Transform Purpose

Perform deterministic payload shaping between execution steps.

### Transform Specializations

- field mapper
- template renderer
- JSON normalizer
- extractor
- redactor/masker
- response flattener

### Transform Ports

- inputs:
  - `in: flow`
  - `source: data`
  - `context: data`
- outputs:
  - `out: flow`
  - `result: data`

### Transform Config

- `transform_mode`: `map | template | extract | redact | merge`
- `mapping_json`
- `template_text`
- `output_schema`
- `strict_validation`

### Transform Rationale

Enterprise graphs need deterministic shaping without burning model tokens or abusing tool nodes for local data manipulation.

---

## 3) `frontier/iterator`

### Iterator Purpose

Execute over collections, batches, or paginated sources.

### Iterator Specializations

- for-each iterator
- batch processor
- chunker
- pager
- scatter/gather coordinator

### Iterator Ports

- inputs:
  - `in: flow`
  - `items: data`
  - `context: data`
- outputs:
  - `loop: flow`
  - `done: flow`
  - `item: data`
  - `aggregate: data`

### Iterator Config

- `iteration_mode`: `foreach | batch | chunk | paginate`
- `batch_size`
- `max_items`
- `max_concurrency`
- `aggregation_mode`

### Iterator Rationale

Most enterprise automations operate on lists of tickets, users, documents, transactions, or alerts.

---

## 4) `frontier/error-handler`

### Error-Handler Purpose

Define failure policy explicitly in the graph.

### Error-Handler Specializations

- retry policy
- fallback provider/model/tool
- compensating action
- dead-letter handler
- escalation handler

### Error-Handler Ports

- inputs:
  - `in: flow`
  - `error: data`
  - `context: data`
- outputs:
  - `retry: flow`
  - `fallback: flow`
  - `escalate: flow`
  - `resolved: flow`
  - `error_state: data`

### Error-Handler Config

- `handler_mode`: `retry | fallback | compensate | escalate | dead_letter`
- `max_retries`
- `backoff_strategy`
- `retryable_error_codes`
- `fallback_target`
- `dead_letter_target`

### Error-Handler Rationale

Enterprise readiness depends on graceful recovery, not just successful happy paths.

---

## 5) `frontier/event`

### Event Purpose

Handle asynchronous communication and resumption.

### Event Specializations

- queue publisher
- topic publisher
- queue consumer
- callback waiter
- domain event emitter

### Event Ports

- inputs:
  - `in: flow`
  - `payload: data`
  - `context: data`
- outputs:
  - `out: flow`
  - `event_result: data`
  - `resume_payload: data`

### Event Config

- `event_mode`: `publish | consume | await_callback | emit`
- `transport_type`: `webhook | queue | topic | bus`
- `destination`
- `event_name`
- `timeout_ms`
- `correlation_key`

### Event Rationale

Enterprise workflows often need asynchronous coordination with external systems instead of synchronous tool chaining only.

---

## 6) `frontier/data-store`

### Data-Store Purpose

Perform explicit business-state reads and writes.

### Data-Store Specializations

- SQL read/write
- CRM CRUD
- ticket update
- document-store upsert
- cache read/write
- warehouse query

### Data-Store Ports

- inputs:
  - `in: flow`
  - `record: data`
  - `query: data`
  - `auth_context: data`
- outputs:
  - `out: flow`
  - `result: data`
  - `status: data`

### Data-Store Config

- `store_type`: `sql | nosql | crm | cache | document | warehouse`
- `operation`: `create | read | update | delete | upsert | query`
- `resource_name`
- `connection_ref`
- `write_policy`
- `transaction_mode`

### Data-Store Rationale

`retrieval` is not a business-system mutation primitive, and `tool-call` is too generic to make common enterprise state operations legible.

---

## 7) `frontier/wait`

### Wait Purpose

Pause and resume a graph on time-based conditions.

### Wait Specializations

- delay
- timeout gate
- reminder timer
- business-hours wait
- resume-at timestamp

### Wait Ports

- inputs:
  - `in: flow`
  - `payload: data`
- outputs:
  - `out: flow`
  - `timed_out: flow`
  - `resume_payload: data`

### Wait Config

- `wait_mode`: `delay | until_time | business_hours | timeout`
- `delay_ms`
- `resume_at`
- `timezone`
- `timeout_action`

### Wait Rationale

Scheduling only at the trigger level is not enough for long-running enterprise operating motions.

---

## Builder profile recommendations

Not every node family needs equal prominence in every builder.

### Agent Studio

Primary families:

- `prompt`
- `agent`
- `tool-call`
- `retrieval`
- `memory`
- `guardrail`
- `transform`
- `error-handler`
- `output`

Secondary families:

- `router`
- `human-review`
- `expression`

Avoid overloading Agent Studio with heavy orchestration-first nodes unless the product wants standalone agents to support richer control flow.

### Workflow Studio

Primary families:

- `trigger`
- `agent`
- `tool-call`
- `retrieval`
- `memory`
- `guardrail`
- `human-review`
- `router`
- `transform`
- `iterator`
- `error-handler`
- `wait`
- `event`
- `data-store`
- `manifold`
- `output`

This should become the main orchestration language.

### Playbook Studio

Primary families:

- `workflow`
- `router`
- `iterator`
- `error-handler`
- `wait`
- `event`
- `data-store`
- `manifold`
- `memory`
- `human-review`
- `output`

Playbooks should stay focused on higher-level operating motions, not prompt engineering or low-level model tuning.

---

## Phased implementation order

### Phase 1 — close the biggest orchestration gaps

Add:

- `frontier/router`
- `frontier/transform`
- `frontier/error-handler`

Why first:

- these unlock deterministic routing, shaping, and resilience,
- they improve nearly every graph without requiring runtime loop/state complexity,
- they are the minimum missing set for credible enterprise orchestration.

### Phase 2 — add scale and long-running flow support

Add:

- `frontier/iterator`
- `frontier/wait`
- `frontier/event`

Why second:

- these unlock batch operations, async resumption, and operating motions that stretch over time.

### Phase 3 — add business-system clarity

Add:

- `frontier/data-store`

Why third:

- some teams can initially model this with `tool-call`,
- but enterprise readability and governance improve once stateful CRUD is explicit.

### Phase 4 — power-user and UX extensions

Add if product direction supports them:

- `frontier/input`
- `frontier/expression`
- `frontier/annotation`
- `frontier/container`

---

## Recommended next canonical executable set

### Core canonical set

- `frontier/trigger`
- `frontier/prompt`
- `frontier/agent`
- `frontier/workflow`
- `frontier/tool-call`
- `frontier/retrieval`
- `frontier/memory`
- `frontier/guardrail`
- `frontier/human-review`
- `frontier/router`
- `frontier/transform`
- `frontier/iterator`
- `frontier/error-handler`
- `frontier/event`
- `frontier/data-store`
- `frontier/wait`
- `frontier/manifold`
- `frontier/output`

### Enterprise-ready interpretation

With that set, Frontier would have a complete general-purpose node language for:

- agent execution,
- orchestration and routing,
- external tool and system integration,
- data shaping,
- memory and retrieval,
- safety and approvals,
- failure recovery,
- asynchronous operating motions,
- business-system state mutation.

---

## Bottom line

The current system already has the right generic connector primitive in `frontier/tool-call`.

The most important missing enterprise-ready **general node families** are:

1. `frontier/router`
2. `frontier/transform`
3. `frontier/iterator`
4. `frontier/error-handler`
5. `frontier/event`
6. `frontier/data-store`
7. `frontier/wait`

These should be added as generic families first, then specialized into product/domain-specific variants as needed.
