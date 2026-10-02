# Locus — Product Documentation

**Status:** Draft v0.1 · 2 Oct 2026 · Owner: James Booth
**Audience:** Anyone (human or agent) designing, building, or reviewing Locus.

> **Naming.** The product is **Locus** (formerly xFrontier). Code identifiers are not renamed by these docs: the repo (`lattix-xfrontier`), package (`lattix-frontier`), modules (`frontier_runtime`, `frontier_tooling`), and node types (`frontier/*`) keep their names until a dedicated rename task changes them. Delivery is tracked in the Linear project [Locus](https://linear.app/lattix/project/locus-3b160e533200/overview).

This folder defines **what Locus is, who it is for, and how it should behave**. It is the product layer above the engineering and status records at the repo root (`AGENTS.md`, `DESIGN.md`, `SECURITY.md`, `THREAT-MODEL.md`, `QUALITY_SCORE.md`, `PLANS.md`) and in [`docs/`](../).

> **Direction change (Oct 2026).** Locus was framed as a *multi-agent orchestration platform* centred on a workflow builder. It is now a **local-first personal AI operator**: one always-on assistant that you chat with, hand tasks to, and let take over your computer, contained by a zero trust core. The builder, cognitive columns and governance work carry forward. They now serve the operator rather than being the product. See [01](01-vision-and-positioning.md) and [20](20-roadmap-and-decisions.md) D-01.

## Precedence

When documents disagree:

1. **`docs/product/`** decides product intent: users, flows, information architecture, interaction model, feature scope and non-goals.
2. **`AGENTS.md` invariants, `SECURITY.md` and `THREAT-MODEL.md`** decide engineering and security guarantees. Product docs never weaken them. Where a product doc *raises* the bar (for example [13](13-security-architecture.md)), the security docs are updated to match.
3. **`PRODUCT_SENSE.md`** is superseded by [01](01-vision-and-positioning.md) and [03](03-product-principles.md). Keep it as a pointer until it is rewritten.
4. **`docs/*.md` implementation and status records** (harness status, sandboxing, desktop installer, column layer plan) describe what *is* or what was planned. Product docs describe what *should be*.

If you change the product direction, change it here first, then implement.

## Documents

| # | Document | Answers |
|---|---|---|
| 01 | [Vision and positioning](01-vision-and-positioning.md) | Why Locus exists, what it is and is not, how we know it works |
| 02 | [Personas and jobs](02-personas-and-jobs.md) | Who uses it, in which context, to get what done |
| 03 | [Product principles](03-product-principles.md) | The rules every feature and screen must obey |
| 04 | [Concept model](04-concept-model.md) | Principals, domains, tasks, runs, envelopes, actions, grants, memory, columns |
| 05 | [Information architecture](05-information-architecture.md) | Spaces, navigation, global layer, URL model |
| 06 | [User flows](06-user-flows.md) | End-to-end journeys |
| 07 | [Inputs and outputs](07-inputs-and-outputs.md) | What goes in, what comes out, how each is governed |
| 08 | [Feature catalog](08-feature-catalog.md) | Capabilities by area with release horizon |
| 09 | [Interaction patterns](09-interaction-patterns.md) | Reusable UX building blocks |
| 10 | [Inference and model routing](10-inference-and-model-routing.md) | Local models, API providers, subscription CLI engines, M365 Copilot, Jev/Laya, routing tiers |
| 11 | [Agentic model](11-agentic-model.md) | Takeover, task envelopes, autonomy tiers, run loop, cognitive columns, always-on |
| 12 | [Computer use](12-computer-use.md) | Desktop and browser control on Windows and macOS, modes, safety |
| 13 | [Security architecture](13-security-architecture.md) | Zero trust zones, policy, capability grants, taint, secrets, egress, audit |
| 14 | [Memory and knowledge](14-memory-and-knowledge.md) | Memory types and scopes, columns, governance, sources |
| 15 | [Skills, MCP and extensions](15-skills-mcp-and-extensions.md) | Skill format and lifecycle, MCP gateway, extension trust |
| 16 | [Work tracker](16-work-tracker.md) | Linear-modelled task tracking for knowledge work and personal life |
| 17 | [Canvas and whiteboard](17-canvas-and-whiteboard.md) | Forever whiteboard (Excalidraw), data-centric models (diagram-js), when to use which |
| 18 | [Federation and collaboration](18-federation-and-collaboration.md) | Peer-to-peer instances, shared spaces, delegation across peers |
| 19 | [Platform and deployment](19-platform-and-deployment.md) | Windows and macOS desktop, always-on daemon, storage, packaging |
| 20 | [Roadmap and decisions](20-roadmap-and-decisions.md) | Horizons, decisions taken, open questions, current-state honesty |

## Using these docs as build context

- For UI work, read 03, 05, 09 and the relevant section of 06 and 08 before touching code.
- For runtime work, read 04, 11 and 13 first. Nothing that executes may bypass the gateway described in 13 §4.
- For provider or engine work, read 10. For anything touching another person's instance, read 18.
- The feature catalog (08) states *intent and horizon*, not build status. Build status lives in a dated status record (the latest is the 2 Oct 2026 System State Review). **origin/main is regressed** (PR #18 deleted 218 files; the fullest state is `ae4d703`), so check status before assuming a capability exists. See [20](20-roadmap-and-decisions.md) §1, H0.

## Terms used throughout

| Term | Meaning |
|---|---|
| **Principal** | Whoever an action is attributed to: a human, an agent instance, or a peer instance |
| **Domain** | A person's sovereign data boundary on their own instance. Nothing leaves it without an explicit share |
| **Space** | A shared, synchronized container (project, board, document) that two or more peers have joined |
| **Area** | A context you organize life and work by (BairesDev, Lattix, Personal, a client). Scopes tasks, memory, grants and engines |
| **Task** | A tracked unit of work in the work tracker, for you, your agent or a peer |
| **Run** | One execution of a task by an agent, with a plan, steps, actions, evidence and outcome |
| **Envelope** | The bounds a run operates inside: goal, done criteria, capabilities, budget, autonomy tier, deadline |
| **Action** | One side-effecting operation (tool call, keystroke, click, send), classified by risk R0–R4 |
| **Grant** | Permission for a class of actions: once, for this run, or standing. Issued as an attenuable capability token |
| **Engine** | Something that can think and act for a run: a local model, an API model, or a vendor agent CLI (Claude Code, Codex) |
| **Judgment** | A typed System One answer (choice, score, noul) from Laya or Jev, with calibrated confidence |
| **Column** | An independent reasoning and memory unit that keeps its own model of a subject and votes in an assembly |
| **Commitment** | An assembly's fused decision with confidence, dissent, blockers and next actions |
| **Gateway** | The single policy enforcement point every tool call, model call and egress passes through |
