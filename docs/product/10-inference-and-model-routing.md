# 10 · Inference and Model Routing

Locus owns the task, context, memory and policy. **Engines** do the thinking. This document defines engine kinds, how each is integrated safely, and how the router picks one.

> **System One notices and sorts. System Two explains and plans. Code calculates. Humans decide what's irreversible.**

## 1. Engine kinds

| Kind | Examples | What Locus controls | What it doesn't |
|---|---|---|---|
| **Code (T0)** | Rules, lookups, policy, formulas, graph queries | Everything | — |
| **System One judge (T1)** | Laya (local, open weights), Jev (TypeSafe, hosted, optional) | Question, options, thresholds | Model internals |
| **Local model (T2/T3)** | Ollama, llama.cpp server, LM Studio, vLLM (OpenAI-compatible) | Prompt, tools, context, sampling | Quality ceiling |
| **API model (T2/T3)** | OpenAI, Anthropic, Azure OpenAI, Gemini, Mistral, others via SDK | Prompt, tools, context | Data handling at provider |
| **Vendor agent engine (T3)** | Codex CLI (ChatGPT plan or API key), Claude Code (Claude plan or API key); OpenCode (API keys or local models) | Workspace, sandbox, tool surface (via gateway), budget, approvals | The vendor's internal loop and model choice |
| **Context provider** | Microsoft 365 Copilot (Retrieval API, Chat API) | Query, scope, what enters run context | It answers; it doesn't act |
| **Human (T4)** | The principal, a peer | — | — |

## 2. Local inference

- **Runtimes:** Ollama (default, already integrated), any OpenAI-compatible local server (llama.cpp `llama-server`, LM Studio, vLLM on a GPU box on the LAN). Detected at first run and in Library → Engines.
- **Catalog and eligibility:** the curated download catalog is provenance-filtered (P28); live discovery independently lists all installed models, including custom `hf.co/...` identifiers. *Discovered* does not mean *admitted*: hosted P28-listed lineages remain denied, while a local model of a listed lineage requires a passing digest-bound provenance attestation under D-29. The eligible catalog also records license, actual capabilities, size, context window, tool-calling quality and measured speed on this machine.
- **Roles:** classification fallback, summarization, extraction, embeddings, private-area drafting, offline operation. Larger local models handle T3 work in areas that forbid hosted engines.
- **Note on OpenCode:** OpenCode is an open-source coding-agent harness, not an inference runtime. It is supported as a vendor-agent-style engine running on API keys or local models.

## 3. Subscription capacity: vendor agent engines

**Decision (D-04):** subscription consumption is used by driving each vendor's **own official client** as a sandboxed engine. Locus never extracts, proxies or reuses subscription OAuth tokens.

### Why

- **Anthropic:** since the February 2026 terms update, Claude Free/Pro/Max OAuth tokens may not be used in third-party tools, and server-side checks reject non-Claude-Code clients. Since April 2026, subscriptions don't cover third-party agent usage, and since June 2026 subscribers get metered Agent SDK credits for use outside Claude Code. **Open question O-01:** whether launching the official `claude` CLI headless from Locus draws on the plan, on Agent SDK credits, or requires an API key. Verify against current terms before H1 ships; the adapter supports all three billing modes and shows which is in use.
- **OpenAI:** Codex CLI supports ChatGPT-account and API-key sign-in through its official client. OpenAI additionally documents a consent-based *Sign in with ChatGPT* path for eligible locally running open-source tools, including a Codex app-server integration. This is **not** blanket permission for every Locus distribution, tenant, subscription or unattended workflow: eligibility, deployment/monetization restrictions and the exact OAuth permission must be verified before enabling. Under D-04 Locus does not extract or reuse the Codex CLI's own tokens; an SIWC adapter would be a separately reviewed, user-authorized provider with managed credentials, explicit billing provenance, and its own security decision. See [official SIWC guide](https://developers.openai.com/siwc/token-sharing-open-source), [Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server) and [preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
- **Microsoft 365 Copilot** is not a general model engine. See §4.

### How an agent engine runs

```
Locus Run
  └─ Engine adapter (claude-code | codex | opencode)
       ├─ launches the official CLI in the run workspace, inside the sandbox tier
       ├─ tool surface = Locus MCP gateway only; built-in shell, file and web tools
       │  disabled or confined to the workspace by the CLI's own permission settings
       ├─ CLI approval hooks → Locus approval cards (same R-classes)
       ├─ egress → per-run proxy (vendor API host allowed; everything else by envelope)
       ├─ events → normalized into the run timeline (steps, tool calls, tokens)
       └─ budget → wall time, turns and spend enforced by the adapter; kill on breach
```

| Requirement | Detail |
|---|---|
| Gateway parity | Every tool the CLI uses goes through Locus's MCP gateway, so grants, taint and audit apply as for native engines (P6) |
| Config isolation | The adapter writes a run-scoped config (permissions, MCP servers, hooks) rather than using the user's global CLI config |
| Login state | Read-only check of whether the CLI is logged in; Locus never reads token files |
| Version pinning | Adapters declare supported CLI versions; unsupported versions run in observe-only mode |
| Honest billing | The run's Spend tab states the billing mode (plan, plan credits, API key) |

## 4. Microsoft 365 Copilot

Role: **work-context provider** for areas tied to an M365 tenant.

- **Retrieval API (GA):** permission-trimmed retrieval from SharePoint, OneDrive and connectors, without exporting data. Locus uses it to ground runs ("find the latest SOW for Legend Biotech") and cites returned items.
- **Chat API (preview):** Copilot-grounded answers in custom apps. Answer-only, so it's used as an evidence source, never as a planner or actor.
- **Meeting Insights API (GA):** Teams meeting notes and action items, as an intake source for Triage.
- **Actions** in M365 (send mail, create events, post in Teams) go through Microsoft Graph tools in the gateway with delegated scopes, never through Copilot.
- **Licensing:** a Microsoft 365 Copilot licence per user plus an eligible M365 base subscription. The adapter reports when the tenant or user isn't entitled.
- **Data class:** Copilot answers inherit the area's classification and taint (tool output: untrusted).

## 5. System One: Laya and Jev

Typed judgments with calibrated probabilities, used as a **safety and speed layer**, not as a generator.

| Primitive | Returns | Locus uses |
|---|---|---|
| **Choice** | One of a known set, with probabilities | Area for an inbound item; intent routing (command vs chat vs task); engine tier; which skill fits |
| **Score** | Position on ordered levels | Urgency; action risk refinement; memory importance; source sensitivity |
| **Noul** | Probability a condition holds | "Does this text contain instructions aimed at an AI?"; "Does this action match the task goal?"; "Does this recipient belong to the task context?"; "Is this screen a login or payment form?" |

- **Laya** (open weights, Apache-2.0, ≈420M parameters, ONNX Runtime, ≈2 GB RAM) runs locally as the `judge` service and is the **default**. Constraints to design around: ~512-token state, ≤ ~20 options per choice, weak zero-shot until calibrated.
- **Jev** (TypeSafe, hosted) is an optional adapter for areas whose policy allows hosted inference, used where Laya is out of envelope (many options, low confidence).
- **Escalation ladder:** Laya → Jev (if allowed) → LLM structured output → human, gated by confidence and envelope checks.
- **Calibration:** shadow mode in H1 logs judgments beside user decisions (approvals, triage choices, memory reviews). Per-judgment temperature fitting and thresholds are set from that data before a judgment can gate anything on its own.
- **Never:** a judgment alone never authorizes an R3 action. It can *block*, *escalate* or *pre-fill*; only grants and humans authorize.

## 6. The router

Ask in order; the first yes picks the tier.

```
1. Can a rule, lookup, formula or policy answer it exactly?              → T0 Code
2. Is it a choice, yes/no or scale with code-supplied options and
   ≤512 tokens of state?                                                → T1 Laya (→ Jev)
3. Does it need new text or structure from a small context?             → T2 Local model (→ API model)
4. Does it need multi-step reasoning, tool use, coding or long context? → T3 API model or vendor agent engine
5. Is it irreversible, outbound or a change of meaning?                 → T4 Human (via approval)
```

Then apply **area policy** as a filter, never as a suggestion:

| Area policy field | Effect |
|---|---|
| `hosted_engines` | allow / deny / list of providers |
| `data_ceiling` | the highest data class that may be sent to each engine kind |
| `preferred_agent_engine` | for coding and long-horizon work (for example Codex for implementation, Claude Code for review) |
| `budget_defaults` | per-run spend and per-day caps per engine |
| `offline_ok` | whether local-only degradation is acceptable or the task should wait |

Routing decisions are recorded on the run, with the reason, so "why did it use Claude Code here?" has an answer.

## 7. Failure behavior

- Missing key, logged-out CLI, unreachable local server: the engine is **unavailable** and the router picks the next allowed engine or blocks the step. Simulated output is removed from production paths (P16).
- Engine errors are surfaced in the timeline with the provider's message (redacted).
- Quota exhaustion on a subscription engine pauses the run with a choice: wait, switch engine, or use an API key.

## 8. Open questions

- **O-01** Claude Code billing mode when invoked by Locus (see §3).
- **O-02** Whether Codex ChatGPT-login runs are permitted unattended (scheduled) or only while the user is present.
- **O-03** Default local T3 model for areas that forbid hosted engines, given the provenance filter and the hardware on hand.


## 9. Task routing versus engine routing (D-32, D-33)

The existing **model/engine router** is a subordinate capability selector: it chooses an eligible inference provider or executable agent engine after policy, cost and feature filtering. The new **Task Orchestrator** owns request interpretation, dependency DAG, ephemeral expert pod composition, independent verifier assignment, workload scheduling, recovery, and final synthesis. These must not become two competing privileged routers.

- **Inference provider:** OpenAI, Anthropic, Ollama, Azure OpenAI or compatible endpoint; features (tool calling, native API, streaming, reasoning, structured output, context window, multimodal, usage) must be individually negotiated and tested. OpenAI-compatible transport does **not** imply native Anthropic API parity.
- **Agent engine:** LangChain Deep Agents, Codex CLI, Claude Code, OpenCode; each has its own lifecycle and external-process security requirements. Codex CLI has a bounded local implementation already; parity for other engines is an acceptance requirement, not an existing claim.
- **Task routing:** model-assisted proposed expert composition is bounded by per-node data class, skills, memory projection, grants, budgets, device capability and independent output rubrics. Deterministic policy gates decide admission, not model text.
- **Security:** vendor-CLI tool access and direct inference routes, including localhost transports, need a documented mediation inventory. Unsupported capabilities fail closed or downgrade only within an explicitly approved envelope. Official-client authentication must not be exported or repurposed.

The initial executable task graph admission contract is `locus_runtime/task_graph_contract.py` v1.0. No client selection, model inference or agent process is launched by that module; see LOCUS-415/416. Existing pricing and unattended billing questions remain open.


## 10. Judge-routing precedence and provenance compatibility (D-36)

The existing **D-11 local Laya default remains active**. The October 5 preference for **Clef on Cloudflare Workers AI** is recorded in D-36 as a **conditional proposal, not an allowed provider**: Clef is Qwen-derived, and hosted Qwen-lineage inference is explicitly denied by `SECURITY.md`, P28/D-29 and `provenance_denial()` in `locus_runtime/model_client.py`. A US-hosted endpoint does not remove the lineage restriction. Do not implement Clef as a fallback, issue tokens, widen network egress or disable the provenance gate from this documentation decision. The principal must separately authorize a scoped security exception with a protected code/policy change, negative gateway/model-client tests, per-area consent, egress audits and rollback before considering Clef the primary judge.

The `DecisionModel` adapter continues to use the typed `/v1/systemone` protocol. Calibrated Laya can deny/escalate only; absent/uncalibrated offline judging pauses for principal review, never a synthetic permit. Any permitted hosted judge must independently satisfy the existing area policy and credential, redaction and metering controls. See [third-party assessment §3.3](../third-party-assessment.md#33-judge-and-decision-tier) and [D-36](20-roadmap-and-decisions.md).


## 11. Adaptive model selection contract (LOCUS-479; proposed)

**Status:** implementation target, not a claim that the v0.10.7 chat path implements it. The backend is the authoritative router; frontend selectors display a server-approved capability snapshot and request a route, but may not independently authorize a model call. The Task Orchestrator owns decomposition and specialist assignment (D-32/D-33); this router decides *which permitted model/engine serves each admitted specialist task*.

### 11.1 Authoritative live inventory

Merge sources into one normalized snapshot, with provider-typed entries and explicit freshness:

| Source | Discovery | Availability and limits |
|---|---|---|
| Ollama local | \`/api/tags\`, then bounded \`/api/show\` only when capability metadata is needed; \`/api/ps\` is optional | Installed exact ID, capabilities, digest, context, memory/VRAM fit, transport health. Never pull a model during discovery |
| Hosted API | User/platform provider configuration, supported model catalog and probe metadata | Key present is *configured*, not *authorized, in quota, or healthy*. Track provider limits, cost and recent failures separately |
| Codex/Claude Code/OpenCode | Supported official CLI presence, version, documented authentication/readiness interface | Vendor-agent engine, **not** an API-key-backed chat model. Do not read CLI token files or synthesize subscription access |
| Other local compatible servers | Explicit registered endpoint and bounded capability probe | Treat a LAN endpoint as network egress, not equivalent to loopback. No auto-discovery across arbitrary addresses |

Each record carries a stable \`provider_id\`, \`engine_kind\`, exact \`model_id\`, \`context_limit\`, \`capabilities\` (reasoning, tools, JSON/schema, vision, modalities), \`health\`, \`auth_state\`, \`quota_state\`, \`price_version\`, \`locality\`, \`provenance_state\`, and \`observed_at\`. Report unknown information as **unknown**, not as a positive capability or zero cost. Cache briefly with bounded probes and invalidate on provider settings changes, explicit refresh, 401/403/429, network failure, model deletion, or attestation change.

Present three different concepts in every selector: **installed/discovered**, **eligible for this run**, and **selected/active**. An installed but provenance-denied, out-of-budget, out-of-context or unhealthy model remains visible with its reason but cannot be admitted. An embedding-only model must not be selectable for chat; unknown custom families require actual capability negotiation or a conservative explicit operator declaration.

### 11.2 Eligibility before ranking

For each task/step, construct a server-side requirement vector: task category, expected reasoning depth, modality, tool and structured-output requirements, estimated input/output tokens, data classification/taint, latency objective, remaining run budget, hardware fit, explicit agent pin, and requested privacy/offline constraints.

Hard-filter candidates in this order:

1. Explicit provider/model pin semantics; supported engine and capability; installed or authenticated, currently runnable, and not in a quota/circuit-breaker cooldown.
2. Data-ceiling, area/tenant policy, P28/D-29 provenance, network egress, provider allowlists, and valid Gateway authorization *for each candidate*. A model's own selection must never confer tool or egress authority.
3. Context/output capacity (with tool and safety margin), resource/concurrency capacity, and budget admission (tokens, spend, wall time, retries). Unknown hosted price is not interpreted as free.
4. Routing consent scope (11.3), including whether changing from local to hosted, API key to subscription, or one vendor to another is approved.

If the resulting set is empty, return a typed \`no_eligible_engine\` decision and preserve the queued user message; do not substitute OpenAI or another provider, fabricate output, or start an unauthorized call.

### 11.3 Ranking and route explanation

After hard gates, use deterministic, inspectable ranking over **measured** suitability and availability: verified task-specific success rate, model/tool capability, reasoning/context adequacy, estimated latency, monetary and hardware cost, resource contention, and route reliability. Static name-based assumptions (e.g. "larger model is always better") are not sufficient evidence. Until enough benchmark data exist, use conservative labeled heuristics and produce a reason with confidence/unknown fields, not false precision.

Use a route plan rather than one global model string: \`task_id\`, \`requirements_version\`, \`policy_version\`, \`catalog_snapshot_id\`, ordered \`eligible_candidates\`, \`chosen_candidate\`, \`reason_codes\`, \`cost_ceiling\`, \`fallback_scope\`, and \`approval_reference\`. Pin the chosen engine for an execution attempt; re-evaluate on a meaningful runtime change or a new step, not arbitrarily mid-tool-call. Record revisions and fallback decisions without prompt or credential disclosure.

### 11.4 Permission-aware switching

Routing consent is **subordinate** to existing Zero Trust policy, budget controls, R-class approvals and area data ceilings. It is not a privileged alternative to them.

| Operator routing preference | May switch silently | Requires explicit approval |
|---|---|---|
| Ask before rerouting | Nothing beyond a retry of the same approved transport under bounded limits | Changing model, engine, billing source or egress |
| Auto within approved scope | Eligible same-boundary models within an already approved provider set and cost cap | New external destination, higher data exposure, increased spend ceiling |
| Trusted auto within approved envelope | Any *pre-authorized* eligible route within grants, data ceiling, time/cost cap and audited provider allowlist | Outside the envelope, high-risk actions, irreversible effects, policy exceptions |

An explicit model pin remains binding unless the operator separately enables fallback for that run/agent. A user may promote a one-use reroute or a narrowly scoped, revocable standing grant; record scope, expiry, cost ceiling and data classification. "Trusted" never disables the Gateway, sandboxes, provenance checks, human approvals, or network controls.

### 11.5 Send, queue and consent UX

The **Send** action should accept and durably queue the operator's message even when the last engine is unavailable, subject to ordinary authentication and message admission. The *execution* remains blocked until an eligible route is authorized. Surface a persistent \`Awaiting engine\` / \`Awaiting reroute approval\` status and a concise notification with current route, reason, proposed eligible alternative, locality, estimated cost, and choices to approve one attempt, change model, keep queued, or cancel. Do not rely on a transient toast as the only record.

When a permitted same-boundary fallback is automatic, show a nonblocking \`Routed to ...\` notice and a link to its audit trace. In strict mode, ask before switching; lack of an acceptable alternative is not permission to make a hosted call. Both same-run messages and follow-up runs must share this server-owned admission and consent behavior.

## 12. Error classification and bounded escalation (LOCUS-479)

| Failure class | Action |
|---|---|
| Temporary timeout, 429 rate limit, transient 5xx | Respect documented retry hints and exponential backoff; bounded same-engine retry; move to another already approved candidate if needed |
| Usage exhausted, account not eligible, invalid login/key | Mark that engine unavailable for its proper scope and cooldown; do not infer all API or plan capacity is exhausted. Choose only another explicitly admitted billing/provider path |
| Model missing, unsupported tools/modality/context, resource exhaustion | Refresh inventory and requirements; re-plan an eligible candidate. An explicit pin requires consent to change |
| P28/D-29 provenance denial, Gateway/policy denial, data-ceiling or budget denial | **Fail closed**; never use fallback to evade the denial. Surface policy/approval path rather than rerunning unchanged |
| Task rubric failed, structured output invalid, verifiable quality below threshold | Retry with repaired context or increased supported reasoning budget, then consider a stronger **eligible** model/agent engine; require independent verification and bound all attempts |
| Any partial external side effect | Stop autonomous replay until idempotency receipts and current state are reconciled; new model may reason, but cannot blindly repeat the action |

The default escalation ladder is **same eligible model, bounded correction → increased reasoning effort if supported → alternative eligible model/engine → human**. A feedback message such as "think harder" may trigger a new route decision; preserve original task criteria and prior evidence. Do not route by an uncalibrated model's self-reported confidence alone. Use executable tests, schema assertions, citations/source checks, deterministic task-specific rubrics, and a separate permitted verifier where available.

Record each attempt with \`decision_id\`, \`failure_class\`, \`source_engine\`, \`destination_engine\`, \`reasoning_budget\`, \`permission_decision\`, \`verification_result\`, \`tokens\`, \`cost\`, and \`side_effect_checkpoint\`. Enforce per-step retry, total run cost and wall-time ceilings.

## 13. Implementation order and release acceptance (LOCUS-479/480)

1. **P0 — close inventory parity.** Qualify LOCUS-480/PR #147. Ensure all chat surfaces consume the live local inventory, not only provider credentials; exclude non-chat models. Installed Windows owner UAT verifies all seven installed IDs, custom \`hf.co/...\` entries, offline removal and actual local inference. A model denied by provenance stays visible but cannot execute.
2. **P0 — authoritative route admission.** Create one backend route-preview/admission contract consumed by task kickoff, same-run and follow-up composers; honor \`Auto\` as a server-side decision, never empty string → OpenAI. Route within local installed policy-eligible models without hosted credentials, and show selection provenance. Keep explicit model pins.
3. **P1 — consent and availability.** Integrate health/quota/cooldown/billing-source tracking, policy-enforced fallback and queued-message reroute approval through existing grant/approval infrastructure. No implicit local→hosted promotion, no unauthorized CLI subscription use.
4. **P1 — verification and escalation.** Introduce task-specific acceptance rubrics, bounded reasoning escalation and independently verified fallback results; track effectiveness and the full retry budget.
5. **P2 — vendor-agent parity.** Enable supported and explicitly consented Codex/Claude Code/OpenCode CLI adapters only after separate sandbox/tool mediation and current vendor billing/auth verification. ChatGPT SIWC eligibility is a separate, opt-in architecture decision, not a credential shortcut.

**Minimum regression matrix:** installed-only Ollama; missing/stale configured default; embed-only inventory; custom model ID; P28 listed-lineage without attestation; signed local attestation; missing hosted key; eligible hosted-only; explicit pin; no candidates; plan/API quota and 429; provider outage; context/capability mismatch; unknown price; R-class/high-data-denial; strict confirmation; bounded within-boundary auto switch; failed structured output/quality; partial tool effects; close-and-resume pending approval; trace/cost/secret-redaction; Windows native task, same-run reply and follow-up.

**Ship gate:** no claim of working Auto until a real Windows Locus run chooses an installed admitted model, makes a gated inference call, preserves run context, reports actual provider/model and usage, completes an independently verifiable artifact, and shows an auditable authorized fallback. All relevant policy, backend and frontend tests and protected CI gates must pass; retain an explicit rollback switch for the new router.
