# Cortical Column Zero-Trust MVP Implementation Plan

## Slice 2: Signed Cognitive Message Admission

### Slice 2 Files

- `envelope.py`
- `events.py`
- `request_security.py`
- `main.py`
- `test_cognitive_transport.py`

### Slice 2 Implementation

- Add a backend admission function for `ColumnMessage`.
- Require signed envelope/event metadata:
  - `tenant_id`
  - `assembly_id`
  - `source_column`
  - `target_column`
  - `nonce`
  - `timestamp`
  - trusted subject
- Reuse existing A2A signature and nonce patterns where possible.
- Fail closed on unsigned, stale, replayed, or cross-tenant messages.

### Slice 2 Tests

- Valid signed column message accepted.
- Unsigned message rejected.
- Replayed nonce rejected.
- Wrong tenant rejected.
- Unknown source column rejected.

## Slice 3: Column Capability Policy

### Slice 3 Files

- `cognition.py`
- `assembly_runner.py`
- `main.py`
- `test_cognition.py`

### Slice 3 Implementation

- Define capabilities by `ColumnKind`, for example:
  - `goal`: read input, emit goal beliefs
  - `evidence`: retrieval only
  - `evaluation`: score/veto only
  - `synthesis`: propose commitment only
  - `uncertainty`: emit blockers
- Add a single capability check before model calls, tool calls, retrieval, memory writes, and commitment publication.
- Deny unknown column kinds by default.

### Slice 3 Tests

- Evidence column cannot publish commitment.
- Synthesis column cannot perform retrieval unless explicitly granted.
- Evaluation column can veto/block.
- Unknown column kind is rejected.

## Slice 4: Assembly Definition Admission Control

### Slice 4 Files

- `cognition.py`
- `assembly_runner.py`
- `main.py`
- `test_assembly_runner.py`

### Slice 4 Implementation

- Validate assembly definitions before execution:
  - max columns
  - allowed column kinds
  - max iterations
  - max messages
  - required goal/evidence/evaluation/synthesis minimum set
  - allowed tenant/provider/model/tool policy
- Reject unsafe assemblies before persistence or runtime execution.

### Slice 4 Tests

- Missing required columns rejected.
- Too many columns rejected.
- Unsupported column kind rejected.
- Valid minimum assembly accepted.

## Slice 5: Shared Column Runtime Gate

### Slice 5 Files

- `main.py`
- `assembly_runner.py`
- `test_cortical_assembly_endpoint.py`

### Slice 5 Implementation

- Introduce one shared function for column execution admission:
  - auth context
  - tenant ownership
  - capability check
  - runtime model/provider allowlist
  - network/tool/retrieval allowlist
  - budget counters
  - audit event
- Route cortical assembly execution through that gate before each column step.

### Slice 5 Tests

- Policy gate allows valid column step.
- Blocks over-budget step.
- Blocks disallowed model/provider.
- Blocks missing tenant context.
- Emits audit events for allow and deny.

## Slice 6: Typed Belief And Evidence Validation

### Slice 6 Files

- `cognition.py`
- `persistence.py`
- `test_cognition.py`
- `test_causal_state_persistence.py`

### Slice 6 Implementation

- Bound `BeliefRecord` fields:
  - key length and pattern
  - value JSON size
  - evidence ref count/length
  - rationale length
  - metadata size
  - confidence finite and `0..1`
- Reject non-JSON-safe values before persistence.
- Keep redaction hooks for sensitive values.

### Slice 6 Tests

- Oversized belief value rejected.
- Non-finite confidence rejected or clamped consistently.
- Too many evidence refs rejected.
- Metadata with unserializable values rejected.

## Slice 7: Commitment Gate

### Slice 7 Files

- `cognition.py`
- `assembly_runner.py`
- `main.py`
- `test_cognition.py`
- `test_assembly_runner.py`

### Slice 7 Implementation

- Add final commitment validation:
  - evidence column participated
  - evaluation column participated
  - no unresolved blocker/veto
  - confidence threshold met
  - human approval required for configured high-risk outcomes
  - signed/audited decision trail present
- Prevent direct runtime shortcut to ready outcome.

### Slice 7 Tests

- Synthesis-only commitment rejected.
- Veto blocks commitment.
- Low confidence escalates.
- Human approval policy blocks high-risk commitment.
- Valid commitment succeeds.

## Slice 8: Tool And Retrieval Least Privilege

### Slice 8 Files

- `main.py`
- `test_runtime_streaming.py`
- `test_generated_artifacts.py`

### Slice 8 Implementation

- Bind tool/retrieval permissions to column kind and assembly policy.
- Require explicit permission for:
  - external HTTP tools
  - MCP servers
  - retrieval source URLs
  - skill-routed integrations
- Prefer staged MCP connections for supported providers so tool-call nodes can reference an approved `mcp_connection_id` instead of bypassing validation with ad hoc endpoint entries.
- Default to deny when no policy is present.

### Slice 8 Tests

- Evidence column can retrieve approved source.
- Evidence column cannot call arbitrary tool.
- Tool column can call approved integration only.
- Skill-routed integration still respects tenant and egress policies.
- Staged MCP connections must validate and receive admin approval before runtime can resolve them.

## Slice 9: Tenant-Scoped Model Runtime Policy

### Slice 9 Files

- `main.py`
- `test_generated_artifacts.py`

### Slice 9 Implementation

- Extend model/provider allowlist from user-only to tenant/assembly effective policy.
- Validate:
  - graph node model
  - agent `model_defaults`
  - runtime payload override
  - fallback model
- Ensure fallback never escapes tenant provider policy.

### Slice 9 Tests

- Runtime override to disallowed provider rejected.
- Fallback chooses tenant-allowed model.
- Agent graph subtype cannot bypass provider policy.
- Environment provider cannot override tenant deny policy.

## Slice 10: Replay And Idempotency Hardening

### Slice 10 Files

- `persistence.py`
- `events.py`
- `main.py`
- `test_cortical_assembly_endpoint.py`

### Slice 10 Implementation

- Persist replay markers for cognitive messages and commitment outcomes.
- Reject duplicate column message mutation.
- Keep idempotent replay for completed assemblies.
- Add stable replay status in endpoint response.

### Slice 10 Tests

- Duplicate message does not mutate belief history twice.
- Duplicate commitment does not append outcome twice.
- Replay returns original outcome.
- Cross-tenant replay rejected.

## Slice 11: Sensitive Data Redaction

### Slice 11 Files

- `main.py`
- `persistence.py`
- `test_cortical_assembly_endpoint.py`

### Slice 11 Implementation

- Redact secrets in:
  - audit events
  - belief metadata
  - evidence refs
  - model prompts/outputs
  - graph projection payloads
- Reuse existing redaction helpers where possible.
- Add stable `redacted: true` metadata for observability.

### Slice 11 Tests

- API keys not present in audit events.
- Auth headers not persisted in belief state.
- Sensitive payload not projected into Neo4j fake graph.
- Error responses do not leak raw internal payloads.

## Slice 12: Observability And Audit Events

### Slice 12 Files

- `main.py`
- `test_cortical_assembly_endpoint.py`

### Slice 12 Implementation

- Emit structured audit events for:
  - assembly admitted/rejected
  - column started/completed/blocked
  - column message accepted/rejected
  - policy decision
  - commitment finalized/escalated
  - projection success/failure
- Include `tenant_id`, `assembly_id`, `column_id`, `actor`, `decision`, and stable reason codes.

### Slice 12 Tests

- Allowed execution emits expected audit sequence.
- Rejected execution emits blocked audit with reason.
- No sensitive payloads in audit events.

## Slice 13: Projection Safety

### Slice 13 Files

- `main.py`
- `platform_services.py`
- `test_generated_artifacts.py`

### Slice 13 Implementation

- Require tenant authorization before causal graph projection.
- Bound projection size:
  - max columns
  - max beliefs
  - max histories
  - max outcome records
- Projection failure must not corrupt causal state.
- Keep projection idempotent by assembly ID.

### Slice 13 Tests

- Wrong tenant projection denied.
- Oversized projection rejected.
- Unavailable graph reports safe status.
- Write failure preserves persisted state.

## Slice 14: Secure Profile Deployment Checks

### Slice 14 Files

- `main.py`
- `request_security.py`
- `test_generated_artifacts.py`
- `test_cortical_assembly_endpoint.py`

### Slice 14 Implementation

- Add startup/profile validation helper for hosted/secure profile:
  - auth required
  - A2A runtime headers required
  - signed messages required
  - replay protection required
  - egress allowlist enabled
  - MCP local policy enforced unless explicitly confirmed
- Fail closed or report unhealthy when required controls are disabled.

### Slice 14 Tests

- Hosted profile rejects insecure settings.
- Local dev remains usable.
- Immutable controls reflected in `/platform/settings`.
- Health/details reports degraded or blocked status for unsafe config.

## Slice 15: Docs And Runbook

### Slice 15 Files

- `ARCHITECTURE.md`
- `SECURITY.md`
- `THREAT-MODEL.md`
- `README.md`

### Slice 15 Implementation

- Document:
  - zero-trust column assumptions
  - tenant isolation model
  - signed message flow
  - policy gate flow
  - deployment profile requirements
  - rollback and monitoring
- Add verification commands and expected test suites.

### Slice 15 Tests

- No runtime tests required, but docs should be updated in the PR with each behavior-changing slice.
