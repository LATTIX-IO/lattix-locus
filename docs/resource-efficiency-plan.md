# Resource Efficiency Plan — Lattix xFrontier

Status: PHASE 0–1 IMPLEMENTED (except 1.2 agent-container collapse and 1.5 module split) · 2.2 SSE BRIDGE IMPLEMENTED · 2.1 BOUNDED IN-PROCESS POOL IMPLEMENTED (external worker pending 2.3) · remaining Phase 2–3 PROPOSED · 2026-06-10
Driver: local-first deployments must run comfortably on a 32 GB developer/operator machine while keeping the full security guardrail posture (PII scanning, policy enforcement, signed A2A, audit). A full local test run recently exhausted 32 GB of RAM; this plan removes that entire class of failure.

## Guiding principles

1. **Measure before rewriting.** Every phase starts with a baseline and ends with a budget check. No Rust rewrite is approved without a profile showing the Python implementation is the bottleneck.
2. **Security features are non-negotiable** — we make them cheaper, never weaker. Each optimization must preserve guardrail/audit/policy behavior bit-for-bit (shared contract tests).
3. **Local-first is the constraint, not the cloud.** The budget target is the smallest supported machine, with the full stack remaining available for server deployments.
4. **Every Rust component keeps a pure-Python fallback** behind a feature flag until it has burned in (one release minimum).

## Resource budgets (acceptance criteria)

| Target | Today (measured/estimated) | Budget |
|---|---|---|
| Local-first idle RSS (backend+frontend+stores) | ~6–9 GB (Neo4j JVM ~1.5 GB, Presidio/spaCy ~0.8–1 GB, Next dev ~1–2 GB, backend ~0.5 GB, Postgres ~0.3 GB) | **≤ 2 GB** |
| Containers in `make local-up` | 6 (incl. Neo4j) | **≤ 4** |
| Cost per mutation (persistence) | O(total store) JSON dump → single Postgres row | **O(changed entity)** |
| Backend cold start | ~8–10 s (15.8k-line module + Presidio import) | **≤ 3 s** |
| Guardrail scan p95 (input+output) | Presidio/spaCy path, 100s of ms | **≤ 5 ms deterministic tier** |
| Test run worker processes | capped at 2 forks / 1 GB heap (done 2026-06-10) | keep |

---

## Phase 0 — Measure & guard (days) — ✅ done 2026-06-10

- **Baseline script** (`scripts/resource-baseline.ps1` / `.sh`): `docker stats --no-stream` + per-process RSS for backend/frontend/stores, written to `docs/perf/baselines/`. Run manually — never as part of the full test suite on dev machines.
- **Container memory limits** in both compose files (`mem_limit` per service). A leak then OOMs one container instead of the host.
- **Profiling kit**: `py-spy`/`memray` for backend, `next build --profile` for frontend. Document one-liners in `docs/perf/README.md`.
- Already done: vitest capped at 2 forks × 1 GB heap (`apps/frontend/vitest.config.ts`); never run full suites locally (memory: `resource-constrained-local-testing`).

## Phase 1 — Quick wins in the current stack (1–2 weeks, no rewrites)

> Implementation status (2026-06-10): 1.1 ✅ (sectioned change-detecting persistence + append-only `frontier_audit_events` table), 1.2 ✅ for Neo4j-off-by-default (`graph` profile; agent-container collapse pending), 1.3 ✅ (Presidio import + engine fully lazy), 1.4 ✅ (`make frontend-serve`), 1.5 partial (audit moved to append-only table; `main.py` module split pending).

**1.1 Fix per-mutation full-store persistence (highest impact).**
`_persist_store_state()` serializes every definition, revision, run, and event into one JSON blob and upserts a single Postgres row on *every* mutation (`apps/backend/app/main.py:10319`, `platform_services.py:137`). Replace with:
- per-entity tables (or per-entity JSONB rows keyed by `state_key = entity_type:id`) and dirty-flag writes;
- run events become append-only inserts (they are already immutable);
- a debounced (e.g. 500 ms) background flusher for low-value counters.
Cost drops from O(total state) per request to O(changed row); also removes the growing GC/alloc churn in the API process.

**1.2 Make heavy optional services actually optional locally.**
- **Neo4j off by default** in `docker-compose.local.yml` (world-graph projection is already feature-flagged) → saves ~1.5 GB JVM.
- **Jaeger, Envoy, Casdoor** stay out of the local profile (compose `profiles:`); local auth uses the existing local-password path.
- Collapse the three `agent-*` containers into **one worker process** with topic routing; per-agent OS isolation only when the sandbox profile demands it.

**1.3 Tiered guardrail scanning (cheap path first).**
Make the deterministic tier (keyword/regex/Luhn/entropy detectors) the default local scanner and **lazy-load Presidio** only when a ruleset explicitly requires NER-grade PII detection. Saves ~0.8–1 GB RSS and most of backend cold-start. This also creates the seam for the Phase 3 Rust engine.

**1.4 Frontend: run production server locally.**
`next start` from the existing `output: "standalone"` build for operator use; the webpack/React-Compiler dev server (1–2 GB) is for active UI development only. Add `make frontend-serve`.

**1.5 Micro-fixes in the API process.**
- `store.audit_events.insert(0, …)` and `inbox.insert(0, …)` are O(n) memmoves on every event → switch to `deque(maxlen=…)` / append + reversed reads.
- Split `main.py` (15.8k lines) into routers/modules — import cost, memory locality, and it unblocks every later extraction.
- Run-event lists already cap at 5000 in `frontier_runtime`; apply the same cap in the backend store.

## Phase 2 — Architectural efficiency (2–6 weeks)

**2.1 Single worker queue instead of in-request execution.** — 🟡 in-process half done 2026-06-10
Run execution now goes through a dedicated bounded `ThreadPoolExecutor` (`FRONTIER_WORKER_CONCURRENCY`, default 2, clamped 1–16; clean shutdown via app shutdown hook) instead of FastAPI `BackgroundTasks`, so run bursts queue instead of consuming the API server's shared request threadpool. Remaining: extract this seam into an external NATS-consuming worker process — which requires the SQL-canonical store (2.3) so worker and API share state.

**2.2 SSE event bridge.** — ✅ done 2026-06-10
`GET /workflow-runs/{run_id}/events/stream` (async generator — no worker thread held while idle; terminal self-close; 5-min rotation with `?after=` cursor reconnect). The run console streams via fetch-based SSE (fetch carries the identity headers EventSource cannot) and automatically downgrades to the interval-polling path on any transport failure. One idle connection per open console instead of 2 requests/3 s.

**2.3 Canonical store = SQL, memory = cache.** — 🟡 SQLite backend done 2026-06-10
`SQLiteStateStore` + `SQLiteAuditLog` (stdlib sqlite3, WAL, lock-guarded shared connection) implement the same sectioned-store and audit interfaces as the Postgres classes. Opt in by leaving `POSTGRES_DSN` unset and setting `FRONTIER_SQLITE_STATE_PATH=.frontier/state.db` — the backend then persists durably with **zero containers**. Remaining: invert `InMemoryStore` into a read-through cache over row-level CRUD (prerequisite for the external worker in 2.1).

**2.4 Embedded policy evaluation.**
Evaluate [regorus](https://github.com/microsoft/regorus) (Rust Rego engine, Python bindings) embedded in the backend for local mode, eliminating the OPA sidecar + network hop. Keep OPA sidecar for cluster deployments; identical `.rego` files, shared `make policy-test`.

## Phase 3 — Rust where it actually pays (6–12 weeks, incremental)

Decision matrix — a component qualifies for Rust only if it is **(a)** CPU-bound or long-running with RSS pressure, **(b)** on the request hot path or a security boundary, and **(c)** has a stable contract:

| Candidate | Verdict | Rationale |
|---|---|---|
| **Guardrail/PII scan engine** | ✅ **#1 — do it** | Runs on every input/output. Rust crate (`aho-corasick`, `regex-automata`, deterministic detectors: Luhn, entropy, format validators) shipped as a **PyO3/maturin wheel** (`frontier-guard`). Expected: ≥10× throughput, ~10 MB RSS vs ~1 GB Presidio path. Presidio remains the optional deep-scan tier. |
| **Local gateway (egress allowlist + A2A signature/nonce verification)** | ✅ **#2** | Consolidates `local-gateway` + `sandbox-egress-gateway` + local Envoy duty into one small Rust proxy (hyper/tower). One ~15 MB static binary replaces 2–3 containers; also the natural Windows-sandbox egress chokepoint (FRONT-13). |
| **Worker runtime / event bus daemon** | 🟡 later | Right shape for Rust (long-running, concurrency, budget enforcement), but only after Phase 2.1 stabilizes the contract. Re-evaluate with profiles. |
| **Policy engine** | 🟡 via regorus | Don't write our own — embed the existing Rust Rego engine (Phase 2.4). |
| **JWT/crypto/hashing** | ❌ no | Already C-backed in Python libs; no win. |
| **FastAPI control plane (CRUD)** | ❌ no | I/O-bound, changes weekly, Python productivity wins. |
| **Next.js UI / LangGraph orchestration** | ❌ no | Ecosystem value dominates. |

**Delivery pattern for each Rust component**
1. Freeze the contract with a golden test corpus generated from the Python implementation.
2. Build the crate + PyO3 bindings; CI builds wheels for win/mac/linux (feeds the FRONT-35 installer).
3. Ship behind `FRONTIER_NATIVE_GUARD=1` (etc.) with automatic fallback to Python on import failure.
4. Run both engines in shadow mode for one release (Rust result authoritative, Python result compared and logged on divergence).
5. Remove the flag; keep the Python path as the documented fallback for exotic platforms.

## Sequencing & risks

- Order: **0 → 1.1/1.2/1.3 (parallel) → 1.4/1.5 → 2.1 → 2.2/2.3 → 2.4 → 3.**
  Phase 1 alone should land the ≤2 GB idle budget; Phase 3 buys headroom + startup time + container count.
- **Risks:** PyO3 wheel build matrix on Windows (mitigate: maturin CI + Python fallback); behavior drift in guardrails (mitigate: golden corpus + shadow mode); SQLite concurrency in zero-container mode (mitigate: single-writer worker, WAL mode).
- **Verification discipline:** all perf claims verified with the baseline script and *focused* tests only — never full-suite runs on developer machines.

## Linear mapping

- Phase 1.1/2.3 → FRONT-2 (Workflow Engine Hardening: resumable runs need real persistence anyway)
- Phase 1.2/2.1 → FRONT-39 groundwork (per-tenant quotas need the worker knob)
- Phase 3 gateway → FRONT-13 (Sandbox & Tool Execution Safety)
- Phase 3 wheels → FRONT-35 (Installer & Self-Hosted Deployment)
- Cut `FRONT-*` stories per phase line item; each carries its budget as the acceptance criterion.
