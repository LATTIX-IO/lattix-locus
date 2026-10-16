# Locus MVP implementation contract

Version: contract v1.1 (proposed freeze for the MVP vertical slice) · prototype v0.3.1 · 13 Oct 2026
Changes from v1.0: run-state contract and idempotent run creation (2.5a), executable agent definition with evaluation, impact and rollback (Agent), handoff classification and scope (Team), rendering rules for untrusted data (9), planned-capability flags (10), release gates (7).
Status: proposed for freeze. Changes after freeze need a version bump and a migration note.

This document is the hand-off between the design prototype and implementation. The prototype store (`locus-state.js`) mirrors these rules so the UI can be reviewed, but the backend is authoritative for every check below. Where the prototype simplifies something, the section says so.

---

## 1. Conventions for every record

| Field | Type | Rule |
|---|---|---|
| `id` | string | Prefixed and immutable (`TSK-`, `PLN-`, `AGT-`, `TEAM-`, `RUN-`, `STP-`, `PD-`, `GRT-`, `APR-`, `ART-`, `CKP-`, `MEM-`). |
| `tenant` | string | Required on every record. All reads and writes are tenant-scoped on the server. A record can never reference a record in another tenant. |
| `schema` | string | Schema version, for example `task/1`. Readers must reject an unknown major version. |
| `rev` | integer | Optimistic concurrency. Every mutation sends the `rev` it read; a stale `rev` returns `409`. |
| `created_at`, `updated_at` | RFC 3339 UTC | Set by the server only. |
| `created_by` | principal | A person (`usr:`), an agent (`agt:`) or the system (`sys:`). |

State transitions are server-side commands, never field writes. Every accepted transition emits exactly one audit event (section 5) and one stream event (section 6). An invalid transition returns `409` with the current state.

---

## 2. Entities and state machines

### Task · `task/1`
Fields: `title`, `project?`, `owner`, `assignee {type: person|agent|team, id}`, `classification`, `due?`, `deps[]`, `plan?` (latest reviewed plan id), `runs[]`, `source? {conversation|schedule}`.

```
not_started → queued → running ⇄ paused
running → awaiting_approval → running
running → failed → running (retry) | canceled
running → in_review → done | needs_rework
any non-terminal → canceled
needs_rework → queued (new run, re-authorized plan)
```
Terminal: `done`, `canceled`. A task's status is derived from its latest run until it is reviewed.

### Plan · `plan/1`
Fields: `objective`, `template`, `assignee`, `classification`, `route`, `caps[]`, `steps[{title, agent}]`, `budget_usd`, `autonomy`, `policy_bundle`, `reviewed_at?`, `reviewed_by?`, `authorization {blockers[], grants_preview[], floor, estimate_usd}`.

```
draft → authorized (reviewed, zero blockers) → superseded
draft → rejected
```
A run can only start from an `authorized` plan. Editing an authorized plan creates a new `draft`. Authorization checks: classification ≥ project floor; route and engine may receive the classification; each agent's ceiling ≥ classification; each agent is published; per-agent step limits; budget ≤ estimate and ≤ assignee spend limit; every capability is held (section 3).

### Agent · `agent/2`
Fields: `name`, `description` (discovery only; never sent to the model as instructions), `instructions` (executable), `constraints[]` (behavioral), `owner`, `engine`, `route`, `ceiling`, `skills[]`, `caps[]` (requested, not granted), `scopes[]` (readable namespaces), `limits {per_run_usd, max_steps, max_minutes_per_step}`, `memory {reads[], writes[]}`, `version`, `draft?`, `evaluation?`, `versions[{v, snapshot, evaluation, status, note, rolled_from?}]`.

```
draft → published (vN) → draft_changes → published (vN+1)
published (vN) → superseded           (when vN+1 is published)
published → retired
```
The definition hash covers every executable field (instructions, constraints, engine, route, skills, caps, scopes, ceiling, limits, memory). Each published version stores its full snapshot.

**Publish gate.** All of the following must hold, in order:
1. Definition checks: the engine is enabled and its mediation allows the ceiling; the route allows the ceiling; computer-use capabilities only on the native sandbox; repository capabilities only on a coding-engine workspace; instructions present (60+ characters); no empty constraints; at least one scope, none above the ceiling; limits within tenant bounds.
2. Evaluation of this exact definition hash, passing every suite: task regression (threshold 0.80), prompt-injection probes (all refused), capability-boundary probes, scope probes. Any edit invalidates the evaluation.
3. Impact review. Breaking impact requires explicit acknowledgement: team delegations the new version no longer holds, authorized plans that would no longer pass authorization, a lowered ceiling.

**Narrowing.** Takes effect for new runs at publish. Authorized plans that no longer pass return to `draft` and need re-authorization. Teams whose delegated authority exceeds the new definition fail their checks until republished; the delegation intersection already prevents use of the dropped capability.
**Superseding.** Runs pin `agent_versions{}` at authorization and keep that version until they end.
**Rollback.** Publishes a stored snapshot as a new version (`vN+1`, `rolled_from: vK`), re-running definition checks and impact review. It reuses the stored evaluation of `vK`. History is append-only.

### Team · `team/1`
Fields: `name`, `purpose`, `owner`, `members[]`, `lead`, `authority {agent_id: caps[]}`, `handoffs[{from, to, carries}]`, `limits {per_run_usd}`, `version`, `draft?`, `versions[]`.

Same lifecycle as Agent. Publish checks: at least two published members; the lead is a member; `authority[a] ⊆ agent(a).caps`; every member is reachable from the lead through handoffs; no self-handoff.
**Handoffs** are `{from, to, carries, classification, namespace}`. A handoff is valid only if the recipient's ceiling ≥ `classification`, and both sender and recipient hold a read scope on `namespace`. A team with an invalid handoff blocks authorization of any plan assigned to it. At run time the mediation layer re-checks each transferred artifact's classification and namespace before delivery.

**Delegation rule (normative).** Delegation receives an independently authorized subset of capabilities; authority cannot widen transitively through a team or execution engine. Effective capability for member *a* in run *r* = `agent(a).caps ∩ team.authority[a] ∩ policy(r)`. A handoff moves work products, never grants.

### Run · `run/1`
Fields: `task`, `plan` (authorized plan id), `agent_versions{}`, `team_version?`, `node`, `status`, `cursor`, `cost_usd`, `grants[]`, `checkpoints[]`, `error?`, `seq` (last event sequence).

```
queued → running
running → paused → running
running → blocked (approval) → running
running → recovering (worker lost) → running
running → failed → running (retry from checkpoint)
running|paused|blocked|recovering|queued → canceling → canceled
running → completed
```
Terminal: `completed`, `canceled`. `canceling` revokes all grants before the worker acknowledges. Retry is refused if any grant on the run was revoked; a new run needs re-authorization.

### 2.5a Run-state contract (normative for every client)

| Status | Phase | Counts as | Task may start another run |
|---|---|---|---|
| `queued`, `running`, `paused`, `recovering` | active | in progress | No |
| `blocked` | waiting | needs a person | No |
| `canceling` | stopping | in progress | No |
| `failed` | attention | needs recovery | No (retry or stop it) |
| `completed`, `canceled` | terminal | finished | Yes |

Every screen classifies runs through this table. It never keeps its own status lists. **Run creation is idempotent.** `POST /tasks/:id/runs` takes an `Idempotency-Key`. The same key returns the original run. A request while the task has any non-terminal run returns `409` with that run's id. The server enforces both rules regardless of what the client displays.

### Step · `step/1`
Fields: `run`, `index`, `actor` (agent), `kind` (`policy|tool|handoff|assembly|artifact|approval|memory`), `title`, `started_at`, `ended_at?`, `idempotency_key`, `attempt`.
States: `pending → running → succeeded | failed | skipped`. On a retry, the step restarts from the previous checkpoint. Tool calls that already succeeded are not repeated; the idempotency key (`run:step:attempt-independent`) guarantees this.

### PolicyDecision · `policy_decision/1`
Fields: `run?`, `subject` (principal), `action` (capability), `resource`, `context {classification, engine, route}`, `decision` (`permit|deny|approval_required`), `rule`, `bundle` (for example `v41`), `reason`, `decided_at`.
Immutable. Every grant, approval and denied call references the decision that produced it.

### CapabilityGrant · `grant/1`
Fields: `run`, `agent`, `cap`, `resource`, `decision` (PolicyDecision id), `uses` (`unlimited|once|per_use_approval`), `expires_at`.

```
pending → active → used | expired
pending|active → revoked
pending → denied
```
One grant per agent × capability × resource × run. A grant is bound to the run, not the worker; worker loss does not mint new grants. Revocation takes effect at the next call; in-flight calls are refused at the mediation layer.

### Approval · `approval/1`
Fields: `run`, `task`, `step`, `requested_by` (agent), `on_behalf_of` (task owner), `executor {agent, engine, node}`, `action`, `resource`, `egress` (`leaves_tenant|stays_in_tenant|local_only`), `data {summary, items[{artifact, hash}], classification, namespace}`, `grant`, `policy {rule, bundle}`, `step_up_required`, `requested_at`, `expires_at`, `fingerprint`, `decision? {by, at, fingerprint, bundle, session, step_up_method, note}`.

```
pending → approved | denied | expired | withdrawn
expired → (re-requested as a new approval; the old one stays expired)
```
`fingerprint = H(action, resource, classification, namespace, data item ids, bundle, rule, expires_at, agent)`. A decision is accepted only when: the approval is `pending`; `now < expires_at`; the submitted fingerprint matches; the decider has an active tenant session and an approver role; and, if `step_up_required`, a step-up younger than 5 minutes. Denial never requires step-up. Approving consumes the grant once and widens nothing else.

### Artifact · `artifact/1`
Fields: `run`, `task`, `title`, `type`, `version`, `produced_by`, `hash` (SHA-256), `classification`, `namespace`, `size`.
States: `draft → final → superseded`. Content is immutable per version. An approval that releases an artifact names its hash.

### Checkpoint · `checkpoint/1`
Fields: `run`, `after_step`, `cursor`, `state_ref` (opaque storage key), `hash`, `created_at`, `retention_until`.
Immutable. Written after each completed `tool`, `artifact`, `assembly` or `handoff` step. Retained for 30 days after the run ends.

### MemoryRecord · `memory/1`
Fields: `text`, `scope` (`run|session|user|tenant|agent|workflow|global`), `namespace` (projects use `tenant` + `projects/<name>`), `source`, `trust` (`unverified|verified|disputed|superseded|rejected|forgotten`), `confidence`, `retention`, `provenance[]`.

```
proposed → accepted (written) | rejected
unverified → verified | disputed → superseded | rejected
any → forgotten (removed from retrieval; purged after a 7-day hold)
```
Writes outside `run` scope only happen through outcome review. A rejected deliverable writes no memory.

---

## 3. Identity and session

| Fact | Issued by | Lifetime | Grants |
|---|---|---|---|
| Verified local identity | OS authenticator (Windows Hello, Touch ID, passkey) | Device unlock | Unlocks local keys and the local runtime. Nothing in a tenant. |
| Tenant session | Tenant IdP via OIDC + PKCE, or a tenant passkey | 8 h absolute, 30 min idle, revocable | The principal every API call is checked against. |
| Step-up | Fresh user verification inside an active session | 5 min, decisions only | Allows `approve` on approvals with `step_up_required`. |

Reauthentication: on idle timeout, expiry or administrator revocation, the client must open a new tenant session. Runs keep their own grants and continue; decisions and new work wait. The desktop client follows the same rule: device sign-in never substitutes for a tenant session.

---

## 4. Consequential operations

Every operation below is a server command. *Owner* may request it; *executor* is the identity that acts; the decision is recorded against the policy bundle in force.

| Operation | Owner | Executor | Resources | Policy | Expiry | Audit event |
|---|---|---|---|---|---|---|
| Authorize plan | Task owner | Policy engine | Plan, agents, team, route, project | bundle at review | until superseded | `plan.authorized` |
| Start run | Task owner | Orchestrator on a node | Authorized plan | bundle pinned on plan | run lifetime | `run.started` |
| Mint grants | System | Policy engine | Agent × cap × resource | bundle pinned on run | run end | `grant.minted` |
| Approve / deny | Task owner (or delegated approver) with step-up when required | Requesting agent, once | Approval resource and data items | bundle on the approval | `expires_at` (30 min) | `approval.decided` |
| Revoke grant | Task owner, security admin | Mediation layer | Grant | n/a | immediate | `grant.revoked` |
| Stop run | Task owner, security admin | Orchestrator, then worker ack | Run, all grants | n/a | immediate revocation | `run.cancel_requested`, `run.canceled` |
| Retry from checkpoint | Task owner | Orchestrator | Run, last checkpoint | bundle pinned on run | run lifetime | `run.retried` |
| Steer | Task owner | Orchestrator at a step boundary | Run | cannot widen grants | next step | `run.steered` |
| Publish agent / team | Definition owner | Registry | Definition version | publish checks + bundle | until next version | `agent.published`, `team.published` |
| Close outcome | Task owner | System | Run review, memory proposals | n/a | n/a | `outcome.reviewed`, `memory.written` |
| Forget memory | Record owner, tenant admin | Memory service | Record | retention policy | 7-day purge hold | `memory.forgotten` |
| Sign in, step-up, sign out | Person | IdP and session service | Tenant session | tenant auth policy | section 3 | `session.opened`, `session.stepped_up`, `session.closed` |

---

## 5. Audit events

Immutable, append-only, tenant-scoped. Fields: `id`, `tenant`, `type`, `actor`, `on_behalf_of?`, `subject` (record id + rev), `policy_bundle?`, `decision?`, `fingerprint?`, `session?`, `at`, `hash_prev` (hash chain). An audit event is written in the same transaction as the state change it records.

---

## 6. Run event stream

The server is authoritative. The client never infers state it has not received.

- **Transport:** Server-Sent Events per run (`/runs/:id/events`) and per tenant inbox (`/inbox/events`).
- **Ordering:** every event carries `seq`, strictly increasing per run with no gaps on the server.
- **Delivery:** at-least-once. The client applies an event only if `seq = last_applied + 1`. It ignores `seq ≤ last_applied` as a duplicate. If it sees `seq > last_applied + 1` (a gap), it refetches the run snapshot and resumes from the snapshot's `seq`.
- **Reconnect:** the client sends `Last-Event-ID: <last_applied>`. The server replays from that sequence inclusively. Backoff is 1, 2, 4 s, then every 8 s with jitter.
- **Staleness:** the server sends a heartbeat every 15 s. After 2 missed heartbeats the client marks the view stale, shows the time of the last applied event, and disables run controls until it is current.
- **Commands:** every command carries an idempotency key and the `rev` it was based on. The command response is not the source of truth; the resulting stream event is.
- **Event types:** `run.state`, `step.started`, `step.finished`, `policy.decision`, `grant.state`, `approval.requested`, `approval.decided`, `approval.expired`, `message`, `steer.applied`, `artifact.created`, `checkpoint.written`, `worker.lost`, `worker.resumed`, `run.cancel_requested`, `run.canceled`, `heartbeat`.

Prototype note: the prototype simulates a single stream from local state. Its run console implements the stale view, backoff, inclusive replay and duplicate counting so the behaviour can be reviewed.

---

## 7. Release acceptance criteria (not yet verified)

| Criterion | Status |
|---|---|
| Home → plan → run → approval → outcome → memory scenario against real services | Not verified |
| Unauthorized access is refused for every route and command | Not verified |
| Cross-tenant isolation: no read, write, reference or stream crosses tenants | Not verified |
| Privilege inheritance: no widening through teams, engines, handoffs or A2A | Not verified |
| Prompt injection from documents, web content and tool output cannot trigger unapproved consequential actions | Not verified |
| Cancellation and revocation take effect for in-flight calls | Not verified |
| Budget exhaustion stops spend at the cap | Not verified |
| Interrupted execution resumes from checkpoints without repeating side effects | Not verified |
| Real-browser QA at desktop, tablet and mobile, light and dark, keyboard and assistive technology | Partly verified in the prototype: headless Chrome reflow at 320 to 1280 px, keyboard focus, dialog tests, and an accessibility-tree audit of MVP paths. No screen-reader pass yet |

### Gate A · design baseline (prototype v0.3.1)
Unified run states, complete agent definition, handoff classification checks, untrusted-data rendering tests on every screen, planned controls marked, registry and export synchronized. Status: addressed in the prototype, apart from the screen-reader pass.

### Gate B · implementation qualification (required before a customer-facing MVP)
| Area | Evidence required |
|---|---|
| Identity and authorization | Server-issued session, protected authentication, step-up, tenant isolation, policy-authoritative approvals |
| Multi-agent orchestration | At least two agents complete a real delegated task with persisted state and governed handoffs |
| Long-running execution | A run continues after the UI closes; checkpoint recovery, reconnection, retries and cancellation work |
| External capabilities | A working MCP integration, a governed skill, and the advertised browser or computer-use capability |
| Security controls | Per-action mediation, bounded grants, revocation, prompt-injection defenses, auditable denials, zero unauthorized tool execution |
| Artifacts and memory | Durable output, integrity and provenance metadata, scoped retrieval, accepted memory writes, access-controlled deletion |
| Release quality | Real-service integration tests, negative security tests, accessibility checks, rollback and deployment validation |

---

## 8. Where the prototype implements each rule

| Rule | Prototype |
|---|---|
| Plan authorization | `authorize()`, `grantSet()` in `locus-state.js` |
| Delegation subset | `grantSet()` team branch; `validateTeam()` |
| Agent publish checks | `validateAgent()`, `A.publishAgent()` |
| Approval binding | `requestApproval()`, `fingerprint()`, `A.decide()` |
| Session and step-up | `S.session`, `A.signIn()`, `A.stepUp()`, `sessionState()` |
| Recovery | `A.simulate()`, `A.cancel()`, `A.revokeGrant()`, `A.rerequest()`, the stream object in `activity-run.html` |
| Run-state contract, idempotency | `RUN_STATES`, `liveRunOf()`, `A.startRun(task, key)`, `A.launch()` |
| Agent evaluation, impact, rollback | `evaluateDef()`, `publishImpact()`, `A.evaluateAgent()`, `A.publishAgent(id, note, ack)`, `A.rollbackAgent()` |
| Handoff checks | `validateTeam()` handoff branch; `authorize()` team check |
| Safe rendering | `H.esc`, `H.enc`, `H.linkTo` |
| Acceptance tests | `prototype-tests.html` T1–T20 |

---

## 9. Rendering untrusted data (all clients)

Every value from the API is untrusted, including identifiers. Text and attribute values are HTML-escaped. URL components are percent-encoded. Links built from stored references use a whitelisted type and an id that matches `^[A-Z]{2,5}-[A-Za-z0-9-]{1,24}$`; anything else renders as plain text. Toasts and notifications are plain text. A regression test poisons every free-text field, plus hostile ids and link types, across every screen.

## 10. Planned capabilities and feature flags

A control whose operation is not implemented never reports success. In demos it is marked Planned, exposed as `aria-disabled`, and explains what it would do. In a live MVP build (`hidePlanned`) it is not rendered. Capabilities advertised as live must execute, and their controls must operate outside the browser.
