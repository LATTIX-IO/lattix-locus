# 04 · Concept Model

## 1. The shape of the model

```
Principal ──owns──▶ Domain ──contains──▶ Areas ──scope──▶ { Tasks, Memory, Boards, Models, Grants, Engine policy }
                       │
                       └──joins──▶ Spaces (shared with peers)

Task ──executed by──▶ Run ──bounded by──▶ Envelope
                       │
                       ├── Plan ──▶ Steps ──▶ Actions ──checked by──▶ Gateway (policy, grant, taint, judgment)
                       ├── Engine calls (local / API / vendor agent)
                       ├── Assembly of Columns ──▶ Commitments
                       ├── Evidence + Artifacts
                       └── Audit events (hash-chained)
```

Everything a run does is an **Action** checked by the **Gateway** against a **Grant**, inside an **Envelope**, attributed to a **Principal**, scoped by an **Area**. That chain is the core invariant.

## 2. Object catalog

### Identity and boundaries

| Object | What it is | Key attributes |
|---|---|---|
| **Principal** | An identity actions are attributed to | kind (human, agent, peer-agent, peer-human), keypair, display name, org membership attestations |
| **Instance** | One running Locus installation on a device | device key, owner principal, platform, version, posture summary |
| **Domain** | The owner's sovereign data boundary across their instances | owner, encryption root, retention policy |
| **Area** | A context: BairesDev, Lattix, Personal, a client | name, icon, engine policy, egress policy, default grants, data classification ceiling |
| **Space** | A shared container joined by two or more peers | members and roles, objects included, CRDT document set, sharing policy |

### Work

| Object | What it is | Key attributes |
|---|---|---|
| **Initiative** | A long-running goal spanning projects | area(s), target date, health, owner |
| **Project** | A bounded outcome with milestones | area, lead, status, milestones, target date, members |
| **Task** | A tracked unit of work (Linear "issue") | identifier (e.g. `LTX-142`), title, description, status, priority, estimate, due, labels, assignee (me / my agent / peer / peer's agent), parent, relations |
| **Cycle** | An optional time box per area | dates, scope, completion |
| **View** | A saved filter, grouping and layout over tasks | query, layout (list, board, timeline), shared or private |
| **Trigger** | Something that creates or starts tasks | kind (schedule, file, webhook, inbox rule, tracker sync, peer request), target area, envelope template |

Full tracker model in [16](16-work-tracker.md).

### Execution

| Object | What it is | Key attributes |
|---|---|---|
| **Run** | One attempt by an agent to complete a task | task, envelope, engine route, state (planning, running, waiting, blocked, done, stopped), spend, timeline |
| **Envelope** | The bounds of a run | goal, done criteria, capability set, budget (wall time, spend, action count), autonomy tier, deadline, area |
| **Plan / Step** | The agent's current decomposition | ordered steps, status, owning column or engine, revisions |
| **Action** | One side effect | tool, target, risk class R0–R4, grant used, taint of inputs, judgment results, result, undo handle |
| **Approval** | A human decision on a proposed action or plan | requested action(s), risk, preview/diff, choice (approve once / for run / standing, edit, deny), decider |
| **Engine** | An inference or agent backend | kind (local model, API model, vendor agent CLI, context provider), capabilities, data-handling class, cost model |
| **Artifact** | A durable output | type (document, file, code change, message draft), version, provenance |
| **Session** | A computer-use session | mode (observe, assist, takeover, isolated), target desktop, recorded frames policy |

### Governance

| Object | What it is | Key attributes |
|---|---|---|
| **Policy** | A rule set evaluated by the policy engine | scope (global, area, skill, peer), Rego source, version, tests |
| **Grant** | Permission for a class of actions | subject, action pattern, constraints (paths, domains, recipients, amounts), duration (once, run, standing, expiry), issuer, token |
| **Capability token** | The cryptographic form of a grant | Biscuit token, attenuations, expiry, revocation id |
| **Taint label** | Trust level of a piece of context | origin (user, system, local file, web, email, screen, peer, tool output), trust tier |
| **Judgment** | A typed System One answer | primitive (choice, score, noul), question, options, probabilities, confidence, model (Laya/Jev) |
| **Audit event** | An immutable record | sequence, hash, signature, principal, run, action, decision, redacted payload |

### Knowledge

| Object | What it is | Key attributes |
|---|---|---|
| **Memory item** | A remembered fact, preference, decision, procedure or episode | type, scope, area, content, provenance, confidence, status (proposed, active, superseded, forgotten), review date |
| **Column** | An independent reasoning and memory unit | role (goal, evidence, risk, plan, verification, domain), belief state, evidence refs, confidence |
| **Assembly** | A task-specific coalition of columns | columns, inference mode, consensus policy, stopping rule |
| **Commitment** | An assembly's fused decision | decision, confidence, supporting and dissenting columns, blockers, next actions |
| **Board** | A forever whiteboard | frames, elements, extracted entities, links to tasks and memory |
| **Model** | A data-centric diagram | model type (architecture, segmentation, dependency, assembly), typed elements, relationships |
| **Source** | Ingested content | origin, taint, hash, extraction state |

### Extensions

| Object | What it is | Key attributes |
|---|---|---|
| **Skill** | A packaged procedure (SKILL.md folder) | manifest (tools, egress, secrets, risk ceiling), version, signature, trust state |
| **Connection** | An MCP server or app integration | transport, auth, tool inventory with pinned schema hashes, egress, trust state |
| **Peer** | Another instance you've paired with | principal, device keys, spaces shared, delegation policy, revocation state |

## 3. Action risk classes

Risk is a property of the action, computed by code from the tool's declaration plus its arguments, and refined by a Laya judgment where the declaration is ambiguous.

| Class | Meaning | Examples | Default handling |
|---|---|---|---|
| **R0** | Read-only | Read file, search memory, screenshot, list calendar | Allowed within envelope scope |
| **R1** | Reversible, local | Write inside run workspace, create draft, local git commit, create task, move a window | Allowed within envelope scope |
| **R2** | Reversible, external or wider local | Save draft email in mailbox, create calendar hold, edit a shared doc with version history, edit files outside workspace with snapshot | Allowed if the envelope's capability set covers it |
| **R3** | Irreversible or outbound | Send, post, pay, delete without trash, push, merge, share with a peer, install software, accept invites | Approval required unless a standing or run grant covers the exact pattern |
| **R4** | Prohibited for agents | Export credentials, change policy, disable audit, widen own grants, act in denied apps | Never; only the human via the Security space |

## 4. Relationship vocabulary

`owns`, `scoped-to` (area), `shared-in` (space), `assigned-to`, `parent-of`, `blocks`, `relates-to`, `duplicates`, `executes` (run → task), `bounded-by` (run → envelope), `authorized-by` (action → grant), `derived-from` (memory/artifact → source/run), `supersedes`, `mentions` (board/memory → any object), `formalizes` (model element → board frame), `delegated-to` (task → peer).

## 5. Lifecycles

- **Task:** Triage → Backlog → Todo → In progress → In review → Done / Canceled (per-area customizable, like Linear team workflows).
- **Run:** Planning → Running ⇄ Waiting (approval, input, schedule) → Done / Blocked / Stopped. Every transition is an audit event.
- **Memory item:** Proposed → Active → Superseded / Forgotten. Forgotten items are deleted, not hidden, except for an audit stub that records the deletion.
- **Skill / Connection:** Imported → Scanned → Evaluated → Trusted (area-scoped or global) → Revoked.
- **Grant:** Issued → Used → Expired / Revoked.

## 6. Scopes

Every object has exactly one **area** (or "Unsorted" during triage) and at most one **space**. Policies, engines, egress and grants resolve area-first, then global. Cross-area context is denied by default: a run in the BairesDev area cannot read Lattix memory unless the envelope explicitly includes it and policy permits.
