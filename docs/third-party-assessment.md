# Third-party repository assessment

Status: **Active reference**. Assessed 2026-10-03 to 2026-10-05 from public sources, read-only: no code was cloned or run.
Owner: principal. Program epic: **LOCUS-406**. Related: P28 / D-29 (provenance), P29 (licenses), P30 (extend FOSS before building), D-28 (modules and ports), P18 / D-08 (forms first; diagrams are views).

The principal shared these repositories and models as candidates to integrate, fork or learn from. This document records one P30 decision per item, so the issues, PRs and future reviews can refer to it. Each entry gives the facts that drove its decision. Facts marked *unverified* could not be confirmed from public pages.

## 1. How to read this

### 1.1 Decision classes

| Class | Meaning | When it fits |
|---|---|---|
| **Integrate as-is** | Pinned dependency or out-of-process service, used unmodified behind a D-28 port | License-clean and provenance-clean, does what we need, and can be mediated by the gateway |
| **Fork & extend** | A Lattix fork with a patch ledger, an upstream-sync cadence and a security re-audit for each upstream release | Only when extension points can't do the job. Every fork is a standing maintenance cost |
| **Custom clone** | Re-implement the capability inside Locus, borrowing the design and named patterns, and copying no code unless it is license- and provenance-clean | The idea is right, but the code would bypass the gateway, mismatches our stack, is hosted, or comes from a D-29 origin |
| **Reference only / skip** | Read for ideas; no work item, or a P3 watch item | Weak fit, immature, or blocked by license or provenance |

### 1.2 Gates that apply to every item

1. **License (P29).** Code we ship or copy must be AGPL-compatible: MIT, Apache-2.0, BSD or similar. Enterprise directories (`ee/`, "Pro" packages) and repos with no license are reference only.
2. **Provenance (P28 / D-29).** Code or weights from a P28-listed country may be used only **locally**, and only after a recorded inspection (LOCUS-358 tooling). Hosted, API or web services from those origins are never allowed. A clean-room re-implementation from a public design needs no inspection, because no code is taken.
3. **One gateway (P6).** Every model and tool call goes through the Locus gateway. Anything that makes its own model or tool calls either runs out of process behind the gateway, or is cloned.
4. **Telemetry off by default.** Every adopted component ships with its telemetry disabled (the env vars are listed per item).
5. **Ports (D-28).** Every integration sits behind a versioned port, so it can be swapped.

### 1.3 Priorities

- **P1:** now, in the H1 personal operator.
- **P2:** next.
- **P3:** later, or watch.

Effort is S (days), M (1–3 weeks) or L (more than 3 weeks).

## 2. Summary

| # | Repository / model | Class | Pri | Effort | Locus module (port) | Linear | Prerequisites / depends on |
|---|---|---|---|---|---|---|---|
| 1 | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | **Integrate as-is** + Locus extension package (fork only if forced) | P1 | M | Agent runtime (`AgentRuntime`) | LOCUS-363 | Decided D-27; default since 2026-10-04 |
| 2 | [openai/symphony](https://github.com/openai/symphony) | **Custom clone** (continue) | P1 | S–M | Intake / loop runner (`Tracker`, `Trigger`) | LOCUS-407 | Linear intake; worktree isolation |
| 3 | [CopilotKit/CopilotKit](https://github.com/CopilotKit/CopilotKit) (AG-UI) | **Integrate as-is**: `@ag-ui/core`, `@ag-ui/client` and `ag-ui-langgraph` only | P1 | M | Surfaces (`SurfaceAdapter`) | LOCUS-369 | Gateway emits AG-UI events; HITL → native confirmation |
| 4 | Laya (convaiinnovations/laya) | **Integrate as-is** (local `/v1/systemone` sidecar) | P1 | S | Judge (`DecisionModel`) | LOCUS-367 | Pin and hash; egress check; validate on Locus cases |
| 5 | [TypeSafe Jev](https://typesafe.ai) | **Integrate as-is**, opt-in hosted tier | P2 | S | Judge (`DecisionModel`) | LOCUS-367 | Area-policy opt-in; no sensitive state sent |
| 6 | [Cloudflare/clef](https://huggingface.co/Cloudflare/clef) (Clef-flash 9B) | Reference only, **blocked by the D-29 format rule** | P3 | — | Judge (`DecisionModel`) | LOCUS-367 | Needs a weights-only (safetensors/GGUF) artifact with no `trust_remote_code` loader; Qwen lineage |
| 7 | [CopilotKit/OpenBot](https://github.com/CopilotKit/OpenBot) | **Custom clone** | P2 | L | Always-on, computer use | LOCUS-370 | LOCUS-354; OS sandbox; browser tiers |
| 8 | [CopilotKit/OpenDots](https://github.com/CopilotKit/OpenDots) | **Custom clone** | P2 | M | Always-on agent | LOCUS-370 | LOCUS-354; durable runs |
| 9 | [CopilotKit/openmuse](https://github.com/CopilotKit/openmuse) | **Custom clone** | P2 | M | Always-on, computer use | LOCUS-370 | Durable runs; browser control |
| 10 | [CopilotKit/OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI) | **Custom clone** | P2 | M | Surfaces (results) | LOCUS-409 | LOCUS-369; Tauri CSP |
| 11 | [CopilotKit/shadify](https://github.com/CopilotKit/shadify) | Reference only | P3 | S | Surfaces (results) | LOCUS-409 | — |
| 12 | [CopilotKit/generative-ui](https://github.com/CopilotKit/generative-ui) | Reference only (**no license**) | P3 | S | Surfaces (results) | LOCUS-409 | License answer on CopilotKit/CopilotKit#7617 |
| 13 | [CopilotKit/channels-sdk](https://github.com/CopilotKit/channels-sdk) | **Integrate as-is** (self-managed Slack sidecar) | P3 | M | Intake / surfaces (`ChannelAdapter`) | LOCUS-408 | AG-UI endpoint; keychain; taint labels |
| 14 | [CopilotKit/OpenTag](https://github.com/CopilotKit/OpenTag) | Reference only | P3 | S | Intake / surfaces | LOCUS-408 | — |
| 15 | [openclaw/openclaw](https://github.com/openclaw/openclaw) | **Custom clone** (channel adapter, sender pairing, release channels) | P3 | M | Intake / surfaces, skills | LOCUS-408 | **Never consume ClawHub skills** |
| 16 | [simstudioai/sim](https://github.com/simstudioai/sim) | **Custom clone** (not a merge) | P2 | L | Pipelines / Playbooks, triggers | LOCUS-364 | D-08 canvas decision; LOCUS-356 ports; observability store |
| 17 | [oomol-lab/open-flow](https://github.com/oomol-lab/open-flow) | **Custom clone** (UX patterns only) | P2 | M | Pipelines / Playbooks canvas | LOCUS-397 | **D-08 decision** (diagram-js vs xyflow); D-29 if any code is used |
| 18 | [philbotar/OpenFlow](https://github.com/philbotar/OpenFlow) | Reference only (borrow post-run advisor and per-node approval) | P3 | S | Pipelines, evals | LOCUS-397 | — |
| 19 | [evermind-ai/raven](https://github.com/evermind-ai/raven) | **Custom clone** (Curator gate) | P2 | M | RSI loop, evals | LOCUS-365 | Scorecard + held-out split (LOCUS-351/382); D-29 for any code |
| 20 | [truefoundry/trueforge](https://github.com/truefoundry/trueforge) | **Custom clone** (context engineering, approvals) | P2 | M | Agent runtime extensions, tools | LOCUS-373 | LOCUS-363 extension package |
| 21 | [google/ax](https://github.com/google/ax) | Reference only | P3 | S | Port contracts | LOCUS-372 | — |
| 22 | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | Reference only | P3 | S | Tools / skills port | — (runtime bake-off doc) | D-29 (pattern source only) |
| 23 | [TencentCloud/Octop](https://github.com/TencentCloud/Octop) | Reference only | P3 | S | Product / UX reference | LOCUS-371 | 1260H origin |
| 24 | [TencentCloud/octop-browser](https://github.com/TencentCloud/octop-browser) | **Custom clone** (clean-room) | P2 | M | Computer use (`BrowserDriver`) | LOCUS-371 | 1260H: no code copied |
| 25 | [TencentCloud/octop-memory](https://github.com/TencentCloud/octop-memory) | **Custom clone** (clean-room) | P2 | M | Memory (`LongTermMemoryStore`) | LOCUS-371 | Cortical columns stay native (D-10) |
| 26 | [TencentCloud/octop-harness](https://github.com/TencentCloud/octop-harness) | Custom clone (patterns) | P3 | S | Agent runtime extensions | LOCUS-371 | LOCUS-363 |
| 27 | [TencentCloud/octop-gateway](https://github.com/TencentCloud/octop-gateway) | Custom clone (patterns) | P3 | S | Intake / surfaces | LOCUS-408 | 1260H: no code copied |
| 28 | [multica-ai/multica](https://github.com/multica-ai/multica) | **Custom clone** (patterns only; **non-FOSS license**) | P2 | M | Intake / loop UX | LOCUS-398 | LOCUS-407 |
| 29 | [stablyai/orca](https://github.com/stablyai/orca) | **Custom clone** (cockpit patterns) | P2 | M | Loop UX | LOCUS-398 | LOCUS-407 |
| 30 | [pacifio/atlas](https://github.com/pacifio/atlas) | **Custom clone** (checkpoints, persistence-time secret scrub, ACP) | P2 | M | RSI provenance, observability | LOCUS-399 | LOCUS-375; LOCUS-380 |
| 31 | [alphaXiv/OpenResearch](https://github.com/alphaXiv/OpenResearch) | **Custom clone** (experiment tree, compute backends) | P2 | M | RSI loop, evals | LOCUS-399 | LOCUS-351 |
| 32 | [langwatch/langwatch](https://github.com/langwatch/langwatch) | **Custom clone** (patterns) + optional OTLP export | P3 | S–M | Observability, evals | LOCUS-400 | LOCUS-375 |
| 33 | Langfuse / LangSmith | **Custom clone** (capabilities on OTel + SQLite) | P1 | M | Observability (`TelemetrySink`) | LOCUS-375 | Principal decision 2026-10-04 |
| 34 | [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) | **Integrate as-is**, pinned 0.3.224 | P1 | — | Evals (`EvalSuite`) | LOCUS-381 | Security review of every upgrade |
| 35 | [cedar-policy/cedar](https://github.com/cedar-policy/cedar) | **Integrate as-is** (binding chosen in phase 0) | P1 | L | Trust kernel (`PolicyEngine`) | LOCUS-390 | D-30 / ADR-0001 |
| 36 | [LingyiChen-AI/DeepDiagram](https://github.com/LingyiChen-AI/DeepDiagram) | **Custom clone** (renderer-router pattern) | P3 | S | Skills | LOCUS-401 | D-29 if any code is used |
| 37 | [JustVugg/colibri](https://github.com/JustVugg/colibri) | Reference only (revisit for overnight batch jobs) | P3 | L | Model access (`ModelProvider`) | LOCUS-366 | D-29 on every model; below 1 tok/s here |
| 38 | [dream-num/univer-workspace](https://github.com/dream-num/univer-workspace) | **Skip** (only the `@univerjs/*` core, later) | P3 | M | Documents surface | LOCUS-368 | Pro license; DeepSeek Harness inside; D-29 |
| 39 | MiroFish (666ghj/MiroFish) | **Skip** | — | — | — | — (noted in LOCUS-401) | China origin, Shanda-backed; cloud defaults |
| 40 | LATTIX-IO/savant (first party) | **Integrate as-is** (skills source connector) | P2 | M | Skills (`SkillStore`) | LOCUS-388 | Principal provides API access or docs |

**Net result:**
- 7 items are integrated as-is: Deep Agents, AG-UI, Laya, Jev, channels-sdk, Inspect AI, Cedar. Savant is a first-party connector on top of these.
- **Zero forks.**
- 21 items are custom clones.
- The rest are reference only or skipped.

There are no forks because extension points and out-of-process services cover every case so far. A fork stays the last resort (LOCUS-363).

## 3. Assessments by area

### 3.1 Agent runtime and harness

**LangChain Deep Agents.** MIT, LangChain Inc. (US).
- Decision: integrate as-is, pinned, plus a separate Locus extension package (middleware, tools, runtime adapter). Fork only when an extension point can't do the job (LOCUS-363).
- Basis: D-27, confirmed 2026-10-04. Deep Agents won the bake-off on the RSI scorecard: 35 of 40 passes vs 32 of 40, with 0.72× the tokens.
- It is the base for everything in this section.
- **Recorded exception to [ARCHITECTURE-MODULES §5](ARCHITECTURE-MODULES.md#5-third-party-code).** §5 runs third-party implementations out of process. D-27 instead runs Deep Agents **in the Locus process** as the default `AgentRuntime`. It is a pinned, provenance-checked library, not a plugin. Compensating controls (they reduce the risk but are **not** an equivalent process boundary):
  - every model and tool call it makes goes through the Locus extension package to the gateway (rule 5 of §2);
  - the gateway-bypass scan fails CI on any direct provider client;
  - the RSI candidate runs jailed in its own AppContainer.

  **Residual risk:** an upstream release could add a call path that skips the middleware. Mitigations: the pin, a re-audit on every upgrade, and the bypass scan. Moving the runtime behind an A2A/ACP sidecar is the stronger option, and it's listed for the principal in §5.

**truefoundry/trueforge.** MIT, TrueFoundry (US). About 6.1k stars; TypeScript on Node 22+.
- An open harness runtime: model calls, MCP, `SKILL.md` skills, sandboxes, approvals, compaction and sessions.
- **Custom clone** of its context-engineering bundle into the Locus extension package:
  - deferred tool loading;
  - "Code Mode";
  - offloading large tool results;
  - compaction;
  - plus its approval, ask-user and generative-UI checkpoints.
- Measure each technique on the bake-off suite.
- Don't adopt its sandbox: it's Daytona (hosted), which conflicts with our OS-native sandboxes. Telemetry *unverified*.

**google/ax.** Apache-2.0, Google. About 13.1k stars; Go.
- A Kubernetes-native orchestrator for agent workloads at very large scale. It is "in heavy development", and no single-machine mode is documented.
- **Reference only.** Mirror its Task / Workspace / Model resource shapes and its suspend/resume semantics in the D-28 port contracts.

**deepseek-ai/deepseek-harness.** MIT, DeepSeek (Hangzhou, China).
- A developer preview with breaking changes; v0.2.1-alpha.1 was released 2026-10-03.
- Compared with Deep Agents in the [runtime bake-off](development/runtime-bakeoff-2026-10.md). It is a **pattern source only** under D-29:
  - scoped plugin lifecycles that clean up their own resources when unloaded;
  - agent-written extensions, but only through review, signing and the eval gate;
  - a compatibility layer for an existing extension ecosystem.

**TencentCloud/octop-harness.** MIT, Tencent (China; **on the DoD 1260H list since January 2025**).
- It wraps Deep Agents `create_deep_agent`, so its architecture is closest to ours.
- **Custom clone, clean-room:**
  - the guardrail and permission middleware layered over Deep Agents;
  - an AgentManager-style registry for multiple agents.
- No code copied.

### 3.2 Self-improvement (RSI), evals and provenance

**openai/symphony.** Apache-2.0, OpenAI (US).
- About 27.5k stars; v0.0.3 released 2026-09-15. A spec plus an Elixir reference implementation.
- The Locus `WORKFLOW.md` contract already follows it.
- **Custom clone (continue), P1:** align with [SPEC.md](https://github.com/openai/symphony/blob/main/SPEC.md):
  - claim states, a reconciliation tick with stall detection, backoff and continuation retries, concurrency per state;
  - hook failure semantics;
  - sanitised workspace names;
  - **tracker credentials not inherited by the agent**.
- Running it as-is would bypass the gateway. Pin the SPEC commit we align to (LOCUS-407).

**evermind-ai/raven.** Apache-2.0, EverMind ("incubated by Shanda Group", China; **D-29**).
- About 5.2k stars; v0.2.4. A "harness of harnesses": a Curator rewrites each agent's strategy modules.
- **Custom clone:**
  - the **Curator gate**: nothing is installed until it is verified, and a failure sends the change back;
  - strategy modules as versioned artifacts;
  - eval signals passed to the Curator **with the reference answer stripped out**;
  - ACP presets for outside agents, behind the gateway.
- Don't use its `curl | bash` install. Make sure held-out signals never reach any curator (LOCUS-382).

**alphaXiv/OpenResearch.** MIT, alphaXiv (US, *unverified*). Rust; local-first SQLite. Release builds send opt-out telemetry.
- **Custom clone:**
  - the **experiment tree** as the data model for scorecard candidates: node = hypothesis / worktree / run / evidence / score, edges = lineage;
  - pluggable compute backends for eval runs: local, SSH, Slurm, Ray, Modal (LOCUS-399).

**pacifio/atlas.** Apache-2.0, a single maintainer. A Tauri app, alpha; PostHog telemetry is on by default.
- **Custom clone:**
  - session→commit **checkpoints**, re-linked through patch-id after a rebase;
  - **secret scrubbing at the point of persistence** in the trace store;
  - ACP for hosting outside agents (LOCUS-399).

**Inspect AI.** **Integrate as-is**, pinned at 0.3.224. Every upgrade gets a security review, and the pin is replaced only if another framework measures better (LOCUS-381).

**multica-ai/multica.** Custom "Multica License" (Apache-2.0 plus restrictions: no hosted service, no embedding in commercial products). **Not FOSS, so no code may be copied.**
- Index Labs (Hong Kong). Telemetry is on by default (`telemetry.multica.ai`).
- It has the most mature issue → agent → PR experience, but no security model, memory or eval gate.
- **Custom clone of patterns only:**
  - CLI auto-detection and registration by a daemon;
  - mid-run steering;
  - issue wakeups with runaway protection;
  - auto-close an issue when its PR merges;
  - cost per run;
  - execution-log replay (LOCUS-398).

**stablyai/orca.** MIT, Stably AI (US). Electron; PostHog telemetry is on by default.
- An operator cockpit, **not** a Symphony replacement: runs are human-initiated, with no WORKFLOW contract and no CI gate.
- **Custom clone:**
  - a cockpit with one worktree per task;
  - "annotate a diff line → re-prompt the agent";
  - remote steering later (LOCUS-398).

### 3.3 Judge and decision tier

These items decide the D-11 defaults. **Standardise the `DecisionModel` port on the Jev-compatible `/v1/systemone` schema**, so the providers can be swapped (LOCUS-367).

| Option | Facts | Decision |
|---|---|---|
| **Laya** (Convai Innovations) | Apache-2.0. 421M ModernBERT-large (non-China lineage). `laya-serve` exposes `/v1/systemone`. Self-reported 0.766 vs Jev 0.727 on typed decisions; ~33 ms per question on a T4; up to ~20 options | **Integrate as-is**: local default for risk/intent classification and done-criteria checks with few options (P1) |
| **Clef-flash** (9B) / Clef (27B) | Apache-2.0; post-trained from **Qwen** (Alibaba lineage), with a joint schema head that needs `trust_remote_code`. Strong on intent classification (BANKING77 94.2), weak on reasoning (GPQA 48 vs Jev 78.3). 27B BF16 ≈ 55 GB | **Reference only (P3), blocked.** The D-29 model check refuses configs that request custom code (`trust_remote_code`, `auto_map`) and any loader code ([PROVENANCE §4](PROVENANCE.md)), so the published artifact cannot pass inspection. Revisit only if a weights-only safetensors or GGUF artifact appears whose joint head loads without custom code; inspect that exact artifact. Skip the 27B locally either way |
| **Jev** (TypeSafe) | Hosted only (waitlist); $0.042 per million input tokens; leads on reasoning-heavy judgments | Opt-in hosted tier for reasoning-heavy acceptance judging (P2). Never sends sensitive state |
| Ollama structured output | No new dependency; no calibrated per-option probabilities | Fallback for open-ended questions or more than 20 options |

**Open question for the principal:** Clef is also served on Cloudflare Workers AI. Does hosted inference of a Qwen-derived model count as "hosted inference from those origins" under D-29? The conservative reading is yes, so local only.

### 3.4 Desktop UI, surfaces and generative UI

**CopilotKit core / AG-UI.** MIT (monorepo, no directory carve-outs). About 37.8k stars; daily releases; v1.76.0 shipped AG-UI 1.0.
- **Paid layer:** "CopilotKit Intelligence" is a hosted or licensed self-host service. It covers streams/replay, memories, analytics and managed channels.
- **Telemetry is on by default.** `@copilotkit/runtime` includes Segment and Scarf. Opt out with `COPILOTKIT_TELEMETRY_DISABLED=true` and `SCARF_ANALYTICS=false`.
- **Decision:**
  - **Integrate as-is, protocol only:** `@ag-ui/core` and `@ag-ui/client` pinned to 1.0.x, plus `ag-ui-langgraph`, emitted from FastAPI behind the gateway.
  - **Do not adopt `@copilotkit/runtime`.** It adds a Node hop, duplicates the gateway, and carries the telemetry.
  - For the chat and run surfaces, **prefer [assistant-ui](https://github.com/assistant-ui/assistant-ui)**: MIT, shadcn-native, with AG-UI and LangGraph adapters. `@copilotkit/react-core` is a pinned fallback.
  - Client-side "frontend tools" must never bypass the gateway (LOCUS-369).

**OpenBot, OpenDots, openmuse.** All MIT. All need **CopilotKit Intelligence** to chat or persist, so they count as hosted. All are young: OpenDots is one week old.
- **Custom clone**, feeding the always-on agent (LOCUS-354, via LOCUS-370).
- From **OpenBot:**
  - decide, then write the audit row, then act;
  - deny is evaluated before allow, and a broken rule refuses;
  - unknown MCP tools are treated as writes;
  - a container, volume and browser profile per agent (optional gVisor);
  - takeover events (`help_requested` / `control_taken` / `control_released`);
  - routine guardrails: 15-minute minimum interval, at most 20 routines, auto-disable after 10 consecutive failures.
- From **OpenDots:**
  - permission toggles per specialist;
  - approve-and-save HITL cards;
  - a live activity / terminal view during takeover;
  - an "Interrupted" state with explicit retry. Locus resumes durably where it is safe, and asks only for uncertain writes.
- From **openmuse:**
  - durable task plans with pause, resume, cancel and retry;
  - **no hidden retries after an uncertain external write**;
  - short-lived signed URLs for files and consoles;
  - the browser worker as a separate process;
  - inline result cards.

**OpenGenerativeUI, shadify, generative-ui.** OpenGenerativeUI and shadify are MIT. **generative-ui still has no license**: CopilotKit/CopilotKit#7617 is open with no reply.
- **Custom clone** of a three-tier results surface (LOCUS-409):
  - **Controlled:** typed Locus cards.
  - **Declarative:** A2UI or Open-JSON-UI specs restricted to an allowlist of shadcn components (the shadify idea).
  - **Open-ended:** agent HTML/SVG/JS in iframes. Use OpenGenerativeUI's streaming parameter order, theme injection and auto-sizing.
- The iframes are sandboxed **without** `allow-same-origin`, with `connect-src 'none'` and no Tauri IPC. Tier 3 is off by default.

### 3.5 Pipelines, Playbooks and the visual canvas

> **Conflict to resolve first:** D-08 retires React Flow (diagram-js renders models; Excalidraw is the whiteboard), and P18 says diagrams are views over forms and declarative files. Sim, open-flow and the earlier LOCUS-397 draft all lean on React Flow / xyflow. **Recommendation:** keep D-08 and render Playbook / Pipeline definitions with diagram-js ("playbook structure" is a listed Model use in [17](product/17-canvas-and-whiteboard.md)), borrowing the UX patterns below. Revisit D-08 only if the principal prefers xyflow's editing experience. Either way the definition stays in forms and files (P18). `reactflow ^11` is still in `apps/frontend/package.json` as legacy.

**simstudioai/sim.** Apache-2.0 at the root, **but `apps/sim/ee/` is an enterprise license** and must not be copied. Sim Studio, Inc. (US).
- About 29.8k stars; TypeScript/Bun, Postgres.
- Telemetry goes to `telemetry.simstudio.ai` and is **on by default**. Copilot/"Mothership" is a hosted service, and code execution uses E2B (hosted).
- **"Merge into our Deep Agents fork" isn't technically possible:** Sim is TypeScript on Postgres with no Python harness to merge into, and its engine would bypass the gateway.
- **Custom clone (P2, L):**
  - a JSON block/edge workflow schema compiled to a LangGraph graph;
  - a block registry with typed inputs and outputs;
  - a trigger registry;
  - a run-trace viewer;
  - "deploy a workflow as an MCP tool".
- Keep the scope to Pipelines (deterministic flows, D-16). Avoid turning it into an n8n clone (LOCUS-364).

**oomol-lab/open-flow.** Apache-2.0, beta. Strong but unconfirmed signs of **mainland-China origin (D-29)**. `posthog-js` is in its server package.
- **Custom clone of the UX** (LOCUS-397):
  - the agent authors the flow (through CLI or MCP tools), a human reviews the canvas, then publish;
  - draft/live versions with rollback;
  - typed input/output mapping per node;
  - approval nodes and bounded agent nodes.
- No dependency.

**philbotar/OpenFlow.** MIT, one maintainer, about 20 stars; Tauri + SolidJS.
- **Reference only.** Borrow the **post-run advisor** (it suggests workflow changes, which can feed the RSI loop) and per-node approval modes.

**DeepDiagram.** AGPL-3.0, funstory.ai (Beijing; **D-29**). Dormant since February 2026. It ships the DeepSeek hosted API as a default provider.
- **Custom clone** of the renderer router: a router picks a renderer-specific sub-agent, which emits declarative Mermaid, draw.io, ECharts or Markmap (LOCUS-401).

### 3.6 Computer use, memory and channels

**octop-browser.** MIT, Tencent (1260H). It drives Chrome over CDP directly.
- **Custom clone, clean-room** (LOCUS-371):
  - a **four-level DOM snapshot**: minimal ~50 tokens, interactive ~200–500, full, and structured JSON, with refs that stay stable across reflow;
  - **record → intent-level skill** with `{{sensitive_value}}` placeholders supplied at replay, and secrets masked at record time;
  - idle-timeout auto-stop.
- Locus adds what it lacks: domain allowlists and approvals (D-25 tiers).

**octop-memory.** MIT, Tencent (1260H). Standard library + SQLite/FTS5.
- **Custom clone, clean-room:**
  - a promotion pipeline RawEvent → Candidate → AtomCard with evidence references;
  - token-budgeted recall;
  - a portable `.hmpkg`-style export.
- These feed the native cortical-column memory and **never replace it** (D-10). It fits the SQLite store from LOCUS-387.

**Octop (the assistant itself).** MIT, Tencent; betas only. **Reference only**, for product and UX comparison.

**channels-sdk, OpenClaw, octop-gateway, OpenTag** (LOCUS-408):
- **channels-sdk:** **integrate as-is** as a self-managed Slack sidecar (Socket Mode; no Intelligence; telemetry env vars set).
- **OpenClaw** (MIT, OpenClaw Foundation, US; about 391k stars): **custom clone** of:
  - its channel-adapter interface;
  - **pairing approval for unknown senders**;
  - extended-stable release channels.
  - **Never consume ClawHub skills:** more than 1,100 malicious skills were reported, and CVE-2026-25253 was cited.
- **octop-gateway:** a clean-room pattern for the `BaseChannel` / `ChannelManager` shape.
- **OpenTag:** reference.
- Inbound channel messages are untrusted and get taint labels.

### 3.7 Observability

**Langfuse / LangSmith.** The principal decided (2026-10-04) to keep **OTel GenAI spans in local SQLite** as the source of truth and to **custom-clone** the capabilities: traces, evals, prompt management and datasets (LOCUS-375).

**langwatch/langwatch.** Apache-2.0 core plus a commercial `ee/`. Reasoning Engine B.V. (Netherlands). Usage stats are on by default (`DISABLE_USAGE_STATS=true`).
- Too heavy to embed: it needs ClickHouse, Postgres, Redis, at least 4 CPUs and 8 GB of RAM.
- **Custom clone** of its patterns (LOCUS-400):
  - Scenario simulations (a user-simulator agent plus a judge);
  - the prompt version and label model;
  - its taxonomy of online evaluators and guardrails.
- Optionally, an off-by-default OTLP export to a user's own LangWatch.

### 3.8 Models and inference

**JustVugg/colibri.** Apache-2.0. Author's country *unverified*. A pure-C engine that runs mixture-of-experts models by streaming experts from disk. It has OpenAI- and Anthropic-compatible endpoints and runs natively on Windows (needs CUDA 12.8 or later for the RTX 5070).
- **Reference only for now.**
- Its useful models are China-origin: GLM (Zhipu is on the Entity List), DeepSeek V4, Kimi, Qwen. Its only US model (OLMoE) is weak.
- On a 32 GB / 12 GB machine, expect **well under 1 tok/s**.
- Revisit as an out-of-process `ModelProvider` for overnight "frontier-quality" batch jobs, with a D-29 inspection for every model (LOCUS-366).

### 3.9 Documents and workspace

**dream-num/univer-workspace.** Apache-2.0 at the repository level, but **effectively non-FOSS in use**:
- it depends on about 95 `@univerjs-pro/*` packages and runs on a rotating 90-day license key;
- its agent layer is **DeepSeek Harness**, about 23 `@deepseek-ai/dsh-*` packages.

**Skip.** If a sheet or doc surface is needed later, evaluate only the Apache-2.0 `@univerjs/*` core (`dream-num/univer`) after a D-29 inspection (LOCUS-368).

**MiroFish.** **Skip.** It's a swarm-simulation engine, not a harness. It is China-origin with Shanda backing, and defaults to Alibaba DashScope cloud inference and the Zep Cloud memory service.

### 3.10 Trust kernel and first-party

**Cedar.** **Integrate as-is** behind the `PolicyEngine` port. The binding is chosen in phase 0; `cedarpy` is unofficial (D-30, ADR-0001, LOCUS-390 to 395).

**LATTIX-IO/savant.** First party. **Integrate as-is** as a `SkillStore` source connector:
- synced skills land quarantined, then go through scan and the eval gate;
- run telemetry is sent back to Skill Intelligence only if the user opts in.

It is blocked until the principal provides API access or docs, because the deployment is behind Vercel SSO (LOCUS-388).

## 4. Sequencing and dependencies

```text
P1  LOCUS-363 Deep Agents extension package ──► LOCUS-373 TrueForge context engineering (P2)
                                           └──► octop-harness middleware patterns (P3, LOCUS-371)
P1  LOCUS-407 Symphony SPEC alignment ───────► LOCUS-398 Multica/Orca orchestration UX (P2)
P1  LOCUS-369 AG-UI protocol + assistant-ui ─► LOCUS-409 generative-UI surface (P2)
                                           └──► LOCUS-408 channels (P3)
P1  LOCUS-367 Laya judge sidecar ────────────► Jev opt-in (P2) · Clef only if a weights-only artifact appears (P3)
P1  LOCUS-375 observability (OTel/SQLite) ───► LOCUS-399 RSI provenance (P2) · LOCUS-400 LangWatch patterns (P3)
P1  LOCUS-390 Cedar epic (D-30)
P2  LOCUS-354 always-on agent ◄── LOCUS-370 OpenBot / OpenDots / openmuse patterns
P2  LOCUS-351/382 scorecard + held-out ──────► LOCUS-365 Raven curator gate · LOCUS-399 experiment tree
P2  D-08 canvas decision (principal) ────────► LOCUS-397 canvas · LOCUS-364 Sim-style pipeline schema
P2  LOCUS-371 octop-browser / octop-memory clean-room patterns (needs LOCUS-387 SQLite store: done)
P3  LOCUS-401 diagram skill · LOCUS-366 colibri · LOCUS-368 univer core · LOCUS-372 ax · LOCUS-388 Savant (blocked on access)
```

## 5. Decisions needed from the principal

1. **D-08 canvas engine.** Keep diagram-js (recommended), or revisit D-08 to allow xyflow for Pipelines and Playbooks? This blocks LOCUS-397 and LOCUS-364.
2. **Clef on Workers AI.** Does hosted inference of a Qwen-derived model fall under the D-29 hosted exclusion? Recommended: yes. Locally, Clef is blocked anyway until a weights-only artifact exists (§3.3).
3. **assistant-ui vs `@copilotkit/react-core`** for the chat and run surfaces. Recommended: assistant-ui. The protocol is AG-UI either way.
4. **Savant access.** API docs or a token so LOCUS-388 can start.
5. **Deep Agents process boundary.** Keep the in-process D-27 runtime with the controls in §3.1 (the current state), or run it behind an A2A/ACP sidecar so it meets ARCHITECTURE-MODULES §5? The sidecar adds latency and work, so the recommendation is to keep it in process until the Cedar cutover (LOCUS-393), then re-assess.

## 6. Keeping this current

- **New repositories:** add a row in §2 and an entry in §3 using the same shape (facts → class → patterns → risks), and link a Linear child of LOCUS-406.
- **Re-assessment triggers:** a license change, a provenance finding, a release that removes a blocker (for example generative-ui gaining a license), or a pinned dependency's version change (D-29 inspections re-run per version).
- **Supersede, don't append:** when a decision changes, edit the row and note the date and reason.
