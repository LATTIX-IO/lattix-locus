# 08 · Feature Catalog

Intent and horizon only. Build status lives in dated status records. Horizons are defined in [20](20-roadmap-and-decisions.md) §1: **H0** recovery, **H1** personal operator, **H2** always on, **H3** federated. "Carry" means the capability existed at `ae4d703` and should be restored or adapted, not rebuilt.

## Shell and global

| Capability | Horizon | Notes |
|---|---|---|
| Desktop app (Tauri) with tray, updater, first-run | H1 (carry) | Restore from `ae4d703`; sign and notarize macOS |
| Always-on background service | H1 | Per-user service; UI optional ([19](19-platform-and-deployment.md)) |
| Area switcher filtering every space | H1 | |
| Command palette, quick capture hotkey | H1 | |
| Panic key handled by native helper | H1 | Works when UI is frozen |
| Universal inspector | H1 | |
| Notifications with quiet hours and batching | H1 | |
| Voice input and spoken replies (local speech models) | H2 | Push-to-talk first; wake word later |
| Companion layout (narrow window) | H2 | |
| Mobile approvals via paired peer device | H3 | Device-bound signed approvals |

## Chat

| Capability | Horizon | Notes |
|---|---|---|
| Streaming chat with run cards and approvals | H1 (carry) | Exists on main today |
| Area-scoped conversations | H1 | |
| `@` references and `/` skills | H1 | |
| Chat → task → run in one turn | H1 | |
| Engine and tier badge on every answer | H1 | |
| Conversation memory capture as proposals | H1 | |

## Delegation and runs ([11](11-agentic-model.md))

| Capability | Horizon | Notes |
|---|---|---|
| Envelope proposal and editor | H1 | |
| Run loop: plan → act → observe → verify → revise | H1 | Built on the harness from `ae4d703` |
| Tiered autonomy R0–R4 | H1 | |
| Approval cards with once / run / standing grants | H1 | |
| Budgets (time, spend, actions) with hard stops | H1 | |
| Verification against done criteria | H1 | Verification column |
| Pause, take over, edit plan, resume, kill | H1 | |
| Run timeline, evidence, spend, audit tabs | H1 | |
| Playbooks (envelope and plan templates) | H1 | Replaces workflow-first authoring |
| Sub-agents and parallel steps | H2 | |
| Column assemblies as gates for R3 and low confidence | H2 | Shadow in H1 |
| Unattended issue runs (Symphony generalized) | H2 | Any tracker or trigger, any engine |
| Resumable runs across restarts | H1 | Durable run state; no silent loss |

## Engines and routing ([10](10-inference-and-model-routing.md))

| Capability | Horizon | Notes |
|---|---|---|
| Local models via Ollama and OpenAI-compatible local servers | H1 (carry) | llama.cpp server, LM Studio, vLLM |
| Local model management (pull, delete, catalog) | H1 (carry) | Provenance-filtered catalog |
| API providers (OpenAI, Anthropic, Azure OpenAI, Gemini, Mistral, others) | H1 (carry) | Restore `openai` dependency; fail loudly |
| Vendor agent engines: Codex CLI, Claude Code | H1 | Driven as sandboxed subprocesses with MCP gateway |
| OpenCode as an engine (API keys or local models only) | H2 | |
| M365 Copilot context provider (Retrieval API) | H2 | Needs M365 Copilot license |
| M365 Copilot Chat API grounding | H2 | Preview API; answer-only |
| Laya judge service (local) | H1 | Shadow mode first |
| Jev adapter (hosted, optional) | H2 | Area-policy gated |
| Router: tier test and area data policy | H1 | |
| Spend and quota tracking per engine | H1 | |

## Computer use ([12](12-computer-use.md))

| Capability | Horizon | Notes |
|---|---|---|
| Desktop control on Windows (UI Automation + input) | H1 | |
| Desktop control on macOS (Accessibility + ScreenCaptureKit + input) | H1 | |
| Browser control via dedicated agent profile (CDP) | H1 | |
| Takeover HUD, user-input preemption, panic key | H1 | |
| App allow/deny lists; sensitive app defaults | H1 | |
| Frame recording with retention policy and redaction | H1 | |
| Isolated desktop mode (separate session or VM) | H2 | |
| Attach to user's own browser profile (approval-gated) | H2 | |

## Security ([13](13-security-architecture.md))

| Capability | Horizon | Notes |
|---|---|---|
| Single gateway for tools, models, egress, computer use | H1 | |
| Embedded policy engine (Rego) on the execution path | H1 | Replaces the Python copy of the rules |
| Policy parity tests | H1 | Threat T9 |
| Capability tokens (Biscuit) for grants | H1 | Replaces custom HMAC tokens |
| Taint labels and propagation | H1 | |
| Secrets in OS keychain, injected by reference | H1 (carry) | |
| Per-run local egress proxy with allowlists | H1 | |
| Sandbox tiers: seatbelt, bwrap, AppContainer, restricted process | H1 (carry) | Restore Windows tier |
| Hash-chained signed audit, durable | H1 (carry) | Restore Postgres audit log |
| Posture page with per-control evidence | H1 | |
| Security CI (SAST, secrets, SCA, SBOM, DAST) | H0 | Land the uncommitted CI rewrite |
| Data-centric protection for shared objects (Lattix TDF-style) | H3 | |

## Memory and knowledge ([14](14-memory-and-knowledge.md))

| Capability | Horizon | Notes |
|---|---|---|
| Memory items with types, scopes, provenance | H1 | |
| Memory review (single and batch) | H1 | |
| Hybrid retrieval (lexical + vector) with Laya rerank | H1 | pgvector (carry) |
| Source ingestion pipeline with taint and screening | H1 | |
| Columns as memory owners and voters | H1 (partial carry) | Goal, evidence, assembly, commitment exist |
| Additional columns: risk, plan, verification, domain | H2 | |
| Knowledge collections / RAG over folders | H1 (carry) | |
| Import of existing memory (AGENTS.md protocol, Obsidian vault) | H2 | |

## Skills, MCP and extensions ([15](15-skills-mcp-and-extensions.md))

| Capability | Horizon | Notes |
|---|---|---|
| Skills in Agent Skills format (SKILL.md folders) | H1 (carry) | |
| Skill lifecycle: import, scan, evaluate, trust, revoke | H1 (carry) | |
| MCP client via gateway (stdio and HTTP, OAuth) | H1 (carry) | Restore deleted `mcp_client` |
| Tool schema pinning and change detection | H1 | |
| Locus as an MCP server | H2 | |
| Signed skill packages and a personal registry | H2 | |

## Work tracker ([16](16-work-tracker.md))

| Capability | Horizon | Notes |
|---|---|---|
| Areas, tasks, sub-tasks, statuses, priorities, estimates, due dates | H1 | |
| Labels and label groups (category, client, solution) | H1 | |
| Projects with milestones; initiatives | H1 | |
| List, board (Kanban) and timeline layouts; saved views | H1 | |
| Triage inbox | H1 | |
| Assign to my agent → delegated run | H1 | |
| Cycles | H2 | |
| Recurring tasks | H1 | |
| Linear, Asana, Jira two-way sync | H2 | Read-first; write-back per mapping |
| Project updates drafted by agent | H2 | |
| Shared projects with peers | H3 | |

## Canvas and whiteboard ([17](17-canvas-and-whiteboard.md))

| Capability | Horizon | Notes |
|---|---|---|
| Forever whiteboard per area (Excalidraw) | H1 | |
| Board content indexed into memory | H1 | |
| Agent read/draw tools on boards | H2 | |
| Data-centric models (diagram-js) | H2 | |
| Frame → model / tasks formalization | H2 | |
| Playbook and assembly views in diagram-js | H2 | Replaces React Flow |
| Real-time co-editing with peers | H3 | |

## Federation ([18](18-federation-and-collaboration.md))

| Capability | Horizon | Notes |
|---|---|---|
| Principal and device keys; signed peer cards | H1 | Seam only; no sharing yet |
| Pairing (invite, LAN discovery) and revocation | H3 | |
| Shared spaces with CRDT sync | H3 | |
| Cross-peer delegation with local acceptance | H3 | |
| Org membership attestations without a hub | H3 | |

## Explicitly out of scope

- Hosted multi-tenant SaaS operation of Locus.
- A Kubernetes/Helm deployment as a primary target (kept as an optional, unsupported profile; see [19](19-platform-and-deployment.md) §6).
- A node-wiring workflow canvas as the primary authoring model.
- Reusing consumer-subscription OAuth tokens outside the vendor's own client.
- GitHub Copilot as an engine (not selected; can be added as an adapter later).
- Endpoint fleet management, MDM, or central admin over peers.
- Local models from excluded jurisdictions ([03](03-product-principles.md) P28).
