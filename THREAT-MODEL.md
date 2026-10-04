# Threat Model

## Purpose

This document is the canonical security expectations and migration-boundary reference for Lattix Locus.

It serves two jobs:

1. describe the **current-state** security posture and failure modes of the repository as it exists today; and
2. constrain implementation toward the **target-state** architecture where `apps/backend` is the sole canonical backend surface and the removed `lattix_locus/` package remains fully disconnected from active backend/runtime responsibility.

This file should be updated whenever security-relevant behavior, deployment modes, trust boundaries, or accepted exceptions change.

## Scope

In scope:

- `apps/backend/`
- `apps/workers/`
- `policies/`
- `docker-compose.yml`
- `docker-compose.local.yml`
- `helm/lattix-locus/`
- top-level deployment and security documentation

Out of scope for this document:

- blue/green rollout mechanics for a future hosted product
- speculative future controls that are not tied to an actual trust boundary or current risk

## Current-state architecture

The repository no longer contains the old `lattix_locus/` package in the working tree, but it still carries historical migration assumptions from that legacy surface. The current active code paths are the canonical backend/runtime surfaces below.

### 1. Backend model (`apps/backend`)

Primary file: `apps/backend/app/main.py`

Characteristics:

- central route inventory and middleware-backed request classification in `apps/backend/app/request_security.py`
- middleware-first auth enforcement with cached request auth context reused by `_enforce_request_authn(...)`
- configurable policy and guardrail evaluation in application code
- regex-based safety signals and policy gates
- local-first runtime assumptions
- graph-definition CRUD, publish, activate, rollback, and execution control plane

Current strengths:

- immutable definition lifecycle with publish and activate pointers
- audit/event hooks for many mutation paths
- explicit graph schema contract
- local-only egress/runtime defaults in several platform settings
- authenticated operator identity headers are already supported by the frontend API client
- backend routes now have explicit access classes with startup validation to catch unclassified endpoints
- security headers are now applied uniformly in `apps/backend`
- secure/local full-stack mode can now fail closed via environment-backed auth defaults
- backend CORS now uses explicit local methods and headers instead of wildcards
- backend A2A replay protection now uses TTL-based expiry with bounded pruning
- runtime/guardrail execution failures now sanitize caller-facing summaries
- backend/shared path authorization checks now use canonical containment under approved roots
- internal-only backend routes now require trusted service-style auth context instead of generic authenticated callers
- memory API and maintenance paths now validate scope-to-bucket alignment before reading or mutating memory state
- memory reads/writes now enforce actor, tenant, collaboration-session, or internal-service authorization depending on scope
- signed shared-runtime bearer JWTs can now carry actor, tenant, and internal-service identity into backend request auth context
- worker A2A JWT issuance and verification now understand the same `actor`, `tenant_id`, `subject`, and `internal_service` identity claims used by the canonical backend
- worker JWT defaults and deployment examples now align on the shared `locus-runtime` audience and hosted-profile runtime settings used by backend/runtime security policy
- worker A2A client now rejects non-HTTPS endpoints when `LOCUS_RUNTIME_PROFILE=hosted`
- worker service templates now keep `/healthz` minimal in strict profiles, move detailed health/readiness behind authenticated bearer checks, and require `internal_service=true` for hosted/local-secure envelope handling
- shared fallback and Rego agent policy now honor explicit capability-style `allowed_tools` and `max_tool_calls` inputs instead of relying only on static per-agent allowlists
- capability token verification now enforces tool-call budgets and canonical read/write path scopes when those claims are supplied to runtime checks
- worker runtime envelopes now carry a normalized `auth_context`, local bus middleware enforces strict-profile service identity plus scope-aware memory authorization before subscriber delivery, and remote A2A dispatch propagates the same actor/tenant/subject context
- strict worker A2A transport now signs `X-Locus-Subject` / `X-Locus-Nonce` / `X-Locus-Signature` headers, verifies them at the receiving service, and rejects nonce replay for non-local profiles
- backend shared security headers now add HSTS automatically when `LOCUS_RUNTIME_PROFILE=hosted`
- CI now performs real Helm lint/template validation for `helm/lattix-locus`, and local helper tooling exposes the same check when Helm is installed
- backend runtime behavior is now pinned by explicit `LOCUS_RUNTIME_PROFILE` values for `local-lightweight`, `local-secure`, and `hosted`

Current weaknesses:

- some hosted/runtime profile expectations are now codified in Helm defaults, but worker/service handler parity is not fully enforced yet
- hosted/internal profile exposure rules are not yet codified as tightly across all worker/service surfaces as they are in the canonical backend
- some runtime-originated memory flows still rely on local execution context, and the converged signed identity claims are not yet enforced uniformly by all worker/service handlers
- policy sources are still split across backend fallback logic and Rego inputs
- worker/service transport expectations outside local-only mode are not fully hardened yet

## Target-state architecture

The target architecture is:

- `apps/backend` is the **only** canonical backend/control-plane surface.
- `apps/workers` remains the worker/runtime surface.
- logic that previously lived under `lattix_locus/` is either:
  - migrated into `apps/backend` or `apps/workers`,
  - extracted into a small shared library with no competing backend surface, or
  - deleted.

### Target-state rules

1. No new feature work should recreate `lattix_locus/` as a parallel backend.
2. Any reusable security primitive inherited from `lattix_locus/` must be either:
   - ported into `apps/backend`, or
   - extracted as a shared primitive with a clearly documented owner.
3. Deployment docs, runtime wiring, and tests must all point to `apps/backend` as the canonical backend.
4. Security expectations must be described against the target architecture, while current-state exceptions remain explicitly documented.

## Deployment modes

### Local-lightweight

Primary stack: `docker-compose.local.yml`

Intent:

- quick local iteration
- simpler frontend/backend/data-service startup
- convenience over full defense-in-depth

Expected characteristics:

- loopback-bound services where practical
- direct frontend to backend routing
- local-only exceptions explicitly allowed
- not the reference security posture for hosted or service-segmented operation

### Local-secure/full

Primary stack: `docker-compose.yml`

Intent:

- local deployment with the full gateway/policy/sandbox-oriented stack
- default local security posture
- closer approximation of service-level zero trust expectations

Expected characteristics:

- gateway-mediated browser/API flow
- OPA/policy infrastructure present
- sandbox egress mediation present
- stronger service-boundary expectations than legacy lightweight localhost-only behavior

### Future hosted

Not the current execution focus, but the target assumptions are:

- TLS-required service boundaries
- no dev tokens/placeholders
- valid network segmentation
- fail-closed authn/authz
- no dependency on local-only convenience exceptions

## Assets

The following assets require explicit protection:

- workflow, agent, and guardrail definitions
- published and active definition revisions
- generated artifacts
- JWT secrets, signing material, and capability tokens
- sandbox policy/configuration
- memory data across run/session/user/tenant/agent/workflow scopes
- OPA policy inputs and authorization decisions
- deployment secrets (`.env`, installer env overlays, Vault-backed config)
- audit/event streams and observability data
- cortical assembly definitions, column state, belief/evidence records, commitment outcomes, replay markers, and causal graph projections

## Actor classes

- anonymous user
- authenticated local operator
- authenticated service/orchestrator subject
- worker/runtime service
- builder/editor user
- future hosted operator/administrator

## Trust boundaries

1. Browser/UI → backend API
2. Backend API → persistence services (Postgres, Redis, Neo4j)
3. Backend runtime → worker/A2A execution surface
4. Orchestrator/worker → policy engine (OPA)
5. Runtime → sandbox/tool jail
6. Service → service across local/full stack network boundaries
7. Deployment/configuration layer → running services
8. Column runtime → causal state persistence and graph projection
9. Cognitive message transport → backend admission handler


## Threats

Use severity: Critical, High, Medium, Low.

| ID | Threat | Severity | Affected Boundary | Description |
|----|--------|----------|-------------------|-------------|
| T1 | Unauthenticated API access | Critical | Browser/UI → backend API | Anonymous or weakly authenticated callers reach mutating endpoints in non-secure profiles |
| T2 | Credential leakage in logs/traces | Critical | Deployment/configuration → running services | Secrets, tokens, or auth headers accidentally logged in structured logs, traces, or error responses |
| T3 | SQL/injection via graph definition inputs | High | Backend API → persistence services | Malicious graph/agent/guardrail definitions trigger injection in Postgres/Neo4j/Redis |
| T4 | Path traversal in shared filesystem access | High | Runtime → sandbox/tool jail / Backend/shared path auth | Attacker escapes approved roots via symlink, \..\, or canonicalization bypass |
| T5 | SSRF via sandbox egress or runtime fetch | High | Runtime → sandbox/tool jail | Sandbox/tool makes outbound requests to internal metadata services or private networks |
| T6 | Replay of A2A / worker messages | High | Service → service / Orchestrator → worker | Captured signed envelopes replayed to impersonate legitimate worker/orchestrator |
| T7 | Capability token forgery or escalation | High | Orchestrator/worker → policy engine | Forged or over-permissioned capability tokens bypass tool-call budgets or path scopes |
| T8 | Memory scope bypass | High | Backend runtime → worker/A2A | Cross-tenant or cross-scope memory read/write due to missing authorization checks |
| T9 | Policy drift between Rego and Python fallback | Medium | Orchestrator/worker → policy engine | Allow/deny decisions diverge when fallback and Rego logic evolve independently |
| T10 | Introspection leakage via health/diagnostic endpoints | Medium | Browser/UI → backend API / Deployment → services | \/healthz\, \/ready\, \/platform/*\ expose runtime config, versions, or topology to unauthenticated callers |
| T11 | CORS misconfiguration | Medium | Browser/UI → backend API | Overly permissive origins/headers allow browser-based credentialed attacks |
| T12 | Supply chain: compromised dependency | Medium | Deployment/configuration → running services | Malicious package in Python/npm supply chain executes at build or runtime |
| T13 | Container escape via sandbox breakout | Low | Runtime → sandbox/tool jail | Attacker breaks out of seccomp/bubblewrap/Docker isolation to host kernel |
| T14 | Denial of service via unbounded queues/retries | Low | Orchestrator/worker → policy engine / Runtime → sandbox | Unbounded work queues, retries, or tool calls exhaust memory/CPU |
| T15 | Configuration drift between local and hosted profiles | Low | Deployment/configuration → running services | Secure/full and lightweight modes diverge, leading to wrong operator assumptions |
| T16 | Local process widens capability through the desktop loopback bootstrap | High | Local process → backend API (desktop `local-native`) | On the desktop every loopback request is authenticated as the operator, so an un-jailed local process (or a confused page) approves its own agent asks, clears panic, loosens settings or guardrails, adds integrations, skills, keys, triggers or schedules, or widens the browser tier |

## Mitigations

| Threat ID | Mitigation | Status | Implementation |
|-----------|------------|--------|----------------|
| T1 | Central route inventory with middleware enforcement; startup validation catches unclassified endpoints; secure/local full-stack fails closed via env-backed auth defaults | **Implemented** | `apps/backend/app/request_security.py`, `apps/backend/app/main.py` (route classification), `LOCUS_RUNTIME_PROFILE=local-secure` |
| T2 | Structured logging redacts auth headers, tokens, secrets; error responses sanitize guardrail internals; no PII in traces by default | **Implemented** | `apps/backend/app/logging_config.py`, runtime sanitization in `locus_runtime/security.py` |
| T3 | Prepared statements / safe query builders in backend; graph schema validation; input length/format checks at API boundary | **Implemented** | SQLAlchemy ORM, Pydantic models in `apps/backend/app/schemas.py` |
| T4 | Canonical containment checks (not prefix matching) in backend loaders and shared fallback policy; symlink resolution before containment test | **Implemented** | `apps/backend/app/filesystem.py`, `policies/fs.rego` |
| T5 | Squid domain allowlist (fail-closed) replacing open IP-range ACL; network namespace isolation when `allow_network=False` | **Implemented** | `docker/sandbox/squid.conf`, `locus_runtime/sandbox.py` |
| T6 | A2A replay protection: TTL-based nonce expiry with bounded pruning; signed `X-Locus-Subject`/`Nonce`/`Signature` headers verified at receiver | **Implemented** | `apps/backend/app/a2a_replay.py`, worker A2A client in `apps/workers/` |
| T7 | Capability grants are Biscuit tokens (Ed25519, key id + accepted-key list for rotation, attenuation only narrows, persisted revocation ids, expiry: run ≤ 24h, standing 30 days unless pinned). The gateway looks grants up server-side for the authenticated principal; a covering grant turns R3 `ask` into `allow` only after policy allowed and never for R4. HMAC capability tokens retired (LOCUS-334) | **Implemented** | `locus_runtime/grants.py`, `locus_runtime/gateway.py` step 3 |
| T8 | Memory reads/writes enforce actor, tenant, collaboration-session, or internal-service authorization per scope; scope-to-bucket validation | **Implemented** | `apps/backend/app/memory.py`, worker runtime envelope auth middleware |
| T9 | Python copy of the rules removed (LOCUS-328); `policies/*.rego` evaluated only by a real Rego engine behind `PolicyEngine` (OPA loopback sidecar, fail closed); parity suite over all 7 policies. Not yet on an execution-path gateway (posture: unverified) | **Partial** | `locus_runtime/policy_engine.py`, `tests/policy/test_policy_parity.py`, `tests/unit/test_policy_engine.py` |
| T10 | Central route classification marks /healthz public (minimal), diagnostics authenticated; secure local mode serves minimal public healthz | **Implemented** | `apps/backend/app/request_security.py` access classes |
| T11 | CORS uses explicit local origins/methods/headers instead of wildcards; localhost-only in local profiles | **Implemented** | `apps/backend/app/main.py` CORS middleware config |
| T12 | Gitleaks secret scanning in CI; Semgrep SAST; Trivy SCA for vuln/misconfig; SBOM via Syft; pinned dependencies in `pyproject.toml`/`package-lock.json` | **Implemented** | `.github/workflows/security-lifecycle.yml`, `precommit.ps1` |
| T13 | Three-tier sandbox: seccomp BPF, read-only rootfs, network namespace, non-root, resource limits, IPC isolation, gVisor/Kata RuntimeClasses for K8s | **Implemented** | `docker/sandbox/seccomp-strict.json`, `locus_runtime/sandbox.py`, Helm chart `locus-sandbox` RuntimeClass |
| T14 | Bounded pids-limit (256), memory (512m), CPU (1.0); capability token `max_tool_calls` budget; sandbox resource limits | **Implemented** | `locus_runtime/sandbox.py`, capability token claims |
| T15 | Explicit `LOCUS_RUNTIME_PROFILE` values (`local-lightweight`, `local-secure`, `hosted`); Helm values-prod.yaml codifies hosted posture; CI validates Helm render | **Implemented** | `locus_tooling/common.py`, `helm/lattix-locus/values-prod.yaml`, CI Helm lint |
| T16 | Every mutating route is classified `widening` / `narrowing` / `conditional` / `neutral` in one table (startup refuses an unclassified route). On the desktop profile a widening request also needs the Tauri shell's single-use HMAC proof (per-launch secret over stdin, ±60 s, bound to action, method, path and canonical body), produced only after the human confirms a native dialog whose text the shell builds from the request and the backend's state; refusals are audited; narrowing needs none; no shell secret means no widening (LOCUS-350, LOCUS-357). **Residual:** same-OS-user code can read process memory or fake input to the dialog; only jailed runs (no loopback) are excluded. In the desktop shell the UI sends these calls through `confirm_action` (`apps/frontend/src/lib/desktop-confirmation.ts`). | **Implemented** (Rust shell compiled in CI only) | `apps/backend/app/request_security.py`, `apps/backend/app/capability_widening.py`, `locus_tooling/shell_confirmation.py`, `apps/desktop-tauri/src-tauri/src/shell_actions.rs`, `browser_tier.rs` |

## Required Tests

The following tests provide regression coverage for the threats and mitigations above. All tests must pass in CI before merge.

| Test Target | Test File(s) | Threat Coverage | Frequency |
|-------------|--------------|-----------------|-----------|
| Route classification & auth enforcement | \pps/backend/tests/test_request_security.py\ | T1 | Every PR |
| Security headers & sanitized errors | \	ests/unit/test_security_headers.py\ | T2, T10 | Every PR |
| Graph schema validation & injection safety | \pps/backend/tests/test_graph_validation.py\ | T3 | Every PR |
| Filesystem canonical containment | \	ests/unit/test_compose_auth_contract.py\, \policies/tests/fs_test.rego\ | T4 | Every PR |
| Sandbox egress allowlist & network isolation | \	ests/unit/test_sandbox_policy.py\, \	ests/backend/test_windows_sandbox.py\ | T5, T13 | Every PR |
| A2A replay protection & nonce expiry | \	ests/unit/test_event_signing.py\, \pps/backend/tests/test_a2a_replay.py\ | T6 | Every PR |
| Capability grant verification (signature, key rotation, expiry, attenuation, revocation, gateway R3/R4/deny) | `tests/unit/test_biscuit_tokens.py`, `tests/unit/test_gateway_grants.py`, `apps/backend/tests/test_gateway_grants_api.py`, `tests/policy/test_gateway_opa.py` | T7 | Every PR |
| Memory scope authorization (actor, tenant, collaboration, internal) | \pps/backend/tests/test_memory_scope.py\ | T8 | Every PR |
| Policy parity (PolicyEngine vs expected Rego decisions) | `policies/tests/*.rego`, `tests/policy/test_policy_parity.py` (CI: quality-policy-helm, real OPA) | T9 | Every PR |
| Health/diagnostic endpoint visibility | \pps/backend/tests/test_endpoint_visibility.py\ | T10 | Every PR |
| CORS configuration | \pps/backend/tests/test_cors.py\ | T11 | Every PR |
| Secret scanning (Gitleaks), SAST (Semgrep), SCA (Trivy) | CI-only: \.github/workflows/security-lifecycle.yml\ | T12 | Every PR + scheduled |
| Sandbox isolation (seccomp, bubblewrap, gVisor) | \	ests/backend/test_windows_sandbox.py\, Helm chart render tests | T13 | Every PR + Windows CI |
| Resource limits & tool-call budgets | \	ests/unit/test_sandbox_policy.py\ | T14 | Every PR |
| Profile consistency (local-lightweight vs local-secure vs hosted) | \	ests/unit/test_helm_security_contract.py\, \helm/lattix-locus/values-*.yaml\ diff check | T15 | Every PR |
| Compose config validation (both compose files) | \docker compose config --quiet\ in CI | T5, T13, T15 | Every PR |
| Helm lint & template render (prod values) | \make helm-validate\ in CI | T15 | Every PR |
| OPA policy tests | \make policy-test\ | T4, T9 | Every PR |
| Desktop shell proof for widening endpoints (refused + audited, valid, replay, bound to the request, narrowing free, route inventory complete, `is_widening`, Rust/Python sync) | `apps/backend/tests/test_shell_proof_endpoints.py`, `apps/backend/tests/test_capability_widening.py`, `apps/backend/tests/test_user_browser_endpoint.py`, `tests/unit/test_shell_confirmation.py`, `tests/backend/test_desktop_packaging.py` | T16 | Every PR |

---
## Service-level zero trust expectations

The secure/full stack target is **service-level zero trust**.

That means:

- no service is trusted merely because it is on the same machine, Docker network, or cluster
- every service boundary should be authenticated and authorized
- policy engines and network boundaries are defense-in-depth, not the only control
- diagnostics and health surfaces must be minimized by default
- local-only exceptions must be explicitly named and scoped to deployment mode

## Security invariants

The following must remain true as the system evolves:

1. Auth defaults fail closed in the secure/full stack.
2. Sensitive configuration and security posture details are not anonymously disclosed.
3. Policy decisions deny by default when uncertain.
4. Filesystem authorization uses canonical containment semantics, not prefix matching.
5. Replay protection expires correctly and does not degrade silently under load.
6. Capability claims that are minted are actually enforced.
7. Memory scope labels are not treated as sufficient protection without actual access control enforcement.
8. Backend/runtime error responses do not reveal guardrail rule internals to untrusted callers.
9. Deployment docs and runtime defaults describe the same security posture.
10. Cognitive messages cannot mutate causal state unless they are signed, fresh, tenant-consistent, assembly-consistent, and non-replayed.
11. Column capabilities are enforced by column kind and assembly policy before model, tool, retrieval, memory, or commitment actions.
12. Causal graph projection is a tenant-authorized derived view and must never be the only copy of causal state.

## Cortical column zero-trust threat model

The cortical assembly runtime extends the service-level zero-trust model into intra-runtime execution. A column, runtime helper, or message sender is not trusted because it runs in the same backend process, worker process, Docker network, or Kubernetes namespace. The security model assumes columns and runtime messages may be malformed, stale, replayed, tenant-confused, over-permissioned, or carrying sensitive payloads until backend admission proves otherwise.

### Zero-trust column assumptions

- Column kind is an authorization boundary. A goal column can read input and emit goal beliefs, evidence can retrieve, evaluation can score or veto, synthesis can propose commitments, and unknown kinds deny by default.
- Assembly definitions are untrusted input. They must be admitted before runtime execution and bounded by max columns, max iterations, max messages, required goal/evidence/evaluation/synthesis participation, and tenant/provider/model/tool policy.
- Runtime execution steps require a shared policy gate. Capability checks alone are not sufficient without authenticated context, tenant ownership, runtime allowlists, network/tool/retrieval allowlists, budget counters, and audit events.
- Commitments are high-impact decisions. Evidence/evaluation participation, veto/blocker state, confidence thresholds, high-risk human approval, and an audited decision trail must be checked before a ready commitment is accepted.
- Projection is not authorization. Neo4j/world-graph causal projection is a bounded derived view of persisted causal state and must fail without corrupting the source state.

### Tenant isolation model

Tenant identity can enter through authenticated backend request context, signed shared-runtime bearer claims, and cognitive message metadata. The expected behavior is fail-closed when those sources conflict. Cross-tenant assembly replay, tenant-mismatched cognitive messages, wrong-tenant graph projection, and scope-label-only memory access are security failures.

The minimum tenant isolation contract is:

1. every admitted cognitive message includes tenant and assembly identity;
2. the authenticated or signed runtime tenant must match persisted assembly ownership;
3. replay markers are scoped by assembly, tenant, source column, target column, message type, and payload ref;
4. causal graph projection requires the caller tenant to match the persisted assembly tenant; and
5. memory and causal state access use authenticated actor, tenant claim, collaboration membership, or internal-service identity instead of trusting caller-supplied bucket labels alone.

### Signed message flow

The trusted flow for column messages is:

1. runtime code converts a `ColumnMessage` into a cognitive `AgentEvent` or `Envelope`;
2. the sender signs the request with `X-Locus-Subject`, `X-Locus-Nonce`, `X-Locus-Timestamp`, `X-Correlation-ID`, and `X-Locus-Signature`;
3. backend request security verifies profile-required authentication, signature freshness, and nonce replay status;
4. cognitive admission validates message type, tenant ownership, assembly existence, known source and target columns, and semantic replay marker; and
5. accepted messages can record replay evidence and audit events before any state mutation proceeds.

Unsigned, stale, replayed, wrong-tenant, unknown-column, or conflicting messages must be rejected before belief history, confidence history, commitment outcomes, or projection state changes.

### Policy gate flow

The shared column runtime gate is the central authorization checkpoint for cortical execution. It must run before model calls, retrieval, tool calls, memory writes, and commitment publication. The gate evaluates authenticated context, tenant ownership, column capability, explicit assembly grants, runtime provider/model allowlists, tool/retrieval/network allowlists, budget counters, and audit emission.

The policy-gate threat assumption is that any future direct path around `admit_column_runtime_step(...)`, `require_column_capability(...)`, assembly admission, or commitment validation can become a privilege-escalation path and should be treated as a security regression.

### Deployment profile requirements

Runtime profiles define which local exceptions are allowed:

| Profile | Intended use | Security expectation |
| --- | --- | --- |
| `local-lightweight` | quick local iteration | local dev remains usable; public health/settings behavior may be lightweight where explicitly classified |
| `local-secure` | secure local/full stack | authenticated operator access, signed/internal service identity for protected paths, replay protection, egress mediation, and degraded status for unsafe secure controls |
| `hosted` | non-local service deployment | authenticated access, required A2A runtime headers, signed messages, replay protection, egress allowlist, MCP local policy unless explicitly confirmed, and blocked/unhealthy status for unsafe controls |

Hosted deployments must not rely on legacy local-only flags, unsigned actor headers, direct internal health detail exposure, or raw MCP endpoint entry as a substitute for staged/admin-approved connection policy.

### Rollback and monitoring expectations

Rollback should preserve causal state and audit evidence. Operators should roll back application code/configuration to the previous known-good release, not delete replay markers, causal state, or audit/event-chain artifacts as a first response. If signing material is compromised, A2A secret rotation is a separate security action and should be coordinated across all runtime senders.

Monitoring should include:

- authenticated `/healthz/details` and `/platform/settings` `secure_profile` status;
- audit events for cognition admission/rejection, policy decisions, column lifecycle, message acceptance/rejection, commitment finalization/escalation, and projection success/failure;
- runtime security counters and traces with correlation IDs;
- projection statuses such as `skipped`, `unavailable`, or `write_failed`;
- tenant-isolation block events; and
- redaction markers plus absence of raw secrets in audit, persistence, projection, and error-response payloads.

## Sandbox hardening status (three-tier hybrid)

The sandbox subsystem has been hardened with the following controls:

| Control | Status | Implementation |
| --------- | -------- | --------------- |
| Custom seccomp BPF profile | **Implemented** | `docker/sandbox/seccomp-strict.json` blocks ptrace, io_uring, mount, bpf, setuid, and 30+ other syscalls |
| Read-only root filesystem | **Implemented** | `--read-only` flag in hardened Docker; `--ro-bind / /` in bubblewrap |
| Network isolation (namespace) | **Implemented** | `--network=none` (Docker) / `--unshare-net` (bubblewrap) when `allow_network=False` |
| Non-root execution | **Implemented** | `--user=1000:1000` (Docker) / `--unshare-user` (bubblewrap) |
| Resource limits | **Implemented** | `--memory=512m --cpus=1.0 --pids-limit=256` in hardened Docker |
| IPC namespace isolation | **Implemented** | `--ipc=private` (Docker) / `--unshare-ipc` (bubblewrap) |
| Capability token expiration | **Implemented** | `exp` field with 10-minute TTL; verifier rejects expired tokens |
| Squid domain allowlist | **Implemented** | Fail-closed domain allowlist replacing open IP-range ACL |
| Kernel sandbox (no Docker) | **Implemented** | bubblewrap (Linux) / seatbelt (macOS) auto-detected by `SandboxManager` |
| K8s gVisor RuntimeClass | **Implemented** | Helm chart deploys `locus-sandbox` RuntimeClass with `runsc` handler |
| K8s Kata RuntimeClass | **Implemented** | Optional `locus-sandbox-vm` RuntimeClass for hardware-level isolation |
| K8s seccomp ConfigMap | **Implemented** | `locus-strict.json` deployed as ConfigMap for node sync |

## Known failure modes and current mitigation status

| Area | Current issue | Impact | Current mitigation / compensating control | Planned remediation |
| --- | --- | --- | --- | --- |
| Backend auth | `apps/backend` now centralizes route classification and request enforcement, but deployment profiles still decide when authenticated-read/mutate routes are mandatory outside secure/local full-stack mode | anonymous or weakly gated access if weaker profiles are misconfigured | central route inventory, middleware enforcement, startup validation, and secure local fail-closed defaults | make secure/hosted profile behavior immutable and consistently selected |
| Security policy disclosure | `/platform/security-policy` should not be public in secure/full mode | attacker reconnaissance | central route classification now marks it authenticated-read and secure local mode requires auth | classify endpoint visibility consistently across hosted/internal profiles |
| Introspection leakage | `/healthz` and runtime readiness/provider endpoints can expose too much detail | service and trust boundary reconnaissance | central route classification plus secure local mode now serves minimal public `/healthz` and authenticated diagnostics endpoints | extend the same contract to all hosted/internal profiles |
| OPA path checks | canonical containment is now enforced in backend loaders and shared fallback policy, but parity must remain consistent across all policy consumers | residual bypass risk if new callers reintroduce prefix checks | canonical containment checks now guard approved roots | keep parity tests and avoid new prefix-based checks |
| Policy drift | Rego policy and Python fallback still duplicate logic, but now share a normalized evaluation contract for security-critical decisions | residual inconsistent-enforcement risk if one side evolves without parity updates | parity tests now cover path access, dynamic tool allowlists, and tool budgets across fallback/Rego paths | keep one normalized request contract and expand parity coverage as new controls are added |
| Static agent allowlists | hardcoded defaults still exist, but policy/fallback now accept explicit per-request `allowed_tools` inputs | reduced new-agent denial/drift risk; defaults can still diverge if overused | dynamic allowlists are now honored by both the Python fallback and Rego policy | move the remaining defaults toward config-backed policy sources |
| Capability enforcement | capability claims are now enforced for tool membership, tool-call budgets, and canonical read/write path scopes in shared runtime checks | reduced over-permission risk; callers must still consistently provide the correct metadata for evaluation | shared verifier + filter-chain enforcement now validate richer claims before execution | keep extending capability checks to future runtime call sites and metadata producers |
| Replay cache | backend A2A nonce handling now expires and prunes correctly, but other auth surfaces could still drift if reimplemented independently | reduced replay weakening / memory pressure risk | TTL-based expiry + bounded pruning in backend | converge remaining auth surfaces on shared replay primitive |
| Worker transport | worker A2A transport now rejects plaintext endpoints in `hosted` profile, but broader TLS verification and service identity expectations still need fuller enforcement | reduced MITM / weak transport assumptions for hosted A2A helper paths | hosted-profile HTTPS enforcement in worker A2A client; local profiles still allow explicit local-only exceptions | extend TLS verification and service identity policy across remaining runtime/service callers |
| Runtime observability | worker/runtime enforcement previously had minimal structured telemetry for allow/block/failure decisions | poor incident reconstruction and harder proof of control operation | runtime envelopes now accumulate structured security events, delivery traces, and counters keyed by `correlation_id` | keep wiring the same telemetry into future runtime paths and operator-facing dashboards |
| Error leakage | caller-facing runtime summaries are now sanitized, but other endpoints still need the same discipline when new failure modes are added | reduced rule-enumeration and recon risk | sanitized runtime/guardrail failure summaries + logs/audit retained | extend sanitization review to future hosted/internal APIs |
| CORS | backend local browser access now uses explicit allowlists | reduced browser attack surface | localhost origin restriction plus explicit method/header allowlists | keep deployment-specific origin config explicit |
| Security headers | backend now applies stronger hosted/TLS headers, but browser/API hardening can still expand further if new exposure modes are added | reduced browser/API hardening risk | backend now sets `nosniff`, `DENY`, restrictive CSP headers, and hosted-profile HSTS by middleware | extend header review as new hosted/browser exposure paths are added |
| Memory boundaries | backend and worker/runtime execution paths now enforce scope-aware access checks, but broader non-backend consumers still need to stay aligned | reduced cross-scope or cross-tenant bleed risk across backend API paths and local worker/runtime delivery; remaining risk is future drift in new consumers | scope-to-bucket validation, actor/user checks, tenant claim checks, collaboration membership checks, internal-only maintenance auth, and runtime envelope authorization middleware | keep propagating the same ownership model into any new runtime/service consumers and policy sources |
| Tenant context drift | runtime envelopes could previously carry authenticated tenant identity while embedding conflicting payload tenant context | cross-tenant confusion, unsafe fan-out, or weak non-prod isolation evidence | runtime security now rejects conflicting payload tenant assertions and records explicit tenant-isolation audit events | extend the same tenant-consistency checks to future remote/runtime consumers as they are added |
| Helm network policies | template was previously malformed; repo now has real Helm lint/render validation in CI, though this local machine still lacks a Helm binary for an interactive run | sharply reduced false-confidence risk because PRs/pushes now render the chart | repaired control-plane policy template, selector audit against current chart workloads, and CI Helm lint/template validation | optionally run the same check locally in Helm-capable operator environments before release |
| Release/promotion drift | release docs previously promised staged promotion and rollback behavior that CI did not implement | unsafe or unverifiable production promotion path | release automation now builds versioned bundles, publishes release assets, gates `dev -> stage -> prod` promotion with environment smoke checks, and exposes a manual rollback workflow driven by rollback metadata | extend the same promotion evidence into deployment apply/reconcile jobs as those surfaces mature |
| Deployment drift | docs/defaults around secure vs lightweight modes have been inconsistent | wrong operator assumptions | recent cleanup improved this | codify in docs + threat model + tests |
| Cognitive message replay | signed transport nonce checks alone do not catch semantic duplicate messages with new nonces | duplicate belief or commitment mutation | persisted replay markers now bind assembly, tenant, columns, message type, and payload ref; completed assemblies replay idempotently | keep replay marker coverage for new message/outcome mutation paths |
| Column privilege escalation | a future runtime path could call model/tool/retrieval/memory/commitment logic without the column runtime gate | unauthorized tool use, tenant data access, or premature commitment | shared `admit_column_runtime_step` and column capability checks cover current cortical execution paths | treat any direct bypass as a security regression and add tests before new runtime paths merge |
| Causal projection abuse | graph projection could reveal cross-tenant state or corrupt persisted causal history if treated as source of truth | tenant leakage or data loss | projection is tenant-checked, bounded, idempotent, and failure-isolated from persisted state | keep projection authorization and failure-mode tests with future graph adapters |
| Secure profile misconfiguration | hosted settings can disable controls that the deployment profile requires | hosted service starts or stays healthy without auth/signature/replay/egress/MCP controls | secure profile report, startup validation, settings rejection, and health blocking/degradation | keep profile requirements immutable and extend parity across worker/service surfaces |
| Desktop update channels (D-26, LOCUS-349) | the updater signing key (repo secret `TAURI_SIGNING_PRIVATE_KEY`) is the root of trust for every Dev and Stable install, and Dev installs auto-install code the self-improvement loop wrote | a leaked key plus release write access, or a loop PR that slips past the gates, ships code to every Dev install without a click | mandatory minisign verification in the app against the committed pubkey; only two compiled-in channel URLs; CI publishes no `latest.json` without the key and verifies every signature against the pubkey before publishing; channel pointers only move forward, serialized; the update trust chain (`apps/desktop-tauri/src-tauri/`, `scripts/desktop_channel.py`, `locus_tooling/{update_contract,desktop_update,build_info}.py`, workflows) is D-22 protected; Stable is a manual promotion of tested bytes; the app refuses a backend whose build stamp differs (tauri#15134) | LOCUS-351 scorecard as a Dev gate; code-signing certificates (D-24); consider a key held outside GitHub (offline signing for Stable) |
| Legacy backend drift | the deleted `lattix_locus/` package is still referenced by some docs and historical migration notes | dual-surface confusion and documentation/operator drift | package removed from the working tree; migration intent documented in this file | complete Phase 3 legacy-surface retirement across docs/tooling/tests |

## Historical migration record for removed `lattix_locus/`

The following is the Phase 0 migration matrix preserved as a historical record of how legacy surfaces were intended to be migrated, extracted, or deleted.

| Surface | Current role | Phase 0 disposition | Notes |
| --- | --- | --- | --- |
| `lattix_locus/api/middleware/auth.py` | stronger auth pattern | migrate to `apps/backend` or extract shared primitive | do not keep as legacy-only behavior |
| `lattix_locus/api/middleware/security_headers.py` | stronger response-header hardening | migrate to `apps/backend` or extract shared primitive | should become canonical backend behavior |
| `lattix_locus/security/jwt_auth.py` | stronger replay/revocation/token validation | extract shared primitive or port into backend auth layer | avoid parallel auth implementations |
| `lattix_locus/security/opa_client.py` | OPA client + fallback | extract shared primitive after remediation, or retire if backend replaces policy path | fix path semantics before reuse |
| `lattix_locus/sandbox/` | tool jail / isolation model | extract shared primitive for worker/backend use | security-critical; not a delete-first area |
| `lattix_locus/guardrails/` | legacy filter-chain guardrails | migrate useful primitives, delete parallel runtime wiring | avoid second guardrail architecture |
| `lattix_locus/orchestrator/` | legacy orchestration/runtime surface | disconnect then delete or reduce to worker/shared execution primitives | should not remain canonical |
| `lattix_locus/api/routes/` | legacy API surface | disconnect from deployment path, then delete | target is `apps/backend` only |
| `lattix_locus/agents/` | legacy A2A/client/server helpers | migrate needed runtime pieces to `apps/workers` or shared package | keep only what workers actually use |
| `lattix_locus/persistence/` | state helpers | evaluate for extraction or replacement by backend platform services | no duplicate system of record |
| `lattix_locus/events/` / `observability/` | event integrity / telemetry primitives | extract if still needed, otherwise delete duplicate surface | preserve audit guarantees |
| `lattix_locus/cli.py` / install helpers | legacy operator entrypoints | update to point at canonical backend/workers or remove | no stale control path |
| `lattix_locus/config.py` | shared settings and secret validation | extract shared config primitive if still needed | keep one config truth |
| `policies/*.rego` | policy source | retain, but feed from canonical backend/worker inputs | do not duplicate policy logic in two backends |

### Migration rule

Every legacy `lattix_locus/` surface was assigned one of three dispositions before new security work proceeded beyond Phase 0:

- **migrate** — port into `apps/backend` / `apps/workers`
- **extract** — move into a small shared primitive package with one owner
- **delete** — remove after disconnection

No item should remain in an unowned “we’ll figure it out later” state.

## Definition of done for Phase 0

Phase 0 is complete when:

1. `THREAT-MODEL.md` exists and is treated as canonical.
2. Current-state and target-state architectures are both documented.
3. the removed `lattix_locus/` package is explicitly documented as historical/transitional rather than current.
4. the migration matrix covers every formerly live security/runtime area in `lattix_locus/`.
5. New work is guided toward `apps/backend` / `apps/workers`, not legacy backend expansion.
6. Repo docs point readers to this threat model for security expectations and architecture convergence.

## Change management requirements

Any security-relevant change should update this file if it changes:

- trust boundaries
- deployment-mode assumptions
- public vs authenticated endpoint exposure
- token/replay/auth semantics
- sandbox policy/invariants
- memory boundary expectations
- the migration status of any historically referenced `lattix_locus/` surface

## Phase 3 focus

Phase 3 is the **legacy-surface retirement and documentation convergence** wave.

Its goal is to finish the repo-level cleanup that becomes possible after the functional/security migration work: remove stale references to the deleted `lattix_locus/` package, keep release/docs/tooling aligned to the canonical surfaces, and prevent drift from reintroducing a phantom second backend.

### Phase 3 objectives

1. remove stale documentation and release references that still describe `lattix_locus/` as a live in-tree package
2. add regression coverage for canonical repo structure assumptions so deleted legacy surfaces do not silently reappear in docs/tooling
3. narrow historical migration notes so they remain useful context without confusing operators about the active architecture

### Phase 3 implementation status

Implemented:

- stale README architecture/layout references to a live `lattix_locus/` package have been removed
- `docs/ARCHITECTURE.md` now describes the active canonical surfaces instead of a deleted dual-surface package layout
- FOSS/security docs now treat `lattix_locus/` as historical migration context rather than a current canonical path

Still remaining for this workstream:

- audit the broader docs/reference tree for stale legacy-package claims and update only the pieces that still affect operator/developer guidance
- add codebase guards where helpful so canonical repo structure changes are validated rather than remembered informally

### Phase 3 workstreams

#### 1. Legacy reference retirement

- remove stale references to deleted in-tree legacy packages from docs, release guidance, and operator instructions
- distinguish historical migration notes from current architecture claims

#### 2. Canonical structure guardrails

- add regression coverage for canonical repo structure expectations
- prevent tooling/docs drift from reviving removed package assumptions

#### 3. Historical record minimization

- keep only the historical migration context that still explains security or ownership decisions
- trim or rewrite notes that no longer help current operators or contributors

## Phase 1 completion status

Phase 1 secure-local hardening is complete when the following are true, and they are now true in the canonical backend/runtime surfaces:

1. path checks use canonical containment semantics under approved roots
2. `/platform/security-policy` and sensitive runtime diagnostics are gated in secure/full local mode
3. secure/full backend auth fails closed by default via deployment-backed profile settings
4. public introspection is minimized while authenticated diagnostic detail remains available
5. stronger replay and response-hardening primitives are ported into canonical surfaces

There are no remaining Phase 1 secure-local blocking tasks in the canonical backend/runtime surfaces. Remaining items now belong to Phase 2 and should be treated as the next execution wave rather than as incomplete Phase 1 work.

## Phase 2 focus

Phase 2 is the **hosted-parity and control-convergence** wave.

Its goal is to move from a verified secure-local baseline to a deployment model that remains trustworthy when services are separated, long-lived, and operated beyond a single local machine.

### Phase 2 objectives

1. make auth/profile behavior centrally enforced rather than route-local plus deployment-convention driven
2. harden non-local worker/service transport expectations
3. implement real memory access control instead of scope-label trust
4. converge Python fallback policy behavior and Rego policy behavior onto one verifiable contract
5. repair deployment-layer controls that currently provide incomplete or misleading protection

### Phase 2 implementation status

The first Phase 2 workstream is now partially complete in the canonical backend.

Implemented:

- explicit backend route inventory in `apps/backend/app/request_security.py`
- access classes for `public-minimal`, `authenticated-read`, `authenticated-mutate`, and `internal-only`
- central middleware enforcement in `apps/backend/app/main.py`
- startup validation that fails if new backend routes are added without classification
- shared response hardening helper in `apps/backend/app/security_headers.py`
- regression coverage for shared headers, route inventory completeness, and previously under-protected read surfaces
- internal-only route enforcement that now requires trusted subject or bearer-backed internal auth context
- memory scope-to-bucket validation for backend retrieval, consolidation, and world-graph projection paths
- backend memory authorization checks for session, user, tenant, agent, and workflow scopes
- regression coverage for cross-scope denial and internal maintenance endpoint protection
- signed shared-runtime JWT bearer claims now populate backend actor, tenant, and internal-service auth context
- regression coverage for JWT-backed tenant memory access and internal-route gating
- worker JWT helpers and A2A envelope posting now emit/consume the same actor, tenant, subject, and internal-service identity claims used by shared runtime auth
- worker runtime defaults/examples now pin the shared `locus-runtime` audience and `hosted` profile expectations instead of a divergent worker-only audience
- regression coverage for worker JWT claim round-tripping and A2A claim propagation
- backend auth and public health exposure now resolve through explicit runtime profiles, with compose defaults pinned to `local-lightweight` and `local-secure`
- regression coverage for `local-secure` and `hosted` runtime-profile behavior
- Helm chart defaults now pin Kubernetes API/orchestrator workloads to the `hosted` runtime profile and require signed A2A runtime headers via chart-managed shared secret wiring
- Helm control-plane network policies have been repaired to match the workloads the chart actually deploys

Still remaining for this workstream:

- move beyond backend-only convergence so workers and service-to-service transport follow the same profile contract
- align hosted profile behavior with worker/service runtime surfaces rather than just backend request handling
- validate Helm render/apply behavior in a Helm-capable environment and extend regression coverage for hosted deployment manifests beyond static contract checks

The memory access control workstream is now partially implemented in the canonical backend as well.

Implemented:

- backend memory endpoints reject bucket/scope mismatches instead of trusting caller-supplied scope labels
- `session` and `user` memory access now binds to the authenticated actor identity
- `tenant` memory access now requires an explicit tenant claim that matches the bucket, whether provided via header or signed runtime bearer token
- `agent` and `workflow` memory access now requires collaboration-session membership for non-internal callers
- internal consolidation and world-graph projection routes now require internal service authentication from trusted headers or signed internal-service bearer claims
- worker service templates now decode and expose the same signed identity claims for downstream handler authorization decisions

Still remaining for this workstream:

- extend memory authorization parity checks to any future non-backend consumers and policy sources so new runtime paths do not drift

The policy source convergence workstream is now partially implemented as well.

Implemented:

- Python fallback policy and `policies/agent_policy.rego` now both honor explicit per-request `allowed_tools`
- Python fallback policy and Rego policy now both enforce `max_tool_calls` budgets
- policy evaluation now flows through `locus_runtime.policy_engine.PolicyEngine` (Rego via OPA sidecar); the `PolicyEvaluationRequest` / `OPAClient` Python rule copy was removed (LOCUS-328)
- capability verification now enforces canonical read/write path scopes and tool-call budgets rather than only tool-name membership
- guardrail filter-chain enforcement now evaluates richer capability claims before targeted runtime actions proceed
- regression coverage now exercises dynamic allowlists, capability budgets, canonical path containment, and structured policy decisions

Still remaining for this workstream:

- keep expanding parity coverage as new policy controls are introduced so fallback/Rego behavior does not drift again
- continue reducing reliance on hardcoded defaults by moving remaining policy inputs toward configuration-backed sources

The operational traces and failure-drill workstream is now partially implemented for worker/runtime security paths.

Implemented:

- worker/runtime envelopes now collect structured `security_events` with `correlation_id`, outcome, control, reason, and sanitized auth context
- runtime metrics now count security decisions, event-bus delivery attempts/successes/blocks/failures, and remote dispatch attempts/successes/failures
- runtime traces now record security, event-bus, and remote-dispatch outcomes in envelope payload logs for later inspection and correlation
- dispatcher paths now emit delivery attempt/success/failure traces rather than silently forwarding messages
- focused failure-path tests now cover blocked tenant memory access, time-budget delivery stops, and subscriber exceptions so incident signals are exercised instead of assumed

Still remaining for this workstream:

- surface the new worker/runtime telemetry in operator-facing observability views alongside existing backend audit/readiness data
- expand failure drills beyond the current local runtime coverage to staged deployment and rollback flows

The release promotion and rollback workstream is now implemented for the canonical repository automation path.

Implemented:

- `.github/workflows/release.yml` now packages Helm and installer artifacts into a versioned release bundle and publishes those assets with tagged releases
- `scripts/build_release_bundle.py` now generates `manifest.json`, `promotion-plan.json`, `rollback-plan.json`, and release notes with version, image, and previous-release metadata
- staged promotion now flows through `dev -> stage -> prod` GitHub environments and reuses the existing Foundry secret validation and smoke gates for each step
- `.github/workflows/rollback.yml` now provides a manual rollback workflow that retrieves rollback metadata for a selected release and re-runs environment smoke checks before rollback application
- local operators can build the same release bundle shape with `make release-bundle VERSION=vX.Y.Z`

Still remaining for this workstream:

- connect bundle promotion and rollback metadata to future GitOps apply/reconcile jobs so deployment state changes are fully automated end-to-end
- add regression coverage for workflow-level release metadata consumption once CI/workflow testing is introduced in-repo

The multi-tenant non-prod validation and reliability-proof workstream is now implemented for canonical worker/runtime isolation paths.

Implemented:

- worker/runtime security now rejects conflicting payload tenant assertions when they do not match authenticated tenant identity
- tenant-isolation allow/block decisions are now recorded as structured runtime security events for correlation and auditability
- focused regressions now cover blocked mismatched payload tenant context, allowed matching tenant context, and repeated mixed-tenant message delivery without bucket cross-contamination
- repeated local runtime validation now exercises multiple tenant-scoped messages in one run, providing basic non-prod evidence for isolation and reliability under small burst load

Still remaining for this workstream:

- extend the same proofing into hosted/non-local deployment environments and larger sustained load scenarios
- add operator-facing summaries for tenant-isolation pass/fail evidence in observability surfaces and release evidence bundles

### Phase 2 workstreams

#### 1. Identity and profile convergence

- define immutable runtime profiles for `local-lightweight`, `local-secure/full`, and future hosted operation
- reduce route-by-route auth drift by centralizing profile-driven enforcement where practical
- ensure diagnostics, health, runtime-provider, and policy endpoints have one visibility policy per profile
- document the profile contract in deployment docs and examples

#### 2. Service-to-service transport hardening

- define explicit HTTPS/TLS expectations for worker and A2A transport outside local-only mode
- require stronger subject authentication and transport guarantees for non-local trust boundaries
- keep local-only exceptions explicit and isolated to the local deployment profiles

#### 3. Memory access control

- implement actual authorization checks for memory reads/writes across session, user, agent, workflow, and tenant-like scopes
- ensure retrieval, consolidation, and world-graph projection paths respect the same access model
- add regression coverage for cross-scope denial cases

#### 4. Policy source convergence

- reduce drift between backend fallback policy logic and `policies/*.rego`
- add parity tests for security-critical decisions such as path access, egress, capability claims, and runtime/tool budgets
- establish one source of truth for policy inputs and expected deny behavior

#### 5. Deployment control repair

- repair and validate Helm/network-policy artifacts so they provide real segmentation guarantees
- extend browser/API hardening headers for hosted/TLS profiles where appropriate
- keep secure/full and lightweight defaults aligned with docs, tests, and deployment manifests

### Definition of done for Phase 2

Phase 2 is complete when:

1. runtime profile behavior is centrally defined and consistently enforced
2. non-local worker/service transport has explicit hardened expectations and tests
3. memory operations enforce real access control rather than trusting scope names alone
4. backend fallback logic and Rego policy are covered by parity tests for security-critical decisions
5. Helm/network-policy artifacts are valid and verified instead of merely present
6. docs, manifests, and tests all describe the same hosted-vs-local security posture

### Recommended execution order for Phase 2

1. identity and profile convergence
2. policy source convergence for critical allow/deny paths
3. memory access control
4. service-to-service transport hardening
5. deployment control repair and hosted-profile documentation cleanup

