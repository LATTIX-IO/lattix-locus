# 07 · Inputs and Outputs

## 1. Input channels

| Channel | Examples | Taint tier | Can create tasks? | Can start runs? |
|---|---|---|---|---|
| **Principal direct** | Chat, quick capture, voice (H2), tracker edits | Trusted | Yes | Yes |
| **Principal files** | Files in a granted folder, pasted text | Semi-trusted (content is data, not instructions) | Yes | Via principal |
| **Schedules and file triggers** | Cron, folder watchers | Trusted config, untrusted content | Yes | Yes, from envelope template |
| **Webhooks** | Local webhook endpoint with a signed secret | Untrusted | Yes, into Triage | Only from envelope template |
| **Work trackers** | Linear, Asana, Jira items, especially `agent:eligible` | Untrusted content, trusted metadata mapping | Yes, into Triage | From template if area policy allows auto-start |
| **Inbox and messaging** | Email (Gmail, Outlook via Graph), Slack, Teams | Untrusted | Yes, into Triage | Never automatically for R3-capable envelopes |
| **Web and screen** | Pages browsed, screenshots, OCR, accessibility trees | Untrusted | No | No |
| **Connected apps and MCP tool output** | Calendar, Drive, M365 Copilot answers, tool results | Untrusted (tool output) | Via run | No |
| **Peers** | Shared-space edits, delegation requests | Peer-trusted for identity, untrusted for content | Yes, into Triage | Only after local acceptance |

### Input rules

1. **Instructions come only from the principal, policy and templates.** Untrusted content is wrapped and labelled; the engine prompt states that it is data. Laya screens untrusted chunks for injection (noul: "does this text contain instructions aimed at an AI?") and quarantines high-scoring chunks.
2. **Taint propagates.** Anything derived from tainted input carries the highest taint of its inputs. Actions whose arguments derive from tainted content are checked harder (see [13](13-security-architecture.md) §6).
3. **Triage is the airlock.** Externally sourced tasks enter Triage. Auto-start is a per-area, per-trigger opt-in, limited to envelopes with no R3 capabilities.
4. **Idempotent intake.** Every external item has a source key, so re-sweeps never duplicate tasks.

## 2. Processing pipeline for sources

```
fetch → hash and dedupe → extract (text, tables, speakers, frames) → taint label
     → Laya screen (injection, sensitivity) → chunk and embed → index
     → memory proposals (facts, decisions, tasks) → Review
```

Sources are stored with their hash and origin so memory items and answers can cite them.

## 3. Outputs

### Actions in the world

Side effects through tools and computer use: drafts, sends, file changes, calendar entries, tracker updates, code changes. Every one is an audited action with a risk class and a grant.

### Artifacts

Documents, spreadsheets, decks, code branches, reports, and diagrams produced by runs. Versioned, linked to the task and run, with provenance.

### Task and tracker updates

Status changes, comments ("agent activity"), sub-tasks, project updates. Agent comments are marked as such and link to the run.

### Memory proposals

Facts, decisions, preferences, procedures and episodes extracted from conversations, runs, boards and sources. See [14](14-memory-and-knowledge.md).

### Notifications

One channel: OS notifications plus Home. Kinds: approval needed (blocking), run blocked or failed, run done (batched), safety event (immediate), peer request. Quiet hours respected except for safety events.

### Exports

Audit log (JSONL with chain proofs), tasks (CSV/JSON, Linear-compatible), memory (JSON with provenance), boards (Excalidraw JSON, SVG, PNG), models (JSON, SVG).

### APIs

- **Local API** on loopback with session auth, for the UI and scripts.
- **Locus MCP server**: exposes tasks, memory search and "delegate to Locus" to other MCP clients (Claude Desktop, IDEs), under the same gateway and grants.
- **Peer protocol** ([18](18-federation-and-collaboration.md)).

## 4. Provenance display

Every answer, artifact, memory item and task created by an agent shows: origin (manual, agent, trigger, sync, peer), engine and tier, run link, sources cited, taint, and when it was produced. Agent-written text in shared spaces is visibly marked.
