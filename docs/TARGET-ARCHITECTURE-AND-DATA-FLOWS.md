# Lattix Locus: Target System Architecture and Data Flows

Status: **Target-state reference architecture, proposed for review (2026-10-22).** This document describes the intended end state and migration boundaries, not a statement that every component has shipped. Base observed: main at \`2b93898238f7e4e0e7456367b9bc323ae31f8d82\` (LOCUS-471).

**Authority and precedence.** Product intent follows [product README](product/README.md), [vision](product/01-vision-and-positioning.md), [concept model](product/04-concept-model.md), [inference and engines](product/10-inference-and-model-routing.md), [security](product/13-security-architecture.md), [memory](product/14-memory-and-knowledge.md), [extensions](product/15-skills-mcp-and-extensions.md), [federation](product/18-federation-and-collaboration.md), and [roadmap](product/20-roadmap-and-decisions.md). Engineering invariants follow [AGENTS](../AGENTS.md), [threat model](../THREAT-MODEL.md), and [ADR-0001](adr/ADR-0001-cedar-authorization-core.md), [ADR-0002](adr/ADR-0002-general-purpose-meta-harness.md), [ADR-0003](adr/ADR-0003-organization-compute-fabric.md). This document consolidates them; it does not silently amend an accepted decision. Contradictions and new approvals required appear in §16.

## 1. System mission, limits and invariants

Locus is a **local-first, Zero Trust, general-purpose multi-agent meta-harness**. The per-user instance owns task state, planning admission, authorization, run orchestration, memory, artifacts and audit. Ollama and API providers supply *inference*; native Locus, Codex CLI, Claude Code and OpenCode supply *agent execution loops*. A persistent user-facing assistant is compatible with ephemeral specialist processes.

**Scope:** software engineering, research, analysis, document/spreadsheet/presentation/image creation, correspondence, business process automation, authorized computer use, knowledge ingestion, local and external integrations, and eventually signed peer-to-peer collaboration. The same action and artifact contracts apply to all work domains.

**Non-goals for the primary profile:** a mandatory cloud control plane; multi-user SaaS tenancy; an independent privileged planner LLM; wholesale replication of vendor engines; default federation; always-on privileged specialist personas; optional Savant as a requirement; or treating a transport connection as permission to access data.

**Non-negotiable invariants:**
1. An LLM or retrieved document may **propose**, never **grant**, policy, capability, identity, plan authority or outcome acceptance.
2. Every agent-originated model call, tool action, filesystem mutation, network connection, MCP call, computer action and external side effect must be mediated by the Locus Gateway/PEP **and** an adequate OS isolation boundary. An unmediated path is disabled, not counted as secured.
3. A child pod receives a strictly **attenuated** subset of its parent run envelope and an explicit memory/data projection; no ambient home directory, credential store, original-workspace writable mount or sibling authority.
4. Work state survives process exits; leases, budgets, action receipts, dependency versions and cancellation are durable or reconstructed from independently authenticated evidence.
5. Artifact completion is independently established. Produced files are not automatically copied to a principal workspace, sent to a user, pushed to a remote repository or shared outside the run.
6. Local execution functions with no provider account, organization service, network, fleet telemetry or peer. Connected capabilities are optional and may be unavailable.
7. The default identity model is **one human principal per local instance** (D-03). Areas separate personal, professional, business and client contexts. Shared spaces and organization compute are explicitly opt-in additions.
8. R4 actions are not delegable. R3 actions need a policy-compliant narrow standing authorization or explicit authenticated human approval, and some protected actions require a human decision on every attempt.

## 2. Logical architecture

### 2.1 Local instance and optional external boundaries

~~~mermaid
flowchart TB
    Human["Human principal"]
    Surface["Tauri desktop / Next.js UI / CLI / API"]
    Intake["Task intake + native tracker + triggers"]
    Supervisor["Durable Task Orchestrator\nIntent / plan / DAG / scheduler / leases"]
    Composer["Pod composer + engine/router registry"]
    Gateway["Single Action Gateway / PEP\nrisk + taint + grant + policy + receipts"]
    Trust["Trust kernel\nidentity / Cedar PDP / Biscuit / secrets / audit"]
    Native["Native Locus agent runtime"]
    External["Official CLI agents\nCodex / Claude Code / OpenCode"]
    Sandboxes["OS sandbox + stage broker + egress proxy"]
    Models["Model providers\nOllama / local compatible / optional hosted"]
    Tools["MCP host/client / native tools / connector adapters"]
    Knowledge["Knowledge, RAG and cognitive memory"]
    State["Durable local state + audit log"]
    Artifacts["Staged artifacts + escrow + verified store"]
    Helper["Signed native computer-use helper"]
    Peer["Optional P2P peers / A2A adapter"]
    Fabric["Proposed opt-in org compute fabric"]

    Human --> Surface --> Intake --> Supervisor
    Supervisor --> Composer
    Composer --> Native
    Composer --> External
    Native --> Gateway
    External --> Gateway
    Supervisor --> Gateway
    Gateway --> Trust
    Gateway --> Sandboxes
    Sandboxes --> Tools
    Gateway --> Models
    Gateway --> Helper
    Supervisor <--> Knowledge
    Supervisor <--> State
    Sandboxes --> Artifacts
    Artifacts --> Supervisor
    Peer <--> Gateway
    Fabric -. "not approved for remote execution" .-> Peer
~~~

**Diagram interpretation.** Arrows from an executor to Gateway represent *requested authority*, not direct permission to reach the final resource. All access to resources must be routed by approved brokers/proxies. Supervisor-to-State and Supervisor-to-Knowledge are trusted control-plane operations subject to authenticated service and area checks; they are not an exemption allowing arbitrary agent instructions to mutate them.

### 2.2 Trust-zone model

| Zone | Authority and contents | Enforcement |
| --- | --- | --- |
| Z0: Trust kernel | Principal/device/run identities, Cedar policy engine, grants, secrets broker, audit and approval issuer | Isolated code paths; signed releases; agent changes prohibited |
| Z1: Local control plane | Authenticated API, tracker, task planner, scheduler, checkpoints, queues, memory projections, UX | Local session security, area ACLs, deterministic validation, signed action receipts |
| Z2: Gateway / PEP | All agent model, tool, MCP, egress, filesystem and computer-use actions | One audited authorization decision per action, budget/taint checks |
| Z3: Untrusted execution | Native model-driven loops, vendor CLI engines, skills, MCP stdio servers, ephemeral pods | Container/AppContainer/seatbelt/bwrap as applicable, staged filesystem and denied ambient egress |
| Z4: User computer | Original source workspaces, user browser profiles, signed native desktop helper | Scoped OS permission, user-presence/approval, snapshot and promotion broker |
| Z5: Data and external APIs | Local knowledge/artifacts, Ollama, hosted models, internet, Microsoft Graph, Linear, GitHub, other connectors | Area classification ceilings, audience-scoped OAuth, egress allowlist, DLP, content taint |
| Z6: Peer/federation | Other human-operated Locus instances and optional organization compute nodes | Explicit pairing, mutual identity proofs, recipient-side authorization; off by default |

Never equate loopback with trusted. The local gateway is bound to loopback where practical, but authenticates local UI, helper and worker callers. Separately sandboxed processes cannot bypass it by talking directly to Ollama, the public network, the desktop helper, the original filesystem or arbitrary MCP servers.

## 3. Module boundaries and ownership

Use **versioned ports** and a single profile-driven composition root per D-28. This is a modular Python/FastAPI backend plus signed native platform components, not a deployment of one microservice per module.

| Module | Responsibilities / source of truth | Candidate home or port |
| --- | --- | --- |
| Operator surfaces | Chat, task tracker, plan review, approvals, run timeline, memory, sources, artifacts, posture | \`apps/frontend\`, \`apps/desktop-tauri\`, \`locus_tooling\`; SurfaceAdapter |
| Identity and sessions | Principal and device key enrollment, local auth, session issuance, optional OIDC membership attestation | \`apps/backend\`; SessionService / PrincipalStore |
| Task intake and tracker | Tasks, areas, projects, initiatives, trigger ingestion, sync ownership | \`apps/backend\`; TaskIntake / Tracker / Trigger |
| Task Orchestrator | TaskIntent admission; versioned TaskPlan DAG; dependency readiness; durable supervisor and recoverable scheduler | \`locus_runtime/task_graph_contract.py\`, \`task_plan_compiler.py\`, \`task_scheduler.py\`, \`task_dispatch_driver.py\`; TaskOrchestrator |
| Pod composer | Capability-fit ephemeral specialists, assignment, per-pod memory projection, budget and lease | PodComposer (LOCUS-412) |
| Execution | Native loop, external engine adapters, cancellation, normalized events | \`locus_runtime/harness\`, \`apps/workers\`; AgentRuntime / AgentEngineAdapter |
| Model and judgment | T0 code, T1 Laya, T2/T3 Ollama or APIs, compatible model feature discovery and per-area routing | \`model_client.py\`, ModelProvider / ModelRouter / GuardrailClassifier |
| Gateway / trust kernel | PolicyEngine (OPA authoritative during shadow migration to Cedar), Biscuit grants, taint, action receipts and rate/budget fences | \`locus_runtime/gateway.py\`, \`policy_engine.py\`, \`grants.py\`; PolicyEngine / GrantIssuer / ActionBroker |
| Tool/connectors | Built-in tools, MCP host/client/server, API connectors, computer-use adapter, egress | ToolProvider / ConnectionStore / BrowserDriver / DesktopDriver |
| Sandbox / artifact escrow | StageLease, staged snapshots, guardian, OS sandbox, verifier input freeze, immutable escrow, approval-bound promotion | \`locus_runtime/pod_stage_*.py\`, \`pod_artifact_*.py\`; Sandbox / ArtifactVerifier / ArtifactStore |
| Knowledge and memory | Sources, parsing, chunks, FTS, embeddings, graph projections, governed durable memory and cortical columns | \`locus_runtime/memory\`, \`cognitive.py\`; Parser / Retriever / ColumnStore |
| Persistence and event spine | Module-owned schemas, run checkpoints, idempotency, transaction/outbox, hash-chain audit, versioning | StateStore / Checkpointer / AuditLog / EventBus |
| Evals, posture and telemetry | Local OTel, domain rubrics, release acceptance, installed-control evidence, optional consented export | \`locus_runtime/telemetry\`; EvalSuite / TelemetrySink |
| Optional peer/fabric | Peer cards and space sync, cross-peer delegation; proposed organization resource leasing | PeerTransport / RemoteExecutor ports; separate approval gate |

Contract interfaces are stable; third-party libraries sit behind replaceable adapters. Agent-runtime or tool swaps occur at run boundaries; trust-kernel swaps require a principal-reviewed signed release.

## 4. Canonical entities and contracts

A **Task** is user-visible work; a **Run** is one attempt to perform it; a **TaskPlan** decomposes it; a **PodAttempt** executes a specific node; an **Action** is one potentially sensitive operation. No model-generated payload is proof that any one of these has been authorized or completed.

| Contract | Required fields / characteristics |
| --- | --- |
| Principal / Instance / Area | Stable IDs, principal and device public keys, issuer and revocation state, area policy and data ceiling |
| TaskIntent | Source identity and taint, immutable area, goal, acceptance rubric, requested output types, bounded parent envelope |
| RunEnvelope | Granted capability set, path/host/tool scopes, classification ceiling, per-action risk policy, engine limits, wall-time/tokens/cost/step budget, expiry, approval modes |
| TaskPlan | Plan revision and digest, typed DAG node IDs and dependencies, output schema, deterministic resource bounds, source policy/catalog versions, status |
| PodSpec | Task role, allowed engine/version, memory projection, attenuated grant, staged input and output allowlists, deadline, resource reservation |
| PodAttempt / StageLease | Parent/child identity, exact source revision, engine pin, lease epoch, OS guardian witness, checkpoint, cancellation state |
| ActionRequest | Run/attempt/action/idempotency IDs, authenticated subject, resource, canonical argument hash, taint, inferred R-class and relevant grant |
| VerificationVerdict | Independent verifier ID and version, rubric, immutable input artifact hashes, deterministic checks, dissent, pass/revise/block |
| ArtifactRef | Stable ID, SHA-256, media type, owner/area, classification and taint, producer attempt, source/provenance links, stage/escrow state and verified revision |
| ApprovalReceipt | Authenticated principal, exact proposed operation and resource, frozen hashes and policy epoch, expiry, nonce and one-use consumption state |
| AuditEvent | Monotonic sequence, actor, area, run/pod/action IDs, event type, trace/causation IDs, content-safe details, previous hash, current hash, signature/checkpoint |

Use canonical JSON/Pydantic contracts, schema versions, unknown-field rejection at authority boundaries, and explicit error/status taxonomies. Plans and artifact references are content-versioned; never silently retarget approvals to a new revision. A provenance citation or a model's reported success does not constitute authorization or independent verification.

## 5. Identity, authentication and authorization

### 5.1 Identities are separate, not aliases

| Identity | Authentication | Permitted authority |
| --- | --- | --- |
| Human principal | OS-bound signed desktop login or locally enrolled passkey; short authenticated API session | Authorize own tasks, issue bounded grants, review R3 actions, administer security after step-up |
| Local installation / device | Device key stored in OS protected store | Sign device, audit and peer messages; never grant principal-level access by itself |
| Run / pod / skill process | Fresh execution identity plus short-lived audience-scoped signed grant/lease | Only envelope-derived scope, budget and time window |
| User's external account | OAuth 2.x consent with PKCE or vendor-supported device/interactive flow; token in Keychain/DPAPI | Only consented API scopes through Gateway and per-area connector policy |
| Official vendor CLI | Vendor's own sanctioned login flow or API credential, under version-pinned engine adapter | Use vendor service only; Locus never reads, extracts or proxies subscription credentials |
| Peer principal/device | Verified signed peer card and per-peer trust record; encrypted authenticated transport | Pairing proves identity, not shared-data rights or delegated execution rights |
| Organization membership | Optional signed enterprise/OIDC membership attestation | Attests affiliation only; not runtime task or data permission |

For localhost and the browser client: bind the API to loopback, validate origin/CSRF and session cookies (HttpOnly, SameSite and Secure where transport permits), prohibit unsigned caller-controlled identity headers, use explicit session expiry/revocation, and require native confirmation or passkey step-up for high-impact human approvals. Local lightweight unauthenticated mode must never silently become the desktop production mode. No default Keycloak/Casdoor server is required by the accepted single-principal design.

### 5.2 Delegated authority

For an action A initiated by pod P:

\`Allowed(A) = authenticated(P) AND valid_lease(P) AND grant_subset(P,parent) AND policy_permit(A) AND within_budget(A) AND data_and_taint_pass(A) AND approved_if_required(A)\`.

- **PDP target:** Cedar schema-validated, deny by default, explicit forbids overriding permits; domain-by-domain migration behind PolicyEngine while OPA remains authoritative until parity (ADR-0001).
- **Grant carrier target:** Biscuit capability tokens, attenuated from run to pod to individual tool invocation. Token verification is distinct from a policy decision and never a substitute for per-action Gateway enforcement.
- **Risk:** R0 read-only; R1 reversible local; R2 reversible broader/external; R3 irreversible/outbound or sensitive; R4 prohibited for agent delegation. Payments and account-security changes always require direct human involvement, even in a permissive browser tier.
- **Data classification and taint:** Area policy decides whether private data may reach an engine/provider/connector. Web pages, source documents, external tool output, model output, screenshot text, skill metadata and peer messages are untrusted evidence; they cannot issue instructions with new authority.
- **Secrets:** Model-visible text receives secret references, never raw values. Only the Gateway secret broker retrieves the credential and injects it directly into an authorized call; logs/artifacts receive redacted fields.
- **Human approval:** A plan/action preview shows scope, target, potential cost, actual diff or outgoing content, data destination and undo path. A time-bounded one-use authenticated receipt covers the exact immutable operation; changes invalidate it.

### 5.3 Authentication-to-action sequence

~~~mermaid
sequenceDiagram
    actor User as Human principal
    participant UI as Tauri / UI
    participant API as Local authenticated API
    participant Identity as Local identity / keychain
    participant Orchestrator as Task supervisor
    participant Gateway as Action Gateway
    participant PDP as Cedar / OPA policy port
    participant Broker as Tool / egress / file broker
    User->>UI: Start session
    UI->>Identity: OS-bound or passkey proof
    Identity-->>API: Verified principal and device
    API-->>UI: Scoped session
    User->>UI: Approve task envelope
    UI->>Orchestrator: Authenticated task intent
    Orchestrator->>Identity: Issue bounded run and pod grants
    Orchestrator-->>Gateway: Authorized run context
    Gateway->>Gateway: Verify subject, lease, grant, risk, taint, budgets
    Gateway->>PDP: Canonical Cedar authorization input
    PDP-->>Gateway: Permit / forbid / ask
    alt One-use human approval required
       Gateway-->>UI: Preview exact action
       User->>UI: Step-up and approve once
       UI-->>Gateway: Signed, version-bound approval receipt
    end
    Gateway->>Broker: Execute approved bounded action
    Broker-->>Gateway: Result and undo/provenance evidence
    Gateway-->>Orchestrator: Audited result or denial
~~~

## 6. End-to-end task, agent and artifact orchestration

### 6.1 Task lifecycle and status

\`Intake -> Normalize -> PlanProposal -> Validate -> Preview/Review -> Admit -> Queue -> Lease -> Execute -> Verify -> Escrow -> Promote/Publish (as separately authorized) -> Synthesize -> Complete\`.

Exception paths: \`InputRequired\`, \`ApprovalRequired\`, \`Blocked\`, \`RetryEligible\`, \`CancelRequested\`, \`Canceled\`, \`RecoveryUncertain\`, \`Failed\`. Terminal success requires accepted rubric evidence, not merely an exit code or a model response.

**Planning:** T0 validation is authoritative. An LLM proposes task decomposition, experts, plan revisions and repair; the compiler validates DAG acyclicity, compatible data areas, risk ceilings, budget allocations, explicit artifact contracts and independent verifier roles. Plan revision, policy epoch and engine registry digest are pinned before launch. Launch is idempotent across restarts.

**Execution:** The scheduler releases independent DAG branches concurrently only after durable reservation/admission. Each pod receives a stage snapshot, nonce/lease, explicit resource reservations, a grant with narrowed scopes, a memory projection and a selected engine. Linux Docker stage-only execution is currently the evidenced hard boundary; equivalent Windows/macOS behavior must be tested before parity claims.

**Handoffs:** An upstream node releases a downstream node only after an independently verified, immutable ArtifactRef matching the prerequisite version. Upstream changes invalidate dependent consumption; repairs are new bounded attempts. A producer may not self-certify accepted status.

### 6.2 General-purpose multi-specialist flow

~~~mermaid
sequenceDiagram
    actor User as Principal
    participant Intake as Task / Chat intake
    participant Plan as Orchestrator / DAG compiler
    participant Runtime as Pod composer / scheduler
    participant Worker1 as Specialist A
    participant Worker2 as Specialist B
    participant Verify as Independent verifier
    participant Store as Escrow / ArtifactStore
    User->>Intake: Research and produce a signed-off brief
    Intake->>Plan: TaskIntent + area, envelope, rubric
    Plan->>Plan: Propose then deterministically validate DAG
    Plan-->>User: Version-pinned plan and expected artifacts
    User->>Plan: Review envelope / approve required scope
    Plan->>Runtime: Durable launch with idempotency key
    par Independent approved work
       Runtime->>Worker1: Lease scoped research pod
       Worker1-->>Store: Cited evidence and source hashes
    and Other independent work
       Runtime->>Worker2: Lease scoped analysis pod
       Worker2-->>Store: Data table, provenance and calculations
    end
    Store->>Verify: Frozen predecessor ArtifactRefs
    Verify-->>Runtime: Rubric verdict and uncertainty
    alt Accepted artifacts
       Runtime->>Worker2: Release downstream composition step
       Worker2-->>Store: Drafted brief and render checks
       Store->>Verify: Review final artifact
       Verify-->>Plan: Accepted artifact IDs
       Plan-->>User: Final outcome, source links, costs and audit
    else Failed or stale
       Verify-->>Plan: Structured blocker / revision request
       Plan->>Runtime: Bounded repair or human escalation
    end
~~~

**Engine routing:** The Task Orchestrator selects *what work* runs and when; ModelRouter selects *which inference or agent engine* may perform a node given capabilities, classification, availability and budget. A model API's OpenAI-compatible transport does not imply tool, multimodal or native agent-engine parity. The local model path is the offline baseline.

**Verification:** Validate according to output type. Code gets format/type/unit/integration tests and diff review; data gets formula, provenance and reproducibility checks; research gets source verification and contradiction checks; documents get structural and rendered-output inspection; business actions get state-readback and idempotency evidence; images get actual output inspection, format validation and provenance. The verifier's conclusion is also untrusted until tied to recorded evidence and policy.

## 7. Tool, model, MCP and computer-use flows

### 7.1 Action mediation pipeline

\`Engine/Native Agent -> Gateway authentication -> Canonicalize action -> Risk/Taint/DLP -> Grant check -> Cedar/OPA PDP -> Budget/reservation -> Approval if required -> Broker -> Evidence/Audit -> Redacted result\`.

The Gateway must broker **both** local/hosted inference and executable tools. Native code-path integration and external CLI tool bridging are two adapters to the same ActionRequest contract; direct vendor CLI built-in shell, web, file or provider network access must be confined or explicitly disabled. "Gateway-only" is not a security claim until denied-path and OS/network isolation tests prove it.

### 7.2 MCP: two directions, one authority

~~~mermaid
flowchart LR
    Native["Native Locus / vendor CLI engines"]
    Server["Locus MCP server facade"]
    PEP["Gateway / policy / capability checks"]
    Client["Locus MCP client host"]
    Local["Sandboxed MCP stdio child"]
    Remote["Authorized remote MCP HTTP server"]
    Service["First-party app connectors"]
    Native --> Server --> PEP
    PEP --> Client
    PEP --> Service
    Client --> Local
    Client --> Remote
~~~

- **Locus as MCP client:** Discover tools/resources; validate protocol versions; sandbox stdio servers; authenticate Streamable HTTP servers with scoped OAuth; pin tool name, description and input/output schema digests; suspend changed or revoked tools until reviewed; validate all calls/results and tag results as tainted.
- **Locus as MCP server:** Offer a narrow external tool surface (e.g., task creation/search, memory search, propose delegation) behind authenticated client pairing, per-area grants and Gateway admission. Exposing Locus through MCP does **not** grant access to original workspaces or authorize R3 actions.
- **Protocol compatibility:** Target the MCP **2026-07-28** stateless core where supported, including request-scoped identity, header/body consistency, version negotiation, discovery and HTTP OAuth hardening; retain an explicitly tested compatibility adapter for older 2025-era clients/servers if needed. Do not bake old \`initialize\` or hidden \`Mcp-Session-Id\` assumptions into authorization or business state. Long-running work uses a durable Locus task and optional MCP tasks-extension adapter.
- **Authorization model:** MCP OAuth identifies a user/client *to the external server*. It does not authorize Locus's internal area/action grant, which is checked separately. Token audience, resource and authorization-server issuer are pinned.

### 7.3 Browser, desktop and native tools

The signed helper runs on the principal's OS session through authenticated local IPC, with separate user-presence and sensitive-screen controls. Browser profile access has explicit Strict-to-Open autonomy policy; the non-removable floor includes panic/cancel, Gateway enforcement, audit, no raw secret capture, and human confirmation for protected actions. Browser tools cannot bypass Gateway by using a direct debugging endpoint or hidden vendor CLI tool. Native filesystem tools target only staged, canonicalized, no-symlink-escape paths.

### 7.4 Engine catalog and compatibility

| Kind | Example | Default transport / containment |
| --- | --- | --- |
| Native inference | Ollama, compatible local server | Loopback-only, model call broker, engine feature tests and data-ceiling enforcement |
| Hosted inference | OpenAI, Anthropic, Azure OpenAI, Gemini, Mistral | Connector-scoped credentials, policy-filtered data, egress and metering |
| Native agent engine | Locus AgentRuntime (Deep Agents current default, verified loop fallback) | Gateway-integrated tool dispatch and reproducible run checkpointing |
| Official CLI engine | Codex CLI, Claude Code, OpenCode | Pinned executable/version, isolated config, staged workspace, Locus MCP Gateway bridge, sandbox/egress, normalized events |
| Context provider | M365 Copilot retrieval/chat/meeting insights | Read-only evidence; Microsoft Graph handles mutations as separately authorized tools |
| Typed judgment | Laya local, optional Jev | Classifier output is advisory/blocking/escalation input, never independent grant |

External providers and plugins fail explicitly on absent credentials, untested features, unsupported CLI versions or denied destinations; there is no simulated successful output.

## 8. Knowledge ingestion, RAG, memory and cognitive assembly

Separate **retrievable knowledge** from **authoritative work state**, and separate both from **durable memory**. Retrieval may change an answer; it must not change the executing lease, grant, source-of-truth task state or approved plan.

### 8.1 Ingestion and grounded retrieval

~~~mermaid
flowchart TB
    Sources["Files / folders / connectors / task artifacts / boards"]
    Acquire["Consent and authenticated ingestion"]
    Guard["Type/size/malware screening\nclassification + taint + source hash"]
    Parse["Sandboxed extraction\nDocling / MarkItDown / modality processors"]
    Chunk["Structured chunks + citations + provenance"]
    Index["FTS5 + optional vector index + derived graph"]
    Query["Authenticated query + area scope + data ceiling"]
    Hybrid["Hybrid search, freshness, rerank, graph evidence"]
    Pack["Least-privilege context projection"]
    Agents["Ephemeral pod / cognitive columns"]
    Memory["Reviewable memory proposals and source lineage"]
    Sources --> Acquire --> Guard --> Parse --> Chunk --> Index
    Query --> Hybrid
    Index --> Hybrid --> Pack --> Agents
    Agents -. "proposals, never direct authoritative writes" .-> Memory
    Memory --> Index
~~~

**Document flow:** Capture an immutable source revision (hash, owner, area, classification, origin, consent). Quarantine and extract in a sandbox; parse supported text, office files, PDFs, images and other media through explicit modality-capable processors. Unsupported types remain indexed by metadata only and report degraded extraction rather than fabricating text. Chunks preserve page/section/cell/time offsets for verifiable citations. Build lexical FTS and (if locally available and approved) embeddings and typed entity/relationship projections. Every derived index retains source lineage for deletion/supersession.

**Retrieval flow:** Authenticate principal/pod/area and filter *before* vector or lexical results are exposed. Apply time/version, classification, space ACL and engine disclosure ceilings. Return a bounded context pack with source IDs, snippets, citation locations, taint and confidence. A compromised document cannot instruct a tool, create a grant or override system policy. Fail with explicit lexical-only degradation if embeddings/graph are down.

**Memory flow:** Working context expires at pod completion; durable episodic, semantic, procedural, decision, preference and project memory enters through a reviewable proposal. Contradictions supersede, with provenance; forget removes derived chunks/embeddings/graph edges under the retention policy. Columns independently retrieve relevant evidence and express confidence/dissent. A consensus commitment informs the plan or human review, not a bypass of Gateway authorization.

**Storage target:** Local durable transactional source of truth, module-owned schemas and WAL/checkpoints. A simplified single-user SQLite/FTS5 profile is the preferred convergence target under the current module-memory plan; optional pgvector/Postgres and graph adapters may serve developer/full-stack profiles. The exact migration off in-memory control-plane snapshots and old embedded Postgres claims requires dedicated ADR and rollback tests; the architecture does not declare an unapproved automatic database migration.

## 9. Artifact production, escrow, review and publication

### 9.1 Artifact state model

\`Candidate -> Staged -> Sealed -> IndependentlyVerified -> Reviewable -> Approved -> Promoted/Published\`, with \`Rejected\`, \`Expired\`, \`Quarantined\` and \`RecoveryUncertain\` alternatives. **Promoted** (local workspace) and **Published** (external irreversible destination) are separate operations.

~~~mermaid
sequenceDiagram
    participant Pod as Untrusted child pod
    participant Stage as Stage-only filesystem
    participant Supervisor as Authenticated supervisor
    participant Verify as Independent verifier
    participant Escrow as Detached immutable escrow
    actor Human as Human principal
    participant Promoter as Privileged promotion broker
    participant Target as Original workspace / external system
    Pod->>Stage: Write allowlisted candidate outputs
    Pod-->>Supervisor: Terminal receipt and claimed files
    Supervisor->>Stage: Verify authenticated lease, source revision and file identity
    Supervisor->>Escrow: Seal bounded immutable outputs
    Supervisor->>Verify: Freeze hashes, provenance and rubric
    Verify-->>Supervisor: Evidence-backed independent verdict
    Supervisor-->>Human: Read-only exact diff / preview
    Human->>Supervisor: Authenticated one-use approval
    Supervisor->>Promoter: Exact manifest, receipt and source pin
    Promoter->>Target: Check revision, stage atomic write, commit or rollback
    Promoter-->>Supervisor: Signed outcome or recovery-uncertain
~~~

The **staged workspace** is disposable and writable by the child; the principal's source/workspace is not. Once the child exits, detached **escrow** must survive lease closure for human review without extending the untrusted child authority. Approval is bound to exact source and artifact hashes and current policy. Linux stage-only Docker, authenticated escrow and **single-file** promotion are present on main as of the observed commits (LOCUS-465–469); generalized multi-file transactions, all operating systems, and other output types need separate evidence. Never describe per-file atomic replace as an atomic multi-file publication.

For non-code outputs, use the same versioned artifact/approval model: document draft -> render/QA -> signed-off file; spreadsheet -> formula/data validation; slide deck -> render/inspection; generated image -> inspected file and allowed provider; email -> draft -> explicit send; external file upload -> preflight -> grant -> state readback. A successful image/text model response is not equivalent to verified user-ready media.

## 10. Native tracking, integrations and autonomous triggers

The native task tracker is the principal's personal work index across areas, initiatives, projects and tasks. **Task tracking is not execution orchestration**: a task can outlive multiple runs, and a single run can contain an arbitrary validated DAG. External Linear/Asana/Jira items have a declared source of truth and explicit mapping/sync/conflict rules.

~~~mermaid
sequenceDiagram
    participant Source as Calendar / webhook / Linear / folder / inbox
    participant Connector as Scoped integration connector
    participant Intake as Intake + classification
    participant Tracker as Local native task tracker
    participant Orchestrator as Task Orchestrator
    participant Gateway as Gateway
    participant System as External API / computer tool
    Source->>Connector: Event, scheduled occurrence or changed source
    Connector->>Gateway: Authenticate, normalize and constrain source read
    Gateway-->>Intake: Tainted event with source id / idempotency key
    Intake->>Tracker: Triage or reconcile task state
    Tracker->>Orchestrator: Eligible bounded TaskIntent
    Orchestrator->>Orchestrator: Plan, dependencies, review and leases
    Orchestrator->>Gateway: Requested action
    Gateway->>System: Approved effect with idempotency / readback
    System-->>Gateway: Result or uncertain status
    Gateway-->>Tracker: Verified outcome / blocker / next task update
~~~

**Connector architecture:** Built-in first-party connectors and remote MCP tools implement the same typed tool-catalog/Gateway contract. Per-connection records contain provider, credential reference, area enablement, granted OAuth scopes, tool inventory/schema pin, egress destinations, sync cursor, webhook verification key reference, last successful reconciliation, error status and revocation epoch.

**Triggers:** Schedules, file watchers, external webhooks, inbox intake, tracker changes and peer requests generate **proposed or pre-authorized intents**, never unrestricted direct tool execution. Persist trigger ownership, deduplication keys and recurrence checkpoints. A retry against a remote API requires provider idempotency support or an explicit read-before-retry reconciliation; never claim exactly-once external side effects where the remote service cannot guarantee them.

**Example cross-domain workflow:** a meeting recap arrives through consented M365 retrieval -> extract tasks with citations -> principal confirms scope -> native tracker tasks created -> pods research suppliers and analyze costs in parallel -> independently verified spreadsheet/brief -> human reviews and authorizes a specific outgoing email -> Graph sends under the connected user's delegated OAuth scopes -> run records receipt and follow-up trigger. Each phase remains subject to the same area, taint, grant, budget and audit controls.

## 11. A2A, peer collaboration and optional distributed compute

**MCP** exposes tools/resources to an agent. **A2A** communicates task requests and progress between independent agent systems. **P2P federation** provides the Locus-to-Locus trust/transport and shared-space model. They overlap in transport possibilities, but do not share an authorization model by default.

- **Local sibling pods:** Use native typed, authenticated envelopes and durable artifact dependency references within the supervisor. Do not incur remote A2A complexity for same-process messages; offer A2A as an interoperability adapter if needed.
- **A2A external adapter:** Speak version-negotiated standard A2A (Agent Card, authenticated message, Task/Message/Artifact, status/get/cancel, streaming where admitted). Treat an Agent Card as capability metadata, not identity proof or authorization. Map inbound A2A Task into a *new* locally authorized TaskIntent. Map outbound Run status/artifacts back to A2A only after release policy.
- **Peer-to-peer transport:** Signed invite and peer-card exchange; device keys and explicit human pairing; local receiving-side admission; encrypted transport (candidate Iroh/libp2p QUIC, provenance-checked); optional CRDT shared spaces and scoped encrypted shared-object replication. Org identity attestations do not convey source-file rights.
- **Proposed organization compute fabric:** Home instance remains authoritative for its own task, policy and data; remote node may accept a **separately** attenuated eligible PodSpec after bilateral policy, attestation, revocation, data residency and budget checks. GPU model serving and distributed KV/caches have separate transport/isolation requirements. ADR-0003 remains **proposed**, and remote execution must remain deny-by-default until approved.

~~~mermaid
sequenceDiagram
    participant Home as Requesting Locus instance
    participant Peer as Paired peer Locus
    participant Auth as Peer identity / local policy
    participant Remote as Peer task supervisor
    participant Gate as Peer Gateway
    Home->>Peer: Signed A2A/P2P task proposal + bounded inputs
    Peer->>Auth: Verify paired identity and revoked-key epoch
    Auth-->>Peer: Identity proven (not execution authorized)
    Peer->>Remote: Inbound untrusted TaskIntent proposal
    Remote->>Gate: Recipient-side scope and admission request
    Gate-->>Remote: Deny / ask local human / narrow accept
    alt Locally authorized
        Remote->>Remote: Run under peer-owned grants and budgets
        Remote-->>Home: Signed status + released artifact references
        Home->>Home: Independent validation and local sharing policy
    else Not authorized
        Remote-->>Home: Explicit denial with safe reason
    end
~~~

**Control tower** is a logical device-discovery and work-status view across user-owned nodes, not a single privileged hosted coordinator. Peers can be independently online/offline, and refusal of delegated work is a normal outcome. No peer may directly push a Locus policy change or force publication on another device.

## 12. Persistence, eventing and failure recovery

| Data class | Durable owner / design target |
| --- | --- |
| Task, plan, run and approval state | Transactional module-owned local database, version and CAS transitions |
| Leases, reservations and side-effect fences | Durable ledger plus OS-supervised guardian, exact attempt epoch and restart reconciliation |
| Event and audit spine | Append-only typed events and tamper-evident hash chain, periodic signed checkpoints |
| Working pod state | Expiring run-scoped stages and checkpoints; no durable privileged persona |
| Human knowledge and memory | Area-scoped encrypted items, FTS/embeddings/graph derived indexes with source lineage |
| Durable artifacts | Content-hashed encrypted artifact store and separate immutable escrow for pending approvals |
| Credentials and identity keys | OS secure store and approved keychain-backed broker; never agent memory, logs or workflow templates |
| Telemetry | Local redacted OTel spans/metrics plus install posture; optional separately consented export |

Use a transactional outbox or equivalent commit-coupled event publication: a task-state transition and its emitted event cannot disagree after crash. Internal delivery is at-least-once with durable deduplication and idempotent consumers; external effects use narrowly scoped action receipts and reconciliation. Avoid standing up NATS, Envoy, Vault, OPA server, Jaeger or Redis as mandatory desktop services merely because they appear in old Docker/Helm templates. An optional full-stack/developer profile may use additional infrastructure behind the same ports.

**Restart/cancel sequence:** Persist a cancellation intent -> stop new leases -> revoke/expire pod grants -> terminate sandbox process tree and outbound proxy -> wait for OS guardian to reconcile stage integrity -> independently identify already-committed external effects -> mark uncertain outcomes for human reconciliation -> leave verified artifacts in escrow only when integrity is established. Recovery never silently replays an irreversible operation or releases an uncertain dependent node as successful. Supervisor admission requires a per-parent OS fence so concurrent recovering processes cannot strand or duplicate a PodAttempt (LOCUS-471).

**Offline/degraded behavior:** UI/tracker/local model/sandbox/native skills remain functional. Hosted providers, remote MCP, peers and external connector triggers become explicitly unavailable. Lexical knowledge retrieval remains usable if vector embeddings fail; no memory or policy degradation silently widens authorization.

## 13. Security, observability and acceptance criteria

Minimum release-blocking evidence:

1. **Authn/authz:** No protected API accessible without a valid local session. Cross-area and cross-principal reads denied. Expired/revoked run grants, altered schema, stale policy epoch and replay fail. Cedar shadow parity and counterexample invariants are documented before domain cutover.
2. **Model and tools:** Every advertised engine path is inventory-checked; no unauthorized shell, model endpoint, browser API, filesystem, network, MCP or secret use from an untrusted pod. Real OS/network negative tests, not mocks alone.
3. **Scheduler:** Cyclic/malformed plans, budget escalation, duplicate parent admissions, concurrent recovery, stale artifacts and canceled descendants are rejected. Diamond graphs can execute independent branches and gate downstream on verified versions.
4. **Artifact integrity:** Forged manifests, altered source, symlink/hardlink swaps, stage tampering, approval reuse/expiry/revocation and supervisor crash at critical phases fail closed. Multi-file publishing must not be described as atomic before its own transaction tests.
5. **External effects:** Unauthorized R3/R4 attempts are rejected. Idempotency, duplicate webhook events, offline interruption, remote API partial failure and uncertain state yield explicit reconciliation.
6. **Knowledge:** Poisoned documents and retrieved content cannot issue grants; cross-area RAG leakage denied; citations trace back to exact source offsets; forget cascades to indexes; vector or graph failures degrade visibly.
7. **Coverage:** Held-out end-to-end tasks in coding, research, documents, data, communications, business operations and computer use yield independently verifiable outcomes. Target metrics follow [product vision](product/01-vision-and-positioning.md) (e.g., >=70% completed to rubric, zero escaped policy violations), calibrated against actual evidence.
8. **Desktop/platform:** Clean Windows/macOS installs with signed binaries, verified sandbox parity, cancellation/panic and exact packaging claims. Linux Docker evidence is not proof of equivalent Windows/macOS security.
9. **Operational posture:** Each claimed control reports implemented/enforced/degraded/off/unverified and last test/evidence age. Logs/traces exclude prompt bodies, secrets, screenshots and unnecessary PII unless explicitly and separately consented.

## 14. Delivery phases and dependencies

| Phase | Product outcome | Primary tracked work |
| --- | --- | --- |
| A: Trustworthy executable two-specialist demo | Real native + Codex CLI worker with staged artifacts, independent handoff and canceled/failure runs, on one machine | LOCUS-470, LOCUS-416; builds on LOCUS-465–469 and 471 |
| B: General-purpose supervisor | Complete versioned planner preview/approval/launch, dynamic pod composition, independent verifier rubrics for noncoding domains | LOCUS-411, 412, 414, 460–464 |
| C: Universal integration gateway | Generic MCP host stdio+Streamable HTTP, inbound MCP server, version-negotiated external engine parity, built-in tool connectors and computer-use enforcement | LOCUS-417, 415/416, 24, 26, 432 |
| D: Personal work and knowledge | Native tracker sync, document/media ingest, scoped hybrid retrieval, governed memory, artifact creation/checkers, schedules | LOCUS-387, 424, 16, plus product docs 07/08/14/16 |
| E: Peer protocol and collaboration | Standard A2A adapters, pairing, signed peer identities, shared spaces, receiving-side delegation | LOCUS-43 and related peer issues |
| F: Optional organization compute | Separate approval of ADR-0003, issuer/revocation/attestation, remote admission and isolation evidence; never a dependency of local functionality | LOCUS-438–454; OCF-01 contract-only today |

These phases are **acceptance dependencies**, not timelines or a promise that an issue's "Done" status alone means the end-to-end behavior is shipped.

## 15. Verified implementation baseline (as of 2026-10-22)

Based on repository main and Linear state inspected for this document:

**Present on main / evidenced in bounded increments:** Canonical \`apps/backend\` control-plane and \`apps/workers\` layout, typed DAG/scheduler components, stage-bound Linux Docker child and OS guardian, authenticated artifact inspection, single-file escrow/approval/promotion with crash recovery, and LOCUS-471 per-parent admission fencing. See LOCUS-413, LOCUS-415, LOCUS-465–469, LOCUS-471. They establish security primitives; they do not themselves demonstrate the general-purpose end-user journey.

**Incomplete or not yet integrated end-to-end:** Real native+Codex two-specialist acceptance (LOCUS-470), dynamic domain-general pod composer (LOCUS-412), independent general-purpose artifact verification and synthesis (LOCUS-414), production TaskPlan preview/review/launch chain (LOCUS-460–464, draft PRs #125/#126), full Codex/Claude Code/OpenCode parity (LOCUS-416), generic MCP host (LOCUS-417), source-wide Cedar migration, full platform sandbox parity, broad noncoding quality suite and cross-domain general-purpose workflow execution.

**Not approved for live remote authority:** Organization compute federation (ADR-0003). A2A v1 interoperability and P2P peer collaboration remain target capabilities, not substantiated production features.

The product docs and README contain some conflicting historical **current-state** deployment claims (e.g., Postgres/Neo4j/NATS/Helm stack vs desired lightweight desktop). Use [product precedence](product/README.md) and actual running tests to determine shipped behavior.

## 16. Design questions and explicit decisions still needed

1. **Persistent state convergence:** Exactly which canonical SQLite state repository replaces in-memory-plus-Postgres snapshot for task/run/approval state, and what reversible migration and backup contract is required? Memory's SQLite port is already defined; do not casually conflate it with every other module's store.
2. **Engine containment on each OS:** Which specific command/built-in tool/network paths remain unmediated in Codex CLI, Claude Code and OpenCode? An engine/version is admitted only after real-denial evidence.
3. **MCP compatibility policy:** Set minimum 2025-era and 2026-07-28 versions by conformance test; use request-scoped auth and version-negotiated adapters rather than implicit transport sessions.
4. **Human approval semantics:** Identify which R3 external effects can use narrowly bounded standing grants and which protected actions always require per-action human confirmation; R4 policy/security mutation remains human-only.
5. **Artifact publication:** Add separately scoped multi-file transactional commit/rollback semantics; decide how image/doc/data artifacts are promoted to external platforms.
6. **External A2A:** Approve the standard version, public/private Agent Card publication policy and peer/device binding before offering network listeners.
7. **Organization compute:** Obtain principal acceptance of ADR-0003 and threat/evidence gates before remote inference, remote pod execution, shared GPU memory or default organization control tower features.

## 17. Related specifications

- [ADR-0001 Cedar authorization](adr/ADR-0001-cedar-authorization-core.md)
- [ADR-0002 general-purpose meta-harness](adr/ADR-0002-general-purpose-meta-harness.md)
- [ADR-0003 proposed org compute](adr/ADR-0003-organization-compute-fabric.md)
- [Module/port contracts](ARCHITECTURE-MODULES.md)
- [Core security and threat model](../THREAT-MODEL.md)
- [Production roadmap](product/20-roadmap-and-decisions.md)
- [MCP 2026-07-28 protocol announcement](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [A2A specification v1](https://a2a-protocol.org/v1.0.1/specification/)

**Maintenance rule:** Update this document when an accepted ADR changes an invariant or a gated integration becomes demonstrably supported. Do not upgrade a feature's status from "target" to "enforced" without code and test evidence.
