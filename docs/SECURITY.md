# Security

See `THREAT-MODEL.md` for the canonical current-state vs target-state security expectations, trust boundaries, deployment-mode assumptions, the historical migration record for the removed `lattix_frontier/` package, and known failure modes.

## Capability tokens

The current implementation mints signed capability envelopes, supports attenuation, and verifies scope and expiry before agent execution.

Lifecycle:

1. Orchestrator mints a token.
2. Token is attached to an envelope.
3. Guardrails verify scope and expiry.
4. Agent server requires a token on `/v1/envelope`.

## OPA policies

Policies live in `policies/` and are tested through `policies/tests/`.

## Vault

Secure local deployments use HashiCorp Vault as the local secret-management service. The secure Compose stack runs Vault with file-backed storage mounted on the persistent `vault-data` Docker volume, and the installer now bootstraps Vault as needed, writes installer-generated passwords/secrets into KV storage, and mirrors installer configuration snapshots there for durable recovery.

Installer compatibility env files still exist for Compose/runtime startup, but they are no longer the only durable copy of sensitive setup state. Kubernetes is still designed to migrate to Kubernetes auth and per-service-account roles.

## DLP pipeline

Tier 1 uses regex and lightweight classification now. The code includes TODO markers for deeper Presidio and GLiNER integration.

## Network isolation

Envoy is the intended egress boundary. Helm templates include a strict default-deny baseline with explicit allow rules.

For local tool execution, the sandbox subsystem now uses a dedicated internal Docker network and a dual-homed `sandbox-egress-gateway` service so jailed containers do not get direct outbound internet access.

## Audit integrity

Every recorded event can be chained through SHA-256 and verified with `scripts/verify_event_chain.py` against the repo's configured event-chain artifact.

## Human gates

Confidential and restricted workflows can require human approval before completion.

## Tool jail

All new secure tool execution should go through the worker/runtime sandbox boundary in `apps/workers/`, with egress mediation enforced by the secure Compose topology and platform policies described in `THREAT-MODEL.md`.

## Staged MCP connections

MCP endpoints now follow a staged approval model instead of relying on ad hoc per-node server URLs alone.

Lifecycle:

1. Builder saves a staged MCP connection from a backend-owned starter catalog.
2. Validation checks the staged connection against `allowed_mcp_server_urls`, local-network requirements, declared capabilities, permission scopes, data access, and egress hosts.
3. Only an admin-approved staged MCP connection can be selected in the builder canvas through `mcp_connection_id`.
4. Runtime resolves `mcp_connection_id` to the approved server URL and auth context before native tool execution.

Security properties:

- Secret refs remain server-side and are masked in list responses.
- Draft or validation-failed MCP records cannot be executed.
- Tool policy can admit approved MCP calls through the explicit MCP server allowlist without widening unrelated tool permissions.
- Builders can still enter raw `mcp_server_url` fields, but the preferred path for supported integrations is the staged catalog and approval workflow.

## Cortical column zero-trust runbook

The cortical assembly runtime uses the same zero-trust posture as the rest of the secure control plane: no column, service, tenant label, or runtime message is trusted because of locality. The backend admits cognitive work only after checking identity, tenant ownership, column capability, assembly policy, replay status, sensitive-data redaction, and deployment profile requirements.

### Required controls

| Control | Required behavior | Primary evidence |
| --- | --- | --- |
| Signed cognitive messages | Column messages must include tenant, assembly, source column, target column, nonce, timestamp, and trusted subject metadata, then pass A2A signature checks before mutation | `/internal/cognition/messages/admit`, `tests/unit/test_cognitive_transport.py`, `apps/backend/tests/test_cortical_assembly_endpoint.py` |
| Replay and idempotency | Request nonces and semantic cognitive replay markers block duplicate column-message mutation, while completed assembly replays return the original outcome | `frontier_runtime/events.py`, `frontier_runtime/persistence.py`, cortical endpoint replay tests |
| Column least privilege | Column kinds have bounded capabilities, and unknown kinds deny by default | `frontier_runtime/cognition.py`, `tests/unit/test_cognition.py` |
| Assembly admission | Assembly definitions are checked for bounds, required columns, tenant/provider/model/tool policy, and budget limits before runtime execution | `frontier_runtime/assembly_runner.py`, `tests/unit/test_assembly_runner.py` |
| Runtime policy gate | Every column step checks auth context, tenant ownership, capability, provider/model/tool/retrieval/network allowlists, budget counters, and audit emission | `admit_column_runtime_step(...)`, cortical endpoint runtime-gate tests |
| Commitment gate | Ready outcomes require evidence/evaluation participation, no unresolved veto/blocker, confidence threshold satisfaction, high-risk human approval where configured, and an audited trail | commitment-gate tests in `tests/unit/test_cognition.py` and `tests/unit/test_assembly_runner.py` |
| Projection safety | Causal graph projection is tenant-authorized, size-bounded, idempotent by assembly ID, and non-corrupting on unavailable/write-failed graph services | projection safety tests in `apps/backend/tests/test_generated_artifacts.py` and cortical endpoint tests |
| Redaction | Audit, belief metadata, evidence refs, prompts/outputs, graph projection payloads, and error responses must not expose raw secrets | Slice 11 redaction tests in `apps/backend/tests/test_cortical_assembly_endpoint.py` |

### Deployment profile requirements

| Profile | Operator/API auth | A2A runtime headers | Signed messages | Replay protection | Egress allowlist | MCP local policy | Unsafe config behavior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `local-lightweight` | Optional for local development | Optional | Recommended where used | Enabled by default controls where configured | Enabled by default but can be relaxed for local development | Local server required by default | Remains usable for local development |
| `local-secure` | Required through profile/effective controls | Not globally required | Required for internal secure paths | Required | Required | Required unless explicitly confirmed | Reports degraded health/settings when required controls are disabled |
| `hosted` | Required | Required | Required | Required | Required | Required unless `FRONTIER_CONFIRM_REMOTE_MCP_SERVERS=true` | Fails closed or reports blocked status |

Hosted or secure-profile operators should check `/platform/settings` and authenticated `/healthz/details` for `secure_profile.status`, `secure_profile.failures`, and the effective immutable controls. Public `/healthz` remains minimal and may report `blocked` without exposing detailed failures.

### Rollback procedure

1. Freeze new hosted profile changes and capture current `/healthz/details`, `/platform/settings`, and recent cognition audit events.
2. Roll back the application image or release bundle to the previous known-good version. Do not delete the persistent state store, Postgres, Redis, Neo4j, or audit/event-chain artifacts as part of normal rollback.
3. Keep `A2A_JWT_SECRET`, runtime profile, tenant identity settings, and egress/MCP policy settings stable unless the incident is specifically a key or policy compromise.
4. Run the focused verification commands below before reopening write paths or re-enabling traffic.
5. Confirm duplicate cognitive messages still return conflict/replay behavior and completed assembly replay still returns the original outcome.
6. Review projection status and audit events for `write_failed`, `unavailable`, blocked policy decisions, missing redaction markers, or unexpected tenant mismatch events.

If the incident involves leaked signing material, rotate the A2A secret and invalidate live runtime clients as a separate security event. Expect in-flight signed messages to fail until all senders share the new secret.

### Monitoring checklist

- `secure_profile.status` is `ok` for hosted before rollout proceeds.
- Authenticated health details report expected profile, source, and secure controls.
- Cognition audit events appear for assembly admission/rejection, policy decisions, column started/completed/blocked, message accepted/rejected, commitment finalized/escalated, and projection success/failure.
- Runtime security counters and traces include tenant-isolation, event-bus, and remote-dispatch outcomes with correlation IDs.
- Projection failures report `skipped`, `unavailable`, or `write_failed` without corrupting persisted causal state.
- Redaction markers appear where sensitive payloads were scrubbed, and raw secrets do not appear in audit/event/projection payloads.

### Verification commands

Run the focused zero-trust suite before merging or promoting a behavior-changing cortical/runtime slice:

```text
python -m pytest tests/unit/test_cognition.py tests/unit/test_assembly_runner.py tests/unit/test_causal_state_persistence.py tests/unit/test_cognitive_transport.py
python -m pytest apps/backend/tests/test_cortical_assembly_endpoint.py
python -m pytest apps/backend/tests/test_generated_artifacts.py -k "secure_profile or runtime_profile or projection or tenant_allowed_runtime or tenant_denied_runtime"
python -m py_compile apps/backend/app/main.py apps/backend/app/request_security.py frontier_runtime/cognition.py frontier_runtime/assembly_runner.py frontier_runtime/events.py frontier_runtime/envelope.py frontier_runtime/persistence.py
```

For deployment evidence, also run the repo-level policy and chart checks when the required tools are available:

```text
make policy-test
make helm-validate
```
