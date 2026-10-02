# 05 · Information Architecture

## 1. Structure at a glance

| Space | Question it answers | Primary objects |
|---|---|---|
| **Home** | What needs me, what's running, what changed? | Approvals, active runs, today's tasks, briefs |
| **Chat** | Talk to the operator | Conversations, inline plans, approvals, artifacts |
| **Tasks** | What is all my work and where does it stand? | Initiatives, projects, tasks, cycles, views, triage |
| **Runs** | What has the agent done and how? | Runs, timelines, actions, evidence, spend |
| **Boards** | Where do I think visually? | Whiteboards, models |
| **Memory** | What does it know and believe? | Memory items, proposals, columns, sources |
| **Library** | What can it use? | Skills, connections (MCP and apps), engines and models, playbooks |
| **Peers** | Who am I connected to? | Peers, spaces, delegation requests |
| **Security** | What is allowed, and what happened? | Policies, grants, sessions, audit log, posture |
| **Settings** | How is it configured? | Areas, profile, devices, notifications, storage, updates |

An **area switcher** (all areas or one) sits in the shell and filters every space. It is the equivalent of Linear's team switcher.

## 2. Home

Four zones, in order: **Waiting on you** (approvals and questions, oldest first, with risk badges), **Running now** (live runs with step, engine and spend), **Today** (tasks due or in the current cycle, by area), **Changed since you were last here** (completed runs, new triage items, peer activity). Each item opens the inspector.

## 3. Chat

- Thread list grouped by area and pinned.
- A conversation can spawn a task and run ("do this") or stay conversational ("help me think").
- Plan cards, approval cards, artifacts and run progress render inline.
- `@` references tasks, memory items, boards, files and peers. `/` invokes skills and commands.
- A conversation is always scoped to one area (switchable, with a visible marker).

## 4. Tasks

Mirrors Linear's structure. See [16](16-work-tracker.md) for behavior.

- **Inbox / Triage:** new items from triggers, sync and peers awaiting area, priority and owner.
- **My issues:** assigned to me, created by me, delegated to my agent.
- **Areas:** each with Backlog, Active, cycles, projects and its own workflow.
- **Projects** and **Initiatives:** overview, milestones, progress and updates.
- **Views:** saved filters with list, board (Kanban) and timeline layouts.

## 5. Runs

A list of runs filterable by state, area, engine and trigger. A run detail has tabs: **Timeline** (steps and actions with risk and grant), **Plan** (current and revisions), **Evidence** (sources, judgments, commitments), **Artifacts**, **Spend** (tokens, money, time per engine), **Audit**. During computer use, a **Session** tab shows the live view and recorded frames.

## 6. Boards

Two kinds, one index: **Whiteboards** (forever canvas) and **Models** (typed diagrams). Each board belongs to an area and optionally a space. See [17](17-canvas-and-whiteboard.md).

## 7. Memory

Tabs: **Items** (searchable, filterable by type, area, status), **Review** (proposed items in batches), **Columns** (column roles, their current beliefs for active tasks, calibration), **Sources** (ingested content with taint and extraction status).

## 8. Library

Tabs: **Skills**, **Connections**, **Engines** (local models, API accounts, vendor agents, context providers, with readiness and cost), **Playbooks** (reusable envelope and plan templates), **Assemblies** (column configurations). Each item shows trust state and its capability manifest.

## 9. Peers

Paired peers with status, spaces you share with each, inbound and outbound delegation requests, and pairing (invite, accept, revoke).

## 10. Security

Tabs: **Posture** (what is enforced right now, per control, with evidence), **Policies**, **Grants** (active, standing, expiring; revoke in one click), **Audit** (searchable log with chain verification), **Sessions** (computer-use history), **Exceptions** (actions allowed by override).

## 11. Global layer

- **Command palette** (Cmd/Ctrl+K): every action, object and setting.
- **Quick capture** (global hotkey): new task or note from anywhere in the OS.
- **Takeover HUD:** an always-on-top overlay during computer use with step, pause, take back and stop. See [12](12-computer-use.md) §5.
- **Panic key:** a global hotkey that stops all runs and releases input. Works even if the UI is unresponsive (handled by the native helper).
- **Tray:** status (idle, running, waiting on you), quick capture, pause all.
- **Inspector:** one right-hand panel for any object, everywhere.

## 12. URL model

Local web UI on loopback, also hosted in the desktop webview:

```
/home
/chat/:conversationId
/tasks/inbox · /tasks/my · /tasks/area/:areaKey · /tasks/:identifier · /tasks/project/:id · /tasks/view/:id
/runs · /runs/:runId[/timeline|plan|evidence|artifacts|spend|audit|session]
/boards/:boardId
/memory[/review|columns|sources] · /memory/:itemId
/library/skills/:id · /library/connections/:id · /library/engines/:id
/peers · /peers/:peerId · /spaces/:spaceId
/security[/posture|policies|grants|audit|sessions|exceptions]
/settings/...
```

Every object has a stable URL and a `locus://` deep link usable from the OS.

## 13. Responsive priorities

Desktop first. A compact companion layout (narrow window, and later mobile via a paired peer) supports Home, approvals, chat and quick capture only. Approving R3 actions from a phone is an H3 item that needs a signed, device-bound approval protocol.
