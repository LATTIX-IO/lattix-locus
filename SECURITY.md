# Security Policy

## Supported scope

This repository is intended to contain only:

- public application code
- public contracts and schemas
- public-safe deployment references
- sample/demo agent assets suitable for local-first development

It must not contain:

- secrets
- customer data
- internal-only prompts
- private Lattix agent definitions
- environment-specific production credentials or overlays

## Reporting a vulnerability

Please report security issues privately to the project maintainers through your established Lattix security contact (<secops@lattix.io>) or private disclosure channel.

Do not include exploit details in public issues or pull requests.

## Secure local-first expectations

The local-first stack is designed to remain functional and secure by default:

- demo agents are sourced from `examples/agents/`
- local compose should not require public users to access private repositories

## Runtime security expectations

The canonical zero-trust runtime and deployment runbook lives in `docs/SECURITY.md`, with threat assumptions in `THREAT-MODEL.md` and architecture notes in `docs/ARCHITECTURE.md`.

Hosted and secure-profile deployments must require authenticated operator access, signed A2A runtime messages, replay protection, egress allowlists, and MCP local-server policy unless remote MCP usage is explicitly confirmed. Check authenticated `/healthz/details` and `/platform/settings` for the `secure_profile` report before promoting or reopening write traffic after rollback.

---

# Engineering Security Standard

This section is the engineering-facing companion to the disclosure policy above. `THREAT-MODEL.md` holds the full threat model; `.github/instructions/lattix-security.instructions.md` holds the path-scoped rules applied automatically to every edit.

Locus sits on a sensitive boundary: operator intent becomes agent execution against real tools, networks, and data. Treat it as a policy and isolation system, not a convenient runner.

## Trust boundaries

- Operator or IDE → control-plane API (`apps/backend/`)
- Control plane → worker runtime over A2A
- Graph execution → tool invocation inside a sandbox
- Sandbox → network egress
- Agent → memory tiers (Redis, Postgres/pgvector, Neo4j)
- Runtime → secret retrieval (Vault)
- Runtime → policy decision point (OPA)
- Platform → external model provider

## Controls

| Control | Where | Requirement |
| --- | --- | --- |
| Runtime profile | `_RUNTIME_PROFILES` in `apps/backend/app/main.py` | Anything non-local pins `local-secure` or `hosted`. The unset default is `local-lightweight`, which permits unauthenticated requests — never rely on it outside a laptop. |
| Operator authentication | OIDC (`LOCUS_AUTH_OIDC_*`), operator session cookie | Header-only actor trust stays disabled in secure profiles |
| Actor authorization | `LOCUS_ADMIN_ACTORS`, `LOCUS_BUILDER_ACTORS` | Least privilege; bootstrap admin is a first-run convenience, not a standing identity |
| Signed A2A runtime headers | `locus_runtime/security.py`, `apps/workers/runtime/security/jwt.py` | Required in `hosted`; verified, not assumed |
| Replay protection | Nonce + TTL, Redis cache with Postgres snapshot fallback | **Fails closed** — `503` when replay state is unavailable |
| Capability grants | Biscuit (`biscuit-python`, Ed25519) — `locus_runtime/grants.py`, verified by the gateway PEP (`locus_runtime/gateway.py`, LOCUS-334) | Grants are stored server-side per principal and never accepted from action input; a covering grant turns an R3 `ask` into `allow` but never overrides a policy deny or R4; standing grants expire after 30 days unless pinned; revocation ids are persisted. No grant authority key (`LOCUS_GRANT_AUTHORITY_KEY`, OS keychain/DPAPI) → no grants (R3 asks). The retired HMAC `CapabilityMinter`/`CapabilityVerifier` tokens are gone; A2A HS256 signing is unchanged (separate follow-up). |
| Policy decisions | OPA — agent, budget, computer use, data classification, filesystem, network egress, network, tool jail | An unavailable PDP is a deny |
| Computer use | `locus_runtime/computer_use/` (agent browser, Windows UIA, macOS AX), `policies/computer_use.rego` — LOCUS-341, see `docs/COMPUTER-USE.md` | Every UI action is a gateway `ui_*` / `browser_*` action classified from the perceived element: typing into password / card / CVV / SSN / OTP fields is R4 (never grantable); send / pay / delete-like controls are R3 and, because screen text is tainted, only a single-use human approval (never a standing grant) allows them; desktop apps need the run's allowlist and the built-in deny list (password managers, banking, OS security and credential prompts, shells, the user's browsers, Locus) always wins; the agent browser has its own profile and every connection passes the egress allowlist (request interception plus a loopback egress proxy); panic (`POST /computer-use/panic`) latches and cancels in-flight actions. macOS is unverified on real hardware. |
| Guardrails | `locus_runtime/guardrails.py` — prompt render, DLP; capability-scoped envelopes fail closed (capabilities are not carried in messages) | Applied to output paths; redaction before persistence and logging |
| Tool isolation | `locus_runtime/sandbox.py` | Explicit strategy per host platform — `kernel-bwrap` (Linux), `kernel-seatbelt` (macOS), `windows-appcontainer` (Windows), `hardened-docker` — with declared capabilities and no silent downgrade |
| Egress control | Sandbox egress gateway, per-integration `egress_allowlist` | Deny by default; allowlist is data, not code |
| Secret storage | Vault (`hvac`), installer-managed mirroring | Secrets never in the repo, logs, or memory records |
| Audit integrity | Hash-chained, signed events (`locus_runtime/events.py`) | No execution path bypasses the event log |
| Transport and headers | Envoy, `apps/backend/app/security_headers.py`, `request_security.py` | Security headers and request validation are not optional middleware |

## Hard limits

- Never generate, commit, print, or log secrets, credentials, tokens, keys, auth headers, or session IDs.
- Never log prompts, tool payloads, memory contents, or retrieved evidence without redaction.
- Never include real PII, customer data, or production identifiers in tests, docs, or examples. Use `<API_KEY>` / `<REDACTED>`.
- Never invent crypto, token formats, or random ID schemes. Use vetted libraries and existing project patterns.
- Never weaken a fail-closed path to make a test or a local run pass.
- Never commit private Lattix agent definitions. Demo assets live in `examples/agents/`; private assets come from `LOCUS_AGENT_ASSETS_ROOT`.
- Replace the placeholder `A2A_JWT_SECRET` in the Helm chart before applying it anywhere.

## Review checkpoints

Require explicit security review when a change touches:

- runtime profile resolution, authentication, or actor allowlists
- signed A2A headers, capability minting/verification, or replay handling
- any `policies/*.rego` file or the OPA client
- sandbox strategy selection, seccomp profile, or `SandboxCapabilities`
- egress allowlists, the sandbox egress gateway, or Envoy configuration
- guardrail filter chain, DLP, or redaction behavior
- memory persistence, consolidation, or world-graph projection paths
- the event hash chain or event signing
- the installer's secret handling or state manifest
- Helm secrets, network policies, RBAC, seccomp profile, or RuntimeClass

## Known posture gaps

Do not describe these as covered:

- `k8s-gvisor` and `k8s-kata` are `IsolationStrategy` values with no implementing strategy. Selecting them must not silently resolve to a weaker tier.
- `budget_policy.rego` has no test file; every other policy does.
- Control-plane persistence failures are swallowed; definitions can be lost on restart without a signal.
- `apps/backend/` — the entire control plane — is not covered by `make typecheck` (491 `mypy --strict` errors).
- **`apps/backend/app/main.py:1587` calls `platform.system()` without importing `platform`** — a latent `NameError` on that path, currently flagged by `ruff` as `F821`.
- The test suite does not collect (`tests/` lacks `__init__.py`, breaking `tests.harness`), so there is no automated security-regression signal until that is fixed.

Windows tool confinement **is** implemented — `_WindowsAppContainerStrategy` with the `windows-appcontainer` tier, selected fail-closed rather than downgrading to a bare Job Object (`LOCUS_FORCE_WINDOWS_APPCONTAINER` forces it).
