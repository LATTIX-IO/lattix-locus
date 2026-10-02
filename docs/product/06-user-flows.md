# 06 · User Flows

Each flow lists trigger, steps, what must be true, and the docs that govern it.

## F0 · Install and first run

1. Install the signed desktop package (Windows MSI/MSIX, macOS notarized DMG).
2. First-run wizard: create the principal identity (keypair in the OS keychain), choose the data location, seed areas (suggested: Work, Venture, Personal; renameable).
3. Engines: detect Ollama and other local servers; offer to pull a provenance-clean default model and the Laya judge; detect installed vendor CLIs (Claude Code, Codex) and their login state; add API keys optionally.
4. Permissions: request macOS Accessibility and Screen Recording, or Windows UI Automation access, with a plain explanation and a test.
5. Security baseline: show the posture page; set the panic key; choose default autonomy (tiered, recommended).

*Must be true:* working chat with a local model in under 10 minutes; no Docker required; nothing sent off-device before the user adds a hosted engine. Governs: [19](19-platform-and-deployment.md), [10](10-inference-and-model-routing.md), [13](13-security-architecture.md).

## F1 · Delegate a task from chat (takeover)

1. "Pull last week's Bain notes, the RFP and our case studies, and draft the POC follow-up pack in my Bain folder."
2. The operator resolves the area (BairesDev), creates task `BD-231`, and proposes an **envelope**: done criteria (pack drafted with sources, reviewed by a verification column), capabilities (read the Bain folder, Drive search, write drafts to the folder, no sending), budget (45 min, $3), tier (tiered).
3. User accepts or edits in one click. The run starts.
4. The run streams plan, steps and evidence. R0–R2 actions proceed. An R3 action (for example, sharing the doc externally) raises an approval card.
5. The verification column checks the done criteria. The run ends Done with artifacts linked to the task, or Blocked with the exact blocker.

*Must be true:* first useful action in < 20 s; no approval for in-envelope R0–R2 actions; result verified against criteria, not just "finished". Governs: [11](11-agentic-model.md), [13](13-security-architecture.md).

## F2 · Supervise a desktop takeover

1. A run needs a desktop app (for example, entering timesheet data in a web ERP, or reconciling a spreadsheet in Excel).
2. The HUD appears; the screen border shows agent control; the run narrates steps.
3. The user moves the mouse or presses a key: the agent pauses immediately and yields input.
4. The user fixes something, then clicks Resume, or Take over to finish manually, which ends the agent's session.
5. The panic key stops everything and releases input.

Governs: [12](12-computer-use.md).

## F3 · Triage incoming work (always-on)

1. Triggers produce candidates: a scheduled sweep, a new email that matches a rule, a Slack mention, a Linear/Asana/Jira item labelled `agent:eligible`, a peer's delegation request.
2. Laya classifies each candidate (area, actionable or not, urgency) and proposes a task with suggested owner (me, my agent, decline).
3. The Triage inbox shows proposals in batches; keyboard accept, edit or dismiss.
4. Tasks assigned to the agent start runs from their envelope template, or wait for the next scheduled window.

*Must be true:* tainted content (email bodies, messages) never sets capabilities or grants; the envelope comes from the template and policy. Governs: [07](07-inputs-and-outputs.md), [16](16-work-tracker.md), [13](13-security-architecture.md) §6.

## F4 · Plan and track my work

1. Open Tasks; pick an area or a view ("Client: Bain", "Solution: Databricks", "Personal: house").
2. Create, nest, estimate and prioritize tasks by keyboard; drag across a board; group by label, project or status.
3. Assign some tasks to "Locus", which makes them delegated with an envelope template; others stay with me.
4. Weekly: review the cycle, roll over unfinished tasks, read the auto-drafted project updates.

Governs: [16](16-work-tracker.md).

## F5 · Brainstorm on the whiteboard, then formalize

1. Open the area's forever whiteboard; sketch in a new frame.
2. Ask: "Turn this frame into a segmentation model" or "Make tasks from these sticky notes".
3. The operator proposes a diagram-js model or a task set, linked back to the frame (`formalizes`).
4. The board content is indexed into memory; later, "what did I sketch about Envoy authz?" finds the frame.

Governs: [17](17-canvas-and-whiteboard.md), [14](14-memory-and-knowledge.md).

## F6 · Recall and correct memory

1. Ask "what did we decide about OPA vs Regorus?"; the answer cites memory items and sources, with dates and confidence.
2. The user notices a stale item, opens it in the inspector, edits or marks it superseded.
3. Weekly memory review: accept or reject proposals in batch; the review date for stable items moves out.

Governs: [14](14-memory-and-knowledge.md).

## F7 · Add a skill or MCP connection

1. Import a skill folder or MCP server (local stdio or remote HTTP with OAuth).
2. The scanner reads the manifest, pins tool schemas, flags risky declarations (shell, broad file access, unrestricted egress) and runs a Laya injection screen on descriptions.
3. Evaluate in a sandbox with test prompts; review the capability manifest.
4. Trust for one area or globally. Any later schema change on an MCP tool suspends it until re-reviewed.

Governs: [15](15-skills-mcp-and-extensions.md).

## F8 · Route work to my subscriptions

1. A coding task in the Lattix area: the router picks Codex CLI (ChatGPT login) as the engine for implementation, and Claude Code for review, according to area policy.
2. Locus launches each CLI inside the run workspace sandbox, injects the Locus MCP gateway as its tool surface, and maps the CLI's own approval hooks to Locus approvals.
3. Spend and quota usage per engine show in the run's Spend tab.

Governs: [10](10-inference-and-model-routing.md) §3.

## F9 · Collaborate with a peer

1. Pair with a colleague by exchanging a signed invite (QR, link, or LAN discovery).
2. Share a project (tracker) and its board as a space. Both instances sync; edits merge.
3. Delegate `LTX-150` to the peer's agent. The peer sees the request with its proposed envelope, edits or accepts it under their own policy, and their agent runs it on their machine. Results return to the shared project.
4. Either party can leave the space or revoke the pairing; future sync stops and shared keys rotate.

Governs: [18](18-federation-and-collaboration.md).

## F10 · Weekly audit

1. Security → Posture: every control enforced, with test evidence age.
2. Review standing grants; revoke what isn't needed.
3. Review exceptions and R3 actions taken this week.
4. Verify audit chain integrity (one click).

Governs: [13](13-security-architecture.md).

## F11 · Schedule recurring work

1. "Every weekday at 7:45, sweep my inbox and Slack for things that need me and put them in Triage."
2. The operator creates a trigger with an envelope template (read-only mail and Slack scopes, task creation, no sending).
3. Each firing creates a run; results land in Triage; failures notify.

Governs: [11](11-agentic-model.md) §8.
