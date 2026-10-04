# Lattix Locus

Lattix Locus is a secure, local-first multi-agent orchestration platform licensed under the GNU Affero General Public License v3.0-or-later (AGPLv3+). It pairs a **zero trust security core** with a **cortical-column ("Thousand Brains") cognitive model** so that agentic work is both contained by default and reasoned about by many independent models rather than a single prompt loop.

This README leads with the methodology, then a quick start (Kubernetes + desktop install on macOS, Windows, and Linux), and only then dives into the architecture and implementation.

> Lattix Locus is an independent project created by Lattix. The Lattix Locus name, ideas, and product direction were developed independently by Lattix.

> **Formerly xFrontier.** Existing installs upgrade in place; see [`docs/UPGRADING-TO-LOCUS.md`](docs/UPGRADING-TO-LOCUS.md).

---

## Methodology

Locus is built on two convictions: that an agent platform must **trust nothing implicitly**, and that reliable machine reasoning comes from **many independent models reaching consensus**, not from one large context window.

### Zero trust core

Zero trust in Locus is not a slogan layered on at the edge — it is a design style that recurs at every major boundary. Identity, scope, or capability is checked *before* work proceeds, and the default posture is fail-closed.

- **Route-level access classification.** Every backend route is assigned one of `public-minimal`, `authenticated-read`, `authenticated-mutate`, or `internal-only`, and the inventory is validated at startup so no endpoint can appear without an access class.
- **Authenticated operator sessions.** Protected UI does not render its data surfaces until an operator session is resolved and authenticated. Secure-local installs default to OIDC-backed auth and disable unsigned header-only actor trust.
- **Signed agent-to-agent transport.** Cross-service traffic carries bearer JWTs, signed runtime headers, subject identity, and nonce-based replay protection instead of relying on local-network trust. HTTPS is required in the `hosted` profile.
- **Scope-checked memory.** Memory is partitioned by scope (`run`, `session`, `user`, `tenant`, `agent`, `workflow`, `global`). The scope label is never trusted by itself; the backend normalizes it, validates the bucket prefix, and authorizes by actor identity, tenant claim, or collaboration membership.
- **Deny-by-default isolation.** Tool execution runs through a sandbox with explicit allowlists for executables, read/write paths, network, and destination hosts. Sensitive subpaths (`.git`, `.ssh`, `.gnupg`, `.aws`, `.azure`, `.kube`) are re-protected even inside a writable parent.
- **Middleware-enforced tenant consistency.** The event bus only delivers envelopes that survive middleware checks; a message can be dropped before any agent handler sees it if the runtime context is inconsistent with policy.

See [`THREAT-MODEL.md`](THREAT-MODEL.md) for the canonical trust-boundary reference.

### Cortical column ("Thousand Brains") cognitive model

Locus's reasoning model is inspired by the **Thousand Brains theory of intelligence**: intelligence emerges from many cortical columns, each building its own model of the world from its own evidence, voting toward a shared conclusion. Translated into the platform, this means agents become *coordination shells* and the actual reasoning is distributed across **cognitive columns** that are fused by **explicit consensus**.

- **Columns** are independent reasoning units. Each maintains its own belief state, evidence references, and confidence — and the platform deliberately keeps them independent so failures are not correlated.
- **Assemblies** are task-specific coalitions of columns with a defined inference mode, consensus policy, and stopping condition.
- **Consensus** fuses column beliefs with weighted support, tracks dissent, and emits a **commitment**: a decision plus confidence, supporting/dissenting columns, blockers, and next actions.

The target state is a distributed cognitive system that maintains multiple independent models of a task, reasons over state, evidence, prediction, and evaluation, and adapts over time under bounded, inspectable cognition — *not* a better prompt-orchestration tool. See [`docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md`](docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md) for the full target spec.

**What ships today** is an additive, bounded **cognitive MVP** — the first slice of that columnar architecture. It adds four graph-native node types without replacing the existing agent runtime:

- `locus/goal` — explicit goal framing
- `locus/evidence` — evidence capture and missing-evidence detection
- `locus/assembly` — bounded weighted-support assembly fusion
- `locus/commitment` — commitment generation with confidence, blockers, dissent, and next actions

Legacy graphs continue to validate and run, and `locus/agent` semantics are unchanged. Advanced columns (Evaluation, Uncertainty, State, Decomposition, Prediction, Adaptation) are planned but not yet part of the shipped slice.

---

The cortical column runtime follows the same zero-trust control-plane model. Column messages, assembly definitions, runtime steps, commitments, and causal graph projections are treated as untrusted until the backend admits them through signed-message verification, tenant ownership checks, column capability policy, assembly/runtime policy gates, replay/idempotency controls, redaction, and deployment-profile validation. The detailed architecture and runbook are in `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, and `THREAT-MODEL.md`.

## Quick start

Pick the path that matches how you want to run Locus. All three converge on the same control plane and secure defaults.

### A. Kubernetes (Helm)

The Helm chart is pinned to the `hosted` runtime profile and deploys the control-plane workloads (`lattix-api`, `lattix-orchestrator`, `lattix-envoy`, `lattix-opa`, `lattix-vault`, `lattix-nats`, `lattix-postgres`, `lattix-jaeger`).

```bash
# Replace the placeholder A2A_JWT_SECRET in the values file before applying.
helm install lattix ./helm/lattix-locus -f helm/lattix-locus/values-prod.yaml
```

The chart wires `A2A_JWT_SECRET` into the API/orchestrator paths so hosted clusters enforce the same signed runtime-header contract as the backend profile tests. `values-dev.yaml` is available for non-production clusters.

### B. Desktop app (macOS · Windows · Linux)

The Tauri desktop shell is a thin, auditable wrapper that spawns a single packaged backend supervisor, which brings up every local service (Postgres + pgvector, Neo4j world models, NATS, Ollama, the confined agents, the FastAPI backend, and the Next.js frontend) — **with no Docker** — then opens a webview once `/healthz` is green.

Download the signed installer for your OS from the project releases and run it:

| OS | Installer |
| --- | --- |
| **Windows** | `.msi` or `.exe` (NSIS) — Authenticode-signed |
| **macOS** | `.dmg` / `.app` — Developer ID signed + notarized |
| **Linux** | `.deb` or `.AppImage` |

First launch performs a one-time fetch of vendored runtime binaries, then drops you into a working multi-agent console. Updates arrive as a one-click **Update & Restart** banner. To build the desktop app from source, see [`apps/desktop-tauri/README.md`](apps/desktop-tauri/README.md).

### C. Local stack (bootstrap installer)

For a full local-first stack on your own machine, run the public bootstrap installer. It pulls vetted `main` content, installs the `lattix` CLI, updates your user `PATH`, and auto-starts the secure stack.

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/LATTIX-IO/lattix-locus/main/install/bootstrap.sh | sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -c "iwr https://raw.githubusercontent.com/LATTIX-IO/lattix-locus/main/install/bootstrap.ps1 -UseBasicParsing | iex"
```

Then start, open, and check the stack:

```bash
lattix up        # auto-starts the secure full stack
lattix health    # API health check
```

On clean machines the bootstrap detects your OS and installs Python 3.12+ and Docker prerequisites automatically when it can, refreshes the current shell `PATH`, and then continues into the interactive installer. The installer itself uses a managed virtual environment under the install root, so local installs do not depend on mutable system or Homebrew Python package state. In other words, the default bootstrap path can install Python 3.12+ and Docker before proceeding with the rest of the setup.

For source-checkout testing, you can still run `pwsh -File .\install\bootstrap.ps1` on Windows or `sh ./install/bootstrap.sh` on POSIX shells. When launched from a checkout, those bootstrap scripts use the checkout's bundled installer instead of downloading `main` again.

Open `http://locus.local` (or your configured `LOCAL_STACK_HOST`); the installer also prints clickable `http://127.0.0.1` and LAN URLs after `lattix up`. If prerequisites cannot be installed automatically, the bootstrap requires a working Python 3 runtime (`py -3` or `python`) on `PATH` — on Windows the Microsoft Store placeholder alias is not sufficient by itself.

Common follow-ups:

```bash
lattix update    # refresh the install without deleting workflows, agents, or settings
lattix remove    # tear down local stacks + installer-managed env (leaves your checkout and .env)
```

`lattix update` keeps `.installer/` env files and Docker data volumes in place, reapplies the package, and restarts the active stack. Re-running the published bootstrap over an existing install follows the same non-destructive posture: it preserves `.installer/` and `.env`, keeps Docker volumes intact, and reuses prior secure-local passwords, bootstrap identities, and OIDC settings as interactive defaults.

> **Profiles.** Set `LOCUS_RUNTIME_PROFILE` to pin security posture explicitly: `local-secure` (fail-closed local/full-stack) or `hosted` (authenticated operator access + signed A2A headers). For lighter local-only iteration, `make local-up` exposes the frontend at `http://localhost:3000` and the backend at `http://localhost:8000` without the gateway `/api` path. The intended default is the **secure full platform stack** (`make up` / `make stack-up`).

---

## Architecture

Locus separates authoring, coordination, execution, memory, and review into explicit layers — it is intentionally *not* a monolith with one undifferentiated memory or agent runtime. The canonical backend surface is `apps/backend/` (control plane) and `apps/workers/` (runtime/worker surface).

### Security + reasoning layers

```text
┌──────────────────────────────────────────────────────────┐
│  LAYER 1: ORCHESTRATION (LangGraph)                       │
│  StateGraph, checkpointing, durable execution             │
├──────────────────────────────────────────────────────────┤
│  LAYER 2: GUARDRAILS (Microsoft Agent Framework filters)  │
│  Prompt render, function invocation, DLP, policy gates    │
├──────────────────────────────────────────────────────────┤
│  LAYER 3: AGENT EXECUTION (MAF ChatAgents + A2A)          │
│  Role-based agents, handoffs, tool invocation via MCP     │
├──────────────────────────────────────────────────────────┤
│  LAYER 4: INFRASTRUCTURE (Docker/K8s + security stack)    │
│  Vault, OPA, Envoy, NATS, Biscuit tokens, Presidio        │
└──────────────────────────────────────────────────────────┘
```

### Cooperating planes

The running system is organized into five cooperating planes:

1. **User interface plane** — the Next.js builder, run console, settings, collaboration, and artifacts (`apps/frontend/`).
2. **Control plane** — the FastAPI backend (`apps/backend/app/main.py`); the canonical API surface owning route classification, auth, definitions, run management, memory APIs, and observability.
3. **Runtime/orchestration plane** — shared runtime primitives (`locus_runtime/`) and worker runtime (`apps/workers/runtime/`) managing staged execution, approvals, discovery, envelopes, middleware, A2A dispatch, and sandbox planning.
4. **Execution plane** — agents and tools execute through bounded runtime contracts; A2A work flows through envelopes and event topics, with tool execution mediated by the sandbox.
5. **State and memory plane** — short-term, durable, and long-term memory plus consolidation queues and world-graph projection, split across Redis, PostgreSQL/pgvector, Neo4j, and local persisted state.

For the full narrative — control plane, memory tiers, isolation strategies, the multi-agent ecosystem, and the frontend connection pattern — see [`docs/SYSTEM-ARCHITECTURE.md`](docs/SYSTEM-ARCHITECTURE.md).

---

## Implementation

### Secure-local installs and runtime profiles

Supported runtime profiles are explicit: `local-secure` (fail-closed secure local/full-stack profile used by `docker-compose.yml`) and `hosted` (non-local; requires authenticated operator access and signed A2A runtime headers). Set `LOCUS_RUNTIME_PROFILE` to pin the posture. Legacy flags like `LOCUS_SECURE_LOCAL_MODE` and `LOCUS_REQUIRE_AUTHENTICATED_REQUESTS` still exist for compatibility, but the named profile is the canonical contract.

Hosted deployments also require signed runtime messages, replay protection, egress allowlists, and MCP local-server policy unless remote MCP servers are explicitly confirmed with `LOCUS_CONFIRM_REMOTE_MCP_SERVERS=true`. Operators can verify the active posture through authenticated `/healthz/details` and `/platform/settings`; both expose the `secure_profile` report used by startup/profile validation.

Secure local installs default to OIDC-backed operator authentication and disable unsigned header-only actor trust. The installer ships with a Casdoor preset by default, but can also emit generic OIDC settings for another IAM provider when you want to connect Locus to an external identity plane. The frontend includes a generic `/auth` portal that points users to the configured provider-hosted sign-in and sign-up URLs, so the same console entry flow works with Casdoor or another OIDC-compliant IAM. The secure local stack exposes Casdoor directly on loopback (`http://127.0.0.1:8081` by default) and also keeps the optional `http://casdoor.localhost` gateway route for environments where that hostname resolves. The installer seeds a default bootstrap admin identity (`locus-admin` / `admin@<hostname>.localhost`) into both the admin and builder actor allowlists so the first authenticated operator lands with the right keys.

Secure-local installs also mirror installer-managed secrets and configuration snapshots into the local Vault instance. The Docker Compose stack backs Vault with the durable `vault-data` volume, while PostgreSQL and Neo4j continue using their own persistent named volumes for long-term platform data. Older installs that do not already have this manifest are upgraded into it automatically during install/update.

### Memory system

Memory is tiered, scoped, and selectively promotable:

- **Redis** handles short-term, hot working memory and session caching.
- **PostgreSQL + pgvector** handles long-term persistent memory and semantic recall.
- **Consolidation scaffolding** queues durable memory candidates when `LOCUS_MEMORY_CONSOLIDATION_ENABLED=true`.
- **Hybrid retrieval** blends short-term session memory, long-term semantic memory, and world-graph context when `LOCUS_MEMORY_HYBRID_RETRIEVAL_ENABLED=true`, with hidden relevance ranking, role-aware boosts, and a bounded token budget.
- **Task learning** promotes task outcomes into long-term memory when `LOCUS_MEMORY_LEARNING_ENABLED=true`.

Internal operators can process queued consolidation candidates via `POST /internal/memory/consolidation/run` and project consolidated summaries into the Neo4j world graph via `POST /internal/memory/world-graph/project`. Useful tuning flags:

- `LOCUS_MEMORY_CONSOLIDATION_MIN_CANDIDATES` — minimum candidates before standard memory is summarized.
- `LOCUS_MEMORY_TASK_LEARNING_MIN_CANDIDATES` — lower threshold for task-learning consolidation.
- `LOCUS_MEMORY_CONSOLIDATION_MAX_POINTS` — maximum bullet points retained in a synthesized summary.
- `LOCUS_MEMORY_CONSOLIDATION_DUPLICATE_MIN_OVERLAP` — token-overlap threshold to suppress near-duplicate summaries.
- `LOCUS_MEMORY_CONSOLIDATION_DUPLICATE_HISTORY_LIMIT` — how many recent summaries are checked for duplicates.
- `LOCUS_MEMORY_HYBRID_MAX_TOKENS` — caps the token budget for ranked hybrid memory injected into execution.
- `LOCUS_MEMORY_HYBRID_MAX_TOPICS` — caps world-graph topics surfaced alongside ranked hybrid memory.
- `LOCUS_MEMORY_GRAPH_PROJECTION_ENABLED` — enables internal Neo4j projection for consolidated summaries.
- `LOCUS_MEMORY_GRAPH_MAX_TOPICS` — maximum topic nodes linked from each consolidated memory.
- `LOCUS_MEMORY_GRAPH_TOPIC_MIN_OCCURRENCES` — minimum repeated occurrences before a topic is projected.

### Execution isolation

`SandboxManager` (`locus_runtime/sandbox.py`) selects the strongest available isolation backend and materializes it from a single declarative policy, so the rest of the runtime never needs to know which backend is in use:

1. **Kernel sandbox** on Linux/macOS via `bubblewrap` or `sandbox-exec`.
2. **Hardened Docker** — read-only root, dropped capabilities, seccomp, resource caps, explicit mounts, optional network disablement.
3. **Kubernetes runtime isolation** — pod metadata for gVisor or Kata runtime classes in hosted deployments.

### Repository layout

- `locus_tooling/` — canonical repo CLI and installer entrypoints
- `locus_runtime/` — shared runtime/security/config primitives used by backend and worker surfaces
- `apps/frontend/` — Next.js builder and operator UI
- `apps/backend/` — FastAPI orchestration/control-plane service
- `apps/workers/` — worker and runtime helpers
- `apps/desktop-tauri/` — Tauri v2 desktop shell
- `packages/contracts/` — public schemas and contracts
- `packages/data/` — public data and seed assets
- `deploy/infra/`, `deploy/gitops/` — public-safe deployment references
- `examples/agents/` — public demo agent assets used by default in local-first development
- `docker-compose.yml` / `docker-compose.local.yml` — local-first stack definitions
- `helm/lattix-locus/` — Kubernetes deployment chart
- `policies/` — baseline OPA policies and tests
- `docs/reference/lattix-locus-docs/` — imported legacy documentation tree

By default, local-first development seeds safe public demo agents from `examples/agents/`. Layer in private agent definitions by setting `LOCUS_AGENT_ASSETS_ROOT` to an external directory.

### CLI

After installation, the `lattix` command supports:

```text
lattix up | down | update | remove
lattix local-up | local-down
lattix health
lattix agent list | agent scaffold --name <agent-name>
lattix workflow list | workflow run <workflow-name> --task "..."
lattix policy test | policy lint
lattix sandbox backend
lattix install run | install bootstrap-url
lattix demo <domain>
```

### Testing

```text
make lint
make typecheck
make policy-test
make helm-validate
make test
```

Windows PowerShell equivalents use `.\scripts\locus.ps1 <target>` (e.g. `.\scripts\locus.ps1 test`). Policy tests use a repo-local OPA binary at `.tools/opa/opa(.exe)` when present, otherwise `opa` on `PATH`; install the pinned binary on Windows with `.\scripts\locus.ps1 install-opa`.

Focused validation for the cognitive MVP:

```text
.venv/Scripts/python.exe -m pytest apps/backend/tests/test_cognitive_graph.py tests/unit/test_cognitive_runtime.py tests/e2e/test_full_pipeline.py -q

cd apps/frontend
npm test -- --run src/lib/locus-node-schema.spec.ts src/components/user-chat-workspace.spec.tsx
```

For the cortical column zero-trust MVP slice, use the focused verification suite below before merging or promoting behavior changes:

```text
python -m pytest tests/unit/test_cognition.py tests/unit/test_assembly_runner.py tests/unit/test_causal_state_persistence.py tests/unit/test_cognitive_transport.py
python -m pytest apps/backend/tests/test_cortical_assembly_endpoint.py
python -m pytest apps/backend/tests/test_generated_artifacts.py -k "secure_profile or runtime_profile or projection or tenant_allowed_runtime or tenant_denied_runtime"
python -m py_compile apps/backend/app/main.py apps/backend/app/request_security.py locus_runtime/cognition.py locus_runtime/assembly_runner.py locus_runtime/events.py locus_runtime/envelope.py locus_runtime/persistence.py
```

Expected coverage includes signed cognitive message admission, replay/idempotency hardening, column capability policy, assembly admission, shared runtime policy gates, commitment validation, sensitive-data redaction, audit event emission, projection safety, secure profile deployment checks, and local-development usability. No separate runtime test is required for documentation-only updates, but behavior-changing slices should update these docs with the applicable commands and expected suites.

### Stack management and rollback

`make stack-up` is kept as an explicit alias for the secure full stack (`make up`); use it when you need the heavier full platform for gateway/sandbox/policy-infra work. `make local-up` runs the lighter `docker-compose.local.yml` stack, which uses `LOCUS_LOCAL_API_BASE_URL` rather than the gateway-based `/api` path.

To tear down the installed local app and delete installer-managed env files so you can test a clean reinstall, use `lattix remove`. Equivalent repo-local helpers remain available:

```text
make remove
.\scripts\locus.ps1 remove
```

For rollback, preserve persistent causal state, replay markers, audit/event-chain artifacts, database volumes, and the A2A signing configuration unless the incident is a signing-key compromise. Roll back application image/configuration first, then rerun the focused zero-trust suite and confirm `/healthz/details` plus `/platform/settings` report an acceptable `secure_profile.status` before reopening write traffic.

### License

This repository is licensed under **AGPL-3.0-or-later**.

- You may use, modify, and redistribute the software under the terms of the AGPL.
- If you run a modified version for users over a network, you must make the corresponding source available to those users.
- AGPL does **not** prohibit commercial use; it requires reciprocity and source availability for covered modifications.

See [`LICENSE`](LICENSE) for the full text. The public repository intentionally excludes proprietary Lattix agent definitions; open-source development should rely on `examples/agents/` or an explicit external `LOCUS_AGENT_ASSETS_ROOT`.

### Documentation

- [`THREAT-MODEL.md`](THREAT-MODEL.md)
- [`docs/SYSTEM-ARCHITECTURE.md`](docs/SYSTEM-ARCHITECTURE.md)
- [`docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md`](docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/SECURITY.md`](docs/SECURITY.md)
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- [`docs/AGENT_DEVELOPMENT.md`](docs/AGENT_DEVELOPMENT.md)
- [`docs/API.md`](docs/API.md)
- [`docs/SANDBOXING.md`](docs/SANDBOXING.md)
- [`docs/INSTALLER.md`](docs/INSTALLER.md)
- [`docs/FOSS_RELEASE.md`](docs/FOSS_RELEASE.md)
