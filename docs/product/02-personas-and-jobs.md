# 02 · Personas and Jobs

## Primary persona — The Principal (operator-owner)

**Who.** A senior technical professional who works across several contexts at once: a primary role (for example, Director of Technology at a services firm), a venture, client engagements, and a personal life with its own admin load. An architect by trade, comfortable with canvases, models and systems thinking. Uses several AI subscriptions daily and is security-literate enough to distrust any agent that can't explain its permissions.

**Context.** Windows and macOS machines. Dozens of open threads across email, Slack/Teams, trackers and documents. Time is the binding constraint. Will delegate anything that can be delegated safely.

**What they want from Locus.**

| Job | Done well looks like |
|---|---|
| **Delegate and walk away** | "Prepare the Bain POC follow-up pack from last week's notes and the RFP." It plans, gathers, drafts, checks and comes back with output and evidence, asking only at real decision points |
| **Supervise a takeover** | Watch it drive the desktop when wanted, interrupt instantly, take the controls back, resume |
| **Triage what comes in** | Inbox, messages and tracker changes become proposed tasks with suggested area, priority and owner (me, my agent, a peer) |
| **Plan and track all work** | One tracker for professional and personal work, organized by area, client and solution, with boards, cycles and projects |
| **Think visually** | An infinite whiteboard for brainstorming and architecture that the assistant can read, reference and help formalize |
| **Recall** | "What did we decide about Envoy authz?" answered with the source, date and confidence |
| **Extend** | Add a skill or MCP server in minutes, knowing exactly what it can reach |
| **Trust and audit** | See, for any run, what it did, under which grant, with which engine, and undo what is undoable |
| **Collaborate** | Share a project or board with a colleague's instance, and delegate a task to their agent with their consent |

**Frustrations to design against.** Approval spam. Agents that loop or burn budget silently. Having to re-explain context. Different memories in every tool. Security theatre.

## Secondary personas

### The Peer (collaborator with their own instance)

A colleague or partner who runs their own Locus. They share spaces and accept or decline delegated tasks. Their domain is as sovereign as the principal's: they see only what is shared, and their agent acts under *their* policy, not the requester's. See [18](18-federation-and-collaboration.md).

### The Builder (principal in authoring mode)

The same person, writing skills, playbooks, policies, column assemblies and engine routes. Wants forms and declarative files (YAML/Markdown) first, with diagrams as a view. Needs test, evaluation and version history before promoting anything to standing use.

### The Auditor (principal or a trusted reviewer)

Reviews posture and history: policies in force, grants outstanding, actions taken under exceptions, skills with broad capabilities. May be the principal on a weekly review, or a security reviewer on a peer's request.

### Guest contributor (H3)

A person without an instance who interacts with a shared space through a signed link (for example, a client commenting on a board). Read-mostly, never an action principal.

## Persona-driven defaults

| Setting | Default for the Principal |
|---|---|
| Landing screen | Home: active runs, waiting approvals, today's tasks, what changed |
| Autonomy | Tiered by action risk ([11](11-agentic-model.md) §5) |
| Engines | Local first for classification and summarization; the router picks hosted engines per task and area policy |
| Areas | Seeded from onboarding (for example: BairesDev, Lattix, Personal), each with its own engine and egress policy |
| Computer use | Takeover in the real session, with a visible HUD and panic key; sensitive apps denied by default |
| Memory capture | Automatic proposals; promotion to durable memory reviewed in batches |

## Anti-personas

| Not for | Why |
|---|---|
| IT administrators managing an endpoint fleet | No central console, no MDM. Peers are equals |
| Users who want a hosted chatbot | Local install, local data and a security model are the point |
| No-code automation builders | Delegation of outcomes, not node wiring, is the primary model |
| Teams wanting a shared SaaS project tool | Shared spaces exist, but the tracker is personal-first and peer-synced |

## Buyer vs user

For H1–H2 the principal is buyer, user, builder and auditor. AGPL distribution means other operators can install it; onboarding and defaults must therefore not assume the author's machine, paths or accounts (an existing defect: `WORKFLOW.md` hard-codes a workstation path).
