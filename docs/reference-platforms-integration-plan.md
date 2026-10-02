# Reference Platforms — Integration & Adoption Plan

Status: PROPOSED · 2026-06-11
Reviewed: `E:\lattix\ref_repos\open-webui`, `E:\lattix\ref_repos\langflow`, `E:\lattix\ref_repos\dify`

## Skills maturity & testing — Savant alignment (2026-06-11)

The skills feature was first modeled on Symphony's SKILL.md format (procedure text
only). The actual skills testing/maturity solution is **Savant** (`D:\lattix\savant`):
a governed platform where skills carry a **maturity tier** (tier1 standards /
tier2 methodology / tier3 personal+workflow), a per-skill **eval harness**
(rubrics, datasets, fixtures, baselines, runs), **governance** (owners,
dependencies, routing-policies in registry YAMLs), and a review→release lifecycle,
with tenant skill packages (`SKILL.md` + `metadata.yaml` + `agents/` + `eval/`)
living in external Git and indexed into a control-plane DB.

Ported into xFrontier's existing skills (✅ done): `tier`, `maturity`
(draft→incubating→validated→standard), `owner`, `dependencies`, an `eval_rubric`
+ `eval_dataset`, and `POST /skills/{id}/eval` (runs each case, LLM-judge grades
vs rubric → averaged score, ≥0.7 passes and earns `validated`) + a promotion-gated
`POST /skills/{id}/promote`. UI: tier/maturity/eval columns in the inventory and a
maturity & evaluation panel (rubric, dataset, Run eval, Promote) in the build page.

Skill import + security blast chamber (✅ done 2026-06-11): skills can be imported
from an online source (`POST /skills/import` — URL with an SSRF guard rejecting
private/loopback/reserved hosts, or pasted content) and land **disabled +
quarantined**. Because skills are prompt procedures (not executable code), the
"blast chamber" is the guarded LLM path: a static content scan (the platform's
FOSS guardrail signals — prompt-injection/exfiltration/command-injection/PII +
secret patterns) then a guarded dry-run whose output is scanned through the same
signals. High-severity findings block; a skill stays quarantined until both stages
clear (`POST /skills/{id}/scan` re-runs it). `save_skill` refuses to enable a
pending/blocked skill and `_augment_system_prompt_with_skills` never injects one.

Deferred Savant pieces (need product decisions): external tenant-Git storage with
the repo contract + validation, governance registry files, signed bundles, and the
review/release control-plane workflow.

## License posture (governs everything below)

| Platform | License | What we may do |
|---|---|---|
| Open WebUI | **MIT** | Port code directly (retain copyright notices) or run as a service. Safest source for direct code lifts. |
| Langflow (+ `lfx` engine) | **MIT** | Port code directly; `lfx` is a standalone MIT Python package usable as an embedded library. |
| Dify | **Modified Apache 2.0** | Additional conditions: no multi-tenant offering built on their code without a commercial license, and their `web/` frontend cannot be rebranded. xFrontier has multi-workspace ambitions → **treat Dify as a design reference only; reimplement patterns, copy no code.** Their separate `dify-sandbox` repo is licensed independently (verify before use). |

## Where xFrontier already stands (don't re-buy what we own)

Multi-provider OpenAI-compatible routing (8 providers incl. NIM + Ollama), local model catalog with managed pulls, skills registry with test bench + usage metrics, integrations catalog (MCP/API metadata), ReactFlow studio for agents/workflows with revisions/publish, SSE run streaming, approval flows, OIDC auth, audit log, sectioned persistence, egress-proxied sandbox topology (squid ≙ Dify's `ssrf_proxy` — independent validation of our design).

## The gap analysis → what each reference solves

### 1. MCP runtime invocation — *the* gap (source: Open WebUI)
Our integrations catalog stores MCP connection metadata but agents cannot invoke MCP tools at runtime. Open WebUI ships a working MIT-licensed MCP client (`backend/open_webui/utils/mcp/client.py`) and a tool-invocation loop (serialize tools → model returns tool_call → dispatch → feed results back). Dify's taxonomy (`api/core/tools/`: `builtin_tool` / `custom_tool` (OpenAPI) / `mcp_tool` / `workflow_as_tool`) is the right *shape* for our tool registry.
**Action: port the Open WebUI MCP client + tool-call loop into the run executor; adopt Dify's tool taxonomy as our registry schema (reimplemented).**

### 2. Declarative component schema for the studio (source: Langflow)
Langflow components declare typed inputs as data (`DropdownInput(options=…, refresh_button=True)`, `SecretStrInput`, `SliderInput(range_spec=…)`, `advanced=True`, `real_time_refresh`) — the UI renders forms, validates edge types, and refreshes model lists from this schema alone (`src/lfx/src/lfx/components/anthropic/anthropic.py` is the canonical example). Our node definitions carry far less metadata, so the studio's node config UX is hand-rolled per node.
**Action: adopt the typed-input schema for our node-definitions API; render studio config panels generically from it. Stretch: per-integration "bundles" packaging (`src/bundles/*`) as the model for our future plugin packaging.**

### 3. Embedded flow execution engine — evaluate, don't decide yet (source: Langflow)
`lfx` is Langflow's MIT execution engine as an importable package (graph compile/run, per-node events, caching). Embedding it could replace our bespoke graph executor for complex flows while we keep our own UI/policy layers.
**Action: time-boxed spike — run one xFrontier workflow graph through embedded `lfx`; measure footprint (resource budgets apply) and policy-hook compatibility before committing.**

### 4. Knowledge/RAG module (sources: Open WebUI code, Dify design)
We have pgvector long-term memory but no document-knowledge product surface. Open WebUI's pluggable **vector-DB factory** (`retrieval/vector/factory.py`, 14 backends) and document loaders are MIT and portable nearly as-is. Dify's pipeline staging (`api/core/rag/`: extractor → cleaner → splitter → index_processor → retrieval → rerank → data_post_processor) is the right architecture to mirror.
**Action: build `frontier_runtime` knowledge module: port Open WebUI's vector factory + 2–3 loaders (PDF/web/markdown), structure stages Dify-style, surface citations in the run console, knowledge collections under builder nav.**

### 5. Triggers & workflow-as-API (source: Dify design)
Dify workflows start from `trigger_webhook` / `trigger_schedule` / `trigger_plugin` nodes; Langflow exposes any flow as an authenticated API endpoint. We have neither — runs start only from the UI.
**Action: add webhook trigger endpoints (per-workflow token-authenticated URL → creates a run) and a scheduler loop for cron triggers; later, "workflow-as-tool" so workflows compose like Dify's.**

### 6. Chat depth (source: Open WebUI)
Conversation branching via a `reply_to_id` message tree (regenerate/edit from any point, branch navigation) vs our follow-ups-create-new-runs model; artifacts canvas for rendered outputs.
**Action: adopt the parent-id event-tree pattern in run events to support regenerate/branch inside one run; artifacts canvas later.**

### 7. Provider management polish (sources: Open WebUI, Dify design)
Per-model parameter defaults, group-based per-model access control, credential **"Test connection"** validation on save, multi-key load balancing per provider (Dify).
**Action: near-term, add a Test button per provider (we already list models — reuse that call as the health probe); per-model ACLs ride on our existing roles later.**

### 8. Sandboxed code execution (sources: Open WebUI, Dify topology)
Open WebUI's `code_interpreter` (MIT) and Dify's dedicated sandbox container both isolate code execution as a service. We have bubblewrap in agent images but no code-execution tool for agents.
**Action: when code-execution tools land, follow the dedicated-sandbox-service topology; port Open WebUI's interpreter as the starting point.**

## Phased plan

| Phase | Scope | Effort | Sources |
|---|---|---|---|
| **A — Tools become real** | ✅ DONE 2026-06-11 — MCP HTTP/SSE client (`app/mcp_client.py`), tool-call loop in `_run_openai_chat` (max-calls cap, per-call run events), `_gather_mcp_run_tools` (configured-only, locality + high-risk + block-tool-calls gating, env:/Vault secret resolution), provider Test button in settings | done | Open WebUI (port), Dify (design) |
| **B — Studio component schema** | ✅ PARTIAL 2026-06-11 — `NodeFieldSpec` declarative typed-input schema on node definitions (9 node types populated: dropdown/text/textarea/number/slider/bool/secret/code, with `options_source` for live lists, advanced/required/bounds); generic `NodeFieldForm` renderer surfaced read-only on `/builder/nodes`. Remaining: wire `NodeFieldForm` into the studio canvas inspector (replace hand-coded per-node config) and edge type-checking. | partial | Langflow (pattern) |
| **C — Knowledge module** | ✅ PARTIAL 2026-06-11 — knowledge collections (create/list/delete), paragraph-aware chunker, document ingestion (embed-on-write) + semantic search reusing the pgvector long-term store; builder Knowledge page with ingest + retrieval test. Secure-stack postgres switched to pgvector image. **Memory layers + RAG-via-integrations (2026-06-11):** `/knowledge/memory-layers` reports honest status+stats for all four tiers (short-term/Redis, long-term/pgvector, world-graph/Neo4j, knowledge); `/knowledge/vector-stores` lists the built-in pgvector backend plus any `type=vector` integration; collections bind to a `vector_store_id` (default `platform`), ingest enforces the binding (501 driverless / 503 store-down); page shows a layers panel + vector-store selector linking to builder/integrations. Remaining: external vector-store drivers (Qdrant/Pinecone/etc.), file/PDF loaders, citation surfacing in the run console, rerank. | partial | Open WebUI (port), Dify (design) |
| **D — Triggers & run-as-API** | ✅ DONE 2026-06-11 — webhook trigger tokens (create/list/revoke; token shown once; `POST /triggers/webhook/{token}` runs as owner via `request.state` pre-auth) AND cron schedule triggers (dependency-free 5-field matcher in `app/cron.py`; `_scheduler_loop` daemon ticks 30s with minute-key dedupe; CRUD + toggle endpoints; `FRONTIER_SCHEDULER_ENABLED` flag for multi-replica). Both reuse the unchanged `create_workflow_run` (scheduler via a request shim) so guardrails/executor/audit apply. Triggers + schedules manager UI on the workflow detail page. | done | Dify/Langflow (design) |
| **E — Chat depth** | ✅ PARTIAL 2026-06-11 — Regenerate action on the run console (re-runs the original task as a linked run, reusing the resolved agent). Remaining: in-run event-tree branching navigation, artifacts canvas. | partial | Open WebUI (pattern) |
| **F — Spikes** | Embedded `lfx` executor; dify-sandbox license check + trial | 3–5 days each | Langflow, Dify |

Recommended order: **A → B → C**, then D/E by product priority. Every port from MIT code retains upstream copyright notices in the file header; nothing is copied from the Dify repo.

## What we deliberately do NOT integrate

- **Running Open WebUI or Dify alongside xFrontier as the chat/builder surface** — they overlap our core product; two UIs would fork the experience.
- **Dify code in any form** — license risk for our multi-workspace direction; design reference only.
- **Langflow as a deployed service** — its builder duplicates ours; the value is the component schema and possibly the embedded `lfx` engine.
