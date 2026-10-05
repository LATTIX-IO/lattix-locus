# 11 · Agentic Model

Locus is one operator you delegate to. Under the hood it composes engines, columns, skills and sub-agents, but the user deals with **one assistant, many runs**.

## 1. What the operator does and doesn't do

| Does | Doesn't |
|---|---|
| Plan, act, observe, verify and revise until done criteria pass | Widen its own envelope or grants |
| Ask at envelope edges and for uncovered R3 actions | Take R4 actions, ever |
| Choose engines through the router | Treat untrusted content as instructions |
| Propose memory, tasks, follow-ups and playbooks | Promote its own memory proposals without review (except run-scoped working memory) |
| Use the desktop and browser like a person would | Operate denied apps or keep control when the user moves the mouse |
| Report honestly when blocked | End a run as "done" without verification |

## 2. The run loop

```
intake → envelope → plan
   ↻  select step → route engine → act (via gateway) → observe → update columns
      → verify step → (revise plan | next step | ask | stop)
→ verify done criteria → done | blocked | stopped
```

- **Plan** is a living object; revisions are versioned and visible.
- **Observe** includes tool results, screen state and new evidence. All are tainted per source.
- **Verify** runs per step (did the action have the intended effect?) and at the end (are the done criteria met?). The verification column owns the final check, using a different engine or prompt from the one that did the work where possible.
- **Loop guards:** repeated-failure detection, a no-progress detector (the same state seen N times), and budget checks before each step.

## 3. Envelopes

| Field | Default source | Notes |
|---|---|---|
| Goal | User | Restated by the operator for confirmation |
| Done criteria | Operator proposes; user edits | Checkable statements; at least one |
| Capabilities | Playbook or area default | Expressed as grant patterns; shown as chips |
| Budget | Area default | Wall time, spend, action count; hard stops |
| Autonomy tier | Global default (tiered) | Per-run override |
| Deadline | Task due date | Optional |
| Area and context | Task | Cross-area context must be explicit |

An envelope template is a **playbook**. Playbooks replace workflow graphs as the main reusable unit: a goal pattern, envelope, optional plan skeleton, skills and engine preferences.

## 4. Takeover

"Takeover" means the operator works on the user's behalf until done, in the background or on the screen.

| Mode | Where it acts | User presence |
|---|---|---|
| **Background** | Tools, files, APIs, headless browser | Not needed; approvals queue |
| **Screen** | The user's real desktop session | User can watch, preempt, take back ([12](12-computer-use.md)) |
| **Isolated screen** (H2) | A separate desktop session or VM | Not needed; user can view |

## 5. Autonomy tiers

**Default: tiered by action risk** (decision D-05). See [04](04-concept-model.md) §3 for R-classes.

| Class | Tiered (default) | Supervised | Envelope-autonomous |
|---|---|---|---|
| R0 read | Auto | Auto | Auto |
| R1 reversible local | Auto | Auto | Auto |
| R2 reversible external | Auto if capability in envelope | Ask | Auto if in envelope |
| R3 irreversible/outbound | Ask unless a grant covers the exact pattern | Ask | Auto if a run grant was approved with the plan |
| R4 prohibited | Never | Never | Never |

Additional gates that apply in every tier:

- **Taint gate:** if an R2/R3 action's arguments derive from untrusted content (a recipient taken from an email, a URL from a web page), the action needs a covering grant *and* passing intent judgments, or it asks.
- **Confidence gate:** if the commitment behind an R3 action has confidence below the area threshold or recorded dissent, it asks, even with a grant.
- **Budget gate:** at 80% of any budget dimension the run reports; at 100% it stops.

## 6. Cognitive columns

The Thousand Brains model stays. Its role is now precise: **columns are independent reasoning and memory units that keep separate models of the task and vote before commitments.**

| Column (H1 → H2) | Keeps a model of | Votes on |
|---|---|---|
| **Goal** (H1, exists) | What the user wants, done criteria, constraints | Whether a step serves the goal |
| **Evidence** (H1, exists) | What is known, from where, and with what taint | Whether claims are supported; missing evidence |
| **Risk** (H1 new) | Side effects, reversibility, policy context | Whether an action should proceed or ask |
| **Plan** (H2) | Decomposition, dependencies, progress | Next step selection |
| **Verification** (H1 new) | Done criteria checks and step outcomes | Whether the run or step is actually done |
| **Domain** (H2) | Area-specific knowledge (a client, a codebase) | Domain correctness |

- **Assembly:** a coalition of columns configured per playbook, with a consensus policy (weighted support, veto rules) and a stopping rule. The existing `locus/assembly` and `locus/commitment` slices are the starting point.
- **Independence:** columns use separate context and, where the area allows, different engines (for example a local model for Risk, a hosted model for Plan), so failures are less correlated.
- **Commitment:** the fused decision with confidence, supporting and dissenting columns, blockers and next actions. R3 actions reference the commitment that justified them.
- **Cost control:** columns call Laya first. Only disagreement or low confidence escalates to an LLM.
- **Memory link:** each column reads and writes its own slice of working memory, and proposes durable memory in its domain ([14](14-memory-and-knowledge.md) §4).

## 7. Sub-agents

A run may spawn sub-runs for parallel or specialized work (research, coding, document drafting). Sub-runs inherit an **attenuated** envelope: never more capability, budget or context than the parent, and grant tokens are attenuated cryptographically (Biscuit). Results return as evidence to the parent's columns.

## 8. Always-on

The service runs whether or not the UI is open.

| Intake | Behavior |
|---|---|
| Schedules | Cron-style triggers with envelope templates; missed runs follow a catch-up policy (skip, run once, run all) |
| File and folder triggers | Watch granted folders; debounce; create tasks or start runs |
| Work trackers | Native tracker tasks assigned to the agent; Linear/Asana/Jira items labelled `agent:eligible` (generalizing Symphony) |
| Inbox and messaging | Email, Slack, Teams sweeps on schedule; proposals into Triage |
| Peers | Delegation requests into Triage; never auto-accepted unless a standing peer grant exists for that request pattern |

**Concurrency:** a per-area limit on concurrent runs and one screen-mode run at a time machine-wide. Background runs continue during a screen run unless they need the screen.

**Symphony generalized:** the current Linear → Codex pipeline (`WORKFLOW.md` contract, eligibility labels, per-issue workspace) becomes one configuration of the general model: tracker trigger + coding playbook + Codex engine. Its workstation-specific paths are removed (use settings, not hard-coded paths).

## 9. Oversight

- Every run has a live view and a permanent record.
- Agent activity on tasks appears as comments attributed to the agent, linking to the run.
- A daily brief (opt-in) summarizes completed, blocked and pending work by area.
- Standing grants and playbooks used in the past week are listed in the weekly audit (F10).

## 10. The assistant persona

One name, one voice, configurable. It is direct, concise and honest about uncertainty and limits. It is not a companion: no emotional dependency patterns, no flattery, and it disagrees when the plan is wrong.
