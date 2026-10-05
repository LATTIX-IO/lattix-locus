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
- **Catalog:** a curated, provenance-filtered list (P28): for example gpt-oss, Llama, Gemma, Mistral, Phi and Granite families. Qwen, DeepSeek, Yi, GLM and Kimi families are excluded. The catalog records license, size, context window, tool-calling quality and measured speed on this machine.
- **Roles:** classification fallback, summarization, extraction, embeddings, private-area drafting, offline operation. Larger local models handle T3 work in areas that forbid hosted engines.
- **Note on OpenCode:** OpenCode is an open-source coding-agent harness, not an inference runtime. It is supported as a vendor-agent-style engine running on API keys or local models.

## 3. Subscription capacity: vendor agent engines

**Decision (D-04):** subscription consumption is used by driving each vendor's **own official client** as a sandboxed engine. Locus never extracts, proxies or reuses subscription OAuth tokens.

### Why

- **Anthropic:** since the February 2026 terms update, Claude Free/Pro/Max OAuth tokens may not be used in third-party tools, and server-side checks reject non-Claude-Code clients. Since April 2026, subscriptions don't cover third-party agent usage, and since June 2026 subscribers get metered Agent SDK credits for use outside Claude Code. **Open question O-01:** whether launching the official `claude` CLI headless from Locus draws on the plan, on Agent SDK credits, or requires an API key. Verify against current terms before H1 ships; the adapter supports all three billing modes and shows which is in use.
- **OpenAI:** Codex CLI supports ChatGPT sign-in and API-key sign-in. ChatGPT sign-in is interactive by design and not meant for headless CI, so Locus relies on the user's own interactive login on their own machine and falls back to an API key for unattended runs if the plan path is disallowed.
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
