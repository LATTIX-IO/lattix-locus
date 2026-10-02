# 01 · Vision and Positioning

## The problem

A knowledge worker running several contexts at once (a primary role, a venture, clients, a personal life) now has access to more AI capability than they can use. That capability is fragmented and untrustworthy at the point where it would matter most:

1. **Capability is scattered across vendors and subscriptions.** Claude Code, Codex, ChatGPT, M365 Copilot and local models each hold a slice of what you pay for and know. None of them sees your whole working context, and none hands off to another.
2. **Assistants talk; they don't finish.** Chat tools answer a question and stop. Agents that do act are either coding-only, locked to one vendor's cloud, or so lightly governed that you cannot let them near your real accounts and desktop.
3. **Trust doesn't scale.** To delegate real work you have to know what the agent can touch, what it did, why, and how to undo it. Today that means watching it, which defeats the point.
4. **Your work state lives in other people's tools.** Tasks are in Linear, Asana, Jira and your head; thinking is on whiteboards nobody's assistant can read; memory is per-vendor and opaque.

## The thesis

> Locus is a local-first personal AI operator. You give it a task; it plans, works and iterates to completion on your machine, using whichever models and subscriptions fit the task, under a zero trust core that makes every action bounded, attributable and reversible, so you can hand over real work and trust what comes back.

Four ideas carry it:

- **One operator, many engines.** Locus owns the task, context, memory and policy. Models and vendor agents (local Ollama models, API models, Claude Code, Codex, M365 Copilot for work context) are interchangeable engines it routes to. See [10](10-inference-and-model-routing.md).
- **Takeover with an envelope.** Delegation is a contract: goal, done criteria, capabilities, budget and autonomy tier. Inside the envelope the agent runs to completion; at its edges it asks. See [11](11-agentic-model.md).
- **Security is the product, not a layer.** Every action passes one gateway that checks identity, capability, policy and data taint before it happens and records it after. Untrusted content can inform the agent but never authorize it. See [13](13-security-architecture.md).
- **Your state, your instance, your peers.** Tasks, memory, whiteboards and models live in your domain on your machine. Collaboration is peer-to-peer between instances, never a vendor cloud in the middle. See [16](16-work-tracker.md), [14](14-memory-and-knowledge.md), [18](18-federation-and-collaboration.md).

## What Locus is

| Facet | Description |
|---|---|
| **An always-on assistant** | A background service on Windows and macOS with chat, voice (H2) and a tray presence, available whenever the machine is |
| **A delegation engine** | Tasks from chat, the tracker, schedules, inbox or peers become runs that iterate to done |
| **A computer operator** | Drives desktop apps and browsers on Windows and macOS, in your session or an isolated one |
| **A personal work system** | A Linear-style tracker for professional and personal work, a forever whiteboard, and data-centric models |
| **A memory you govern** | Durable, inspectable, editable memory with provenance, organized by area and reasoned over by cognitive columns |
| **An extensible harness** | Skills and MCP connections, each with a declared capability manifest and a trust lifecycle |
| **A peer in a network** | Connects to colleagues' instances to share spaces and delegate tasks, under signed, attenuated grants |

## What Locus is not

| Not | Because |
|---|---|
| A hosted SaaS or cloud agent | Local-first is a core promise. No mandatory external control plane. Hosted model APIs are optional engines, not the platform |
| A workflow automation builder (Zapier/n8n style) | Workflows exist as reusable skills and playbooks, but the primary interaction is delegating outcomes, not wiring nodes |
| A coding-only agent | Coding is one skill family. The target is general knowledge work, admin, research, communication and personal tasks |
| A model or a chatbot | Chat is one surface. The product is the operator: context, delegation, governance and follow-through |
| A fleet or SOC platform | Built for one person and their peers, not for administering thousands of endpoints |
| A replacement for every tracker | The native tracker is your system of record. Linear, Asana and Jira sync in where teams require them |
| A way around vendor terms | Subscription capacity is used through each vendor's own sanctioned client, never by reusing tokens. See [10](10-inference-and-model-routing.md) §3 |

## Differentiators

1. **Vendor-neutral delegation.** One task can use a local model to classify, Codex to write code and Claude Code to review it, with one envelope, memory and audit trail.
2. **Enforced, not declared, zero trust.** Capability tokens, policy decisions and taint checks gate every action at run time, and the posture is visible. (Today several of these are declared but not enforced. See [20](20-roadmap-and-decisions.md) §1.)
3. **Fast typed judgment as a safety layer.** Laya runs locally as a System One classifier on every risky step (injection screening, action risk, intent match) in tens of milliseconds, before a slower model or a human is asked.
4. **Cognitive columns.** Independent reasoning units keep separate models of a task and vote, so a single bad context window does not silently drive an irreversible action.
5. **Personal and professional in one place, separated by policy.** Areas keep a client's data, your venture's data and your family logistics in distinct scopes with distinct engines and grants.
6. **Peer-to-peer collaboration.** Two people can co-own a project or board from their own instances, with no shared server.

## Success measures

Targets to calibrate after the first 30 days of daily use.

| Measure | Why it matters | Initial target |
|---|---|---|
| Delegated tasks completed to done criteria with only planned approvals | Proves takeover works | ≥ 70% |
| Unplanned interventions per completed run | Proves autonomy is calibrated | ≤ 1 median |
| Escaped policy violations (an R3/R4 action without a valid grant) | Proves the core holds | 0 |
| Approval prompts answered "deny" or "edit" | Proves gates are meaningful, not noise | 10–35% |
| Share of judgments served locally (T0/T1 or local models) | Proves local-first and cost control | ≥ 60% of calls |
| Median time from task handoff to first useful action | Proves it feels like an assistant, not a batch job | < 20 s |
| Memory recall precision on "what did we decide about X?" | Proves memory is trustworthy | ≥ 90% judged correct |
| Days per week the operator is used for real work | Proves it earned a place | ≥ 5 |

Anti-measures (signs we are building the wrong thing): approvals you click through without reading; runs you watch end to end because you don't trust them; work tracked outside Locus because the tracker is too slow; engines chosen by habit rather than by the router.
