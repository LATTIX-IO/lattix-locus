# 16 · Work Tracker

A personal, Linear-modelled task system for **all** of the principal's work: professional (role, venture, clients) and personal. It's the system of record for what needs doing, and the main way work is handed to the agent.

## 1. Why Linear as the model

Linear gets the fundamentals right: speed, keyboard-first operation, opinionated defaults, a clean hierarchy (initiatives → projects → issues → sub-issues), triage, cycles and views. Its vocabulary and defaults are software-centric (teams, sprints, story points, git integration). Locus keeps the structure and speed and generalizes the vocabulary and defaults to knowledge work and life.

| Linear | Locus | Change |
|---|---|---|
| Workspace | Domain | Personal and sovereign; peers share via spaces |
| Team | **Area** | Contexts like BairesDev, Lattix, Personal, a client. Each has its own workflow, prefix and defaults |
| Initiative | Initiative | Same |
| Project | Project | Same, plus a linked board and memory |
| Issue | **Task** | Same structure; identifier `AREA-123` |
| Sub-issue | Sub-task | Same, unlimited depth (display capped at 3 levels by default) |
| Cycle | Cycle | Optional per area (weekly by default when enabled) |
| Estimate (points) | Effort | T-shirt or hours; optional |
| Labels / label groups | Labels / label groups | Seeded groups: **Category**, **Client**, **Solution**, **Context** (@desk, @phone, @errand) |
| Triage | Triage | Also receives agent proposals, sync items and peer requests |
| Assignee | Assignee | Me, **my agent**, a peer, or a peer's agent |
| Git integration | Run integration | Tasks link to runs, artifacts and evidence |

## 2. Object model

| Object | Fields |
|---|---|
| **Area** | key (prefix), name, icon, workflow statuses, default labels, default playbook for agent tasks, cycle settings, engine and egress policy link |
| **Initiative** | name, description, areas, projects, target date, health (on track / at risk / off track), updates |
| **Project** | name, area(s), lead, members (incl. peers), status, milestones, start/target dates, linked board, updates |
| **Milestone** | name, date, tasks |
| **Task** | id, title, description (Markdown), status, priority (urgent/high/medium/low/none), assignee, effort, due date, start date, labels, project, milestone, cycle, parent, relations (blocks, blocked by, related, duplicate), recurrence, attachments, comments, activity |
| **View** | filter (any field, including label groups and "assigned to my agent"), grouping, ordering, layout (list / board / timeline), visibility (private, shared space) |

Default workflow per area: **Triage → Backlog → Todo → In progress → In review → Done | Canceled**. Areas can rename, add or hide statuses (for example "Waiting on someone" for personal and client areas).

## 3. Organizing ("bucketize")

The principal can organize the same tasks in several ways without duplicating them:

- **By area** (structural, one per task): BairesDev, Lattix, Personal, Kepler…
- **By label groups** (many per task): Client = Bain, Legend Biotech, Medtronic; Solution = Databricks, RFID, Zero trust; Category = Admin, Finance, Health, Home, Learning.
- **By project and initiative** (outcomes).
- **By time:** cycle, due date, timeline.
- **By owner:** me, my agent, a peer.

Saved views combine these ("Client: Bain, not done, grouped by status" as a board). Cross-area views are allowed for the principal (for example "Everything due this week").

## 4. Layouts

| Layout | Use |
|---|---|
| **List** | Default; dense, grouped, sortable, multi-select editing |
| **Board (Kanban)** | Group columns by status (default) or any single-value field; swimlanes by another field; drag to change |
| **Timeline** | Projects and tasks with start and due dates; dependencies drawn from `blocks` relations |
| **Calendar** (H2) | Due dates and scheduled agent runs |

## 5. Tasks and the agent

- **Assign to my agent:** sets the assignee to Locus and opens the envelope card from the area's default playbook. On start, a run is created and linked; status moves to In progress; agent activity posts to the task as comments.
- **Run outcomes map to status:** Done (criteria verified) → In review or Done (per area setting); Blocked → task gets a "Blocked" marker with the blocker text and stays In progress; Stopped → back to Todo with a note.
- **Agent suggestions:** the agent proposes sub-tasks, labels, dates, priorities and duplicates (Laya choice/score). Suggestions appear as ghost chips; one keystroke accepts.
- **Triage assistance:** new items arrive with suggested area, labels, priority and owner.
- **Project updates:** a weekly draft per project from task movement and run outcomes, for the principal to edit and post (H2).
- **Natural-language capture:** "remind me to renew the passport by March, personal, high" becomes a structured task.

## 6. Recurring and personal work

- Recurrence rules (every weekday, every 2 weeks on Friday, last day of month, N days after completion).
- Personal areas default to fewer statuses, no cycles and context labels.
- Privacy: personal areas default to local-only engines unless changed (area policy).

## 7. External tracker sync (H2)

| System | Default | Notes |
|---|---|---|
| Linear | Two-way for mapped teams/projects | Status, assignee, comments, labels; `agent:eligible` label drives agent intake |
| Asana | Read + selective write-back | Sections map to statuses; custom fields to labels |
| Jira | Read + selective write-back | Per-project status mapping |

Rules: one system of record per task (shown on the task); conflicts resolved last-writer-wins on scalar fields with a visible conflict note; external items enter through Triage unless a mapping auto-files them; write-back actions are R2/R3 through the gateway.

## 8. Shared projects (H3)

A project (with its tasks and board) can be shared as a space with peers. Each peer sees the shared project inside their own tracker, in an area of their choosing; edits sync by CRDT; assignees can be any member or member's agent; delegation to a peer's agent follows [18](18-federation-and-collaboration.md) §5.

## 9. Performance and keyboard

Local-first storage with optimistic UI. Budgets: list filter ≤ 50 ms, task open ≤ 100 ms p75, board drag commit ≤ 50 ms perceived. Full keyboard map in [09](09-interaction-patterns.md) §6.

## 10. Non-goals

- Time tracking and invoicing.
- Resource capacity planning across people.
- Replacing a team's shared tracker; sync instead.
- Git-centric features beyond linking runs, branches and PRs to tasks.
