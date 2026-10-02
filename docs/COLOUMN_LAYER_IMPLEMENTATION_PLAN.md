Understood. I’ll provide the full product spec inline so you don’t depend on file download.

---

# xFrontier Product Spec

## Gap Closure: Current System → Columnar Cognitive System

---

## 1. Summary

xFrontier today is a **local-first, multi-agent orchestration platform** with strong:

* control plane authority
* runtime isolation and policy enforcement
* envelope-based communication
* tiered memory and graph projection
* workflow and agent execution

The target state is a **distributed cognitive system** with:

* primitive cognitive columns
* assemblies and consensus
* uncertainty-aware decision making
* predictive + evaluative reasoning loops
* agents acting as coordinators, not primary thinkers

This spec defines the **minimum product and architecture changes required** to bridge that gap.

---

## 2. Problem Statement

### Current limitation

The system is still:

* agent-centric
* prompt-driven
* context-injected
* sequentially orchestrated

This results in:

* correlated reasoning failures
* weak uncertainty handling
* implicit evaluation criteria
* no structured belief state
* no distributed reasoning

### Target capability

xFrontier must become a system that:

* maintains **multiple independent models of a task**
* fuses them via **explicit consensus**
* reasons using **state, evidence, prediction, and evaluation**
* adapts over time
* operates under bounded, inspectable cognition

---

## 3. Gap Analysis

### A. Missing Cognitive Layer

Current:

* agents perform reasoning

Missing:

* structured cognitive primitives
* local belief models
* independent reasoning units

---

### B. No Assembly Concept

Current:

* agents collaborate via envelopes

Missing:

* task-specific cognitive coalitions
* explicit column participation
* consensus logic

---

### C. Memory is Retrieval-Oriented

Current:

* short-term + long-term + graph

Missing:

* live belief state
* causal state representation
* per-column state ownership

---

### D. Messaging is Execution-Oriented

Current:

* envelopes carry tasks and payloads

Missing:

* belief exchange
* prediction signaling
* dissent propagation
* uncertainty signals

---

### E. No Formal Evaluation/Commitment Model

Current:

* output is produced and optionally reviewed

Missing:

* explicit evaluation rubric
* confidence thresholds
* commitment logic
* dissent tracking

---

### F. Agents Are Overloaded

Current:

* agents do everything

Missing:

* separation between:

  * cognition
  * coordination
  * execution

---

### G. UI is Prompt-Centric

Current:

* agents defined by prompts + tools

Missing:

* goal definition
* evaluation definition
* assembly configuration
* autonomy control

---

## 4. Core Product Additions

---

## 4.1 Primitive Column Runtime

### New Runtime Primitive

```text
Column
```

Each column must support:

* observe()
* update_belief()
* predict()
* evaluate()
* emit_message()
* update_confidence()

---

### Required Column Types (v1)

* Goal
* State
* Decomposition
* Evidence
* Prediction
* Evaluation
* Uncertainty
* Synthesis

---

### Column State Model

```text
ColumnState
- column_id
- assembly_id
- belief_set
- evidence_refs
- confidence
- last_updated
- adaptation_metrics
```

---

## 4.2 Assembly Runtime

### New Runtime Object

```text
Assembly
```

Defines:

* participating columns
* inference mode
* consensus policy
* stopping condition

---

### Assembly Definition

```text
AssemblyDefinition
- columns[]
- overlays[]
- consensus_policy
- inference_mode
- budget_constraints
- escalation_policy
```

---

## 4.3 Consensus Engine

### Required Capabilities

* weighted belief fusion
* dissent tracking
* veto handling
* confidence aggregation

---

### Output Structure

```text
Commitment
- decision
- confidence
- supporting_columns
- dissenting_columns
- blockers
- next_actions
```

---

## 4.4 Cognitive Messaging Layer

Extend envelope system with:

### New Message Types

* belief_update
* evidence_claim
* prediction_branch
* evaluation_score
* uncertainty_signal
* synthesis_proposal
* dissent
* commitment

---

### Requirements

* small payloads
* reference-based data
* strict schema validation
* message budgets

---

## 4.5 Column State + Memory Integration

### New Memory Layer

```text
Causal State Layer
```

Extends current memory with:

* belief graphs
* hypothesis tracking
* prediction outcomes
* confidence history

---

### Changes Required

* per-column memory partitions
* belief persistence
* outcome tracking
* feedback loops

---

## 4.6 Agent Refactor

### Current

Agent = reasoning + execution + planning

---

### Target

Agent = coordination shell

---

### New Responsibilities

* interpret intent
* create assembly
* route observations
* invoke tools
* manage lifecycle
* finalize output

---

### Removed Responsibilities

* full reasoning
* evaluation
* uncertainty handling
* belief modeling

---

## 4.7 UI / UX Changes

---

### A. Goal Node (NEW)

Inputs:

* intent
* success criteria
* constraints
* priorities
* output contract

---

### B. Assembly Node (NEW)

Inputs:

* columns enabled
* overlays
* consensus policy
* inference mode

---

### C. Evidence Node (NEW)

Inputs:

* allowed sources
* required evidence
* ranking rules

---

### D. Evaluation Node (NEW)

Inputs:

* scoring criteria
* thresholds
* blockers

---

### E. Commitment Node (NEW)

Inputs:

* autonomy level
* confidence threshold
* escalation rules

---

### F. Human Checkpoint Node (ENHANCED)

Inputs:

* trigger conditions
* context presentation
* required action

---

### G. Agent Node (MODIFIED)

Now configures:

* assembly used
* tools allowed
* execution role
* output formatting

NOT primary reasoning.

---

## 5. System Architecture (Target)

```text
UI / Builder / Run Console
        ↓
Executive / Agent Shell Layer
        ↓
Assembly Layer
        ↓
Primitive Column Layer
        ↓
Runtime / Messaging / Memory
        ↓
Tools / MCP / Workflows
        ↓
External Systems
```

---

## 6. Implementation Workstreams

---

## Workstream 1: Column Runtime

Deliver:

* column interface
* lifecycle hooks
* local state store
* column registry

---

## Workstream 2: Assembly + Consensus

Deliver:

* assembly manager
* consensus engine
* conflict handling
* stopping rules

---

## Workstream 3: Messaging Upgrade

Deliver:

* new message schemas
* envelope extensions
* routing rules
* payload constraints

---

## Workstream 4: Memory Evolution

Deliver:

* belief storage
* causal state layer
* outcome tracking
* calibration data

---

## Workstream 5: Agent Refactor

Deliver:

* agent shell abstraction
* assembly orchestration
* tool routing logic
* output commit handling

---

## Workstream 6: UI / Builder Evolution

Deliver:

* new node types
* validation rules
* assembly configuration UX
* evaluation/commitment UX

---

## Workstream 7: Adaptation + Learning

Deliver:

* feedback ingestion
* calibration models
* performance metrics
* learning loops

---

## 7. Phased Roadmap

---

### Phase 1: Minimal Cognitive Loop

* Goal column
* Evidence column
* Synthesis column
* basic assembly
* basic consensus

---

### Phase 2: Accuracy Layer

* Evaluation column
* Uncertainty column
* evaluation node UI
* confidence thresholds

---

### Phase 3: State + Structure

* State column
* Decomposition column
* state modeling
* task graph support

---

### Phase 4: Prediction + Adaptation

* Prediction column
* outcome tracking
* learning loops
* calibration

---

## 8. Risks

---

### 1. Over-complex UX

Mitigation:

* templates
* progressive disclosure

---

### 2. Performance overhead

Mitigation:

* sparse messaging
* bounded assemblies
* adaptive execution

---

### 3. Column redundancy

Mitigation:

* enforce independence
* enforce scoped inputs

---

### 4. Lack of measurable improvement

Mitigation:

* A/B compare against agent baseline
* track accuracy and correction rate

---

## 9. Success Metrics

---

### Accuracy

* reduction in hallucination rate
* improvement in evaluation score

---

### Reliability

* % tasks meeting success criteria
* reduction in human overrides

---

### Calibration

* confidence vs correctness alignment

---

### Efficiency

* tool calls per task
* time to convergence

---

### Adaptation

* performance improvement over time

---

## 10. End State

xFrontier becomes:
* a **distributed cognitive system**
* built on **modular reasoning primitives**
* coordinated through **assemblies**
* governed by **explicit evaluation and uncertainty**
* executed through **secure, bounded infrastructure**

Not:
* a better agent framework
* not a prompt orchestration tool
* not a single-model system

But:
**a structured, inspectable, adaptive intelligence fabric**
