# LOCUS-416 increment B — pinned external engine and Gateway pod preflight

## Security goal

Connect the versioned OpenCode CLI contract to the already-merged durable
task-graph scheduler and Gateway pod reservation/lease infrastructure, without
granting new tools or running an unconfined external program.

A trusted supervisor uses `preflight_opencode_pod(profile, lease,
observed_version=..., observed_effective_config=...)`. It must supply a
compiled profile from increment A, an admitted PodLease from the existing
`DurableTaskScheduler`, and observations collected by an **independently
sandboxed, trusted** CLI inspection process. Values echoed by a model or
agent are not valid observations.

The preflight executes these checks in order:

1. The existing SQLite ledger confirms the pod is active and the child run
   ID matches the reservation; a revoked/expired parent or pod cannot
   authenticate.
2. Every field of the immutable launch plan is recomputed through
   `compile_opencode_launch` and compared to the submitted plan, preventing
   arbitrary replacement of argv, model, tools, environment, config JSON,
   digest, platform or executable path.
3. The only MCP-visible names accepted are the documented `locus_*`
   namespace of `codex_mcp_server`: `locus_execute_bash`, `locus_search`,
   `locus_str_replace_editor` and `locus_run_tests`. Each resolves to the
   underlying Locus tool that **must already appear in the child PodLease's
   delegated Gateway capability list**. Unrecognized MCP tools or additional
   servers are rejected.
4. The CLI version observation must exactly match the operator-accepted
   dialect and version. No pre-release, multiline output, unknown major,
   shell string or model-supplied banner is accepted.
5. The effective config is checked against the trusted compile contract,
   refusing native file/shell/plugin/skill/subagent/provider escalations.
6. The executable must exist at its canonical, non-symlink path. On POSIX,
   a group/world-writable or non-executable binary is rejected. The existing
   bounded, no-follow file-descriptor SHA-256 verification runs on the
   registered file and refuses in-flight file mutation.
7. The ledger checks pod liveness again after hashing to catch revocation or
   expiration during inspection.
8. The function returns a deterministic `verified_preflight_unexecuted`
   correlation digest bound to the child run, parent run, graph digest,
   reservation, CLI digest/config, provider and exact tool allowlist.

**Critical distinction:** this correlation record is NOT an authorization
token, signed attestation, independent artifact proof, safe process runtime,
or evidence that OpenCode ran. A filesystem measurement can have a TOCTOU
replacement after hashing. An actual implementation must use an immutable
container digest or race-free OS file-handle launch, alongside effective
sandbox, egress, process kill-tree, MCP and tool receipts, before work starts.

## Verification

`pytest -q tests/unit/test_opencode_contract.py tests/unit/test_opencode_pod_preflight.py`

Regression/security test coverage:
- Real Gateway/SQLite admitted child identity and durable plan binding;
- V1 and V2 profile dialects;
- Correct digest, tampered executable, group/world writable binary;
- Symlinked executable or directory parent denial;
- Version spoofing, unknown version, multiline error injection;
- Parent revocation and disabled/expired active pod;
- Requested MCP grants beyond delegated capabilities;
- Immutable dataclass tampering with argv, model, tool lists, config,
  digests, environment and binary identity;
- Foreign config extensions, provider switching and snapshot changes;
- No host-executed CLI, subprocess, network call or duplicated Gateway PEP.

All tests use a deliberately **non-executed fake binary** and real in-memory
policy/SQLite authorization. They are not live OpenCode/Ollama smoke tests.

## Follow-on production gates

The actual external runner must:

- Verify the installed `opencode --version` using a controlled process
  contained *before* running the version command; pin both binary digest
  and configuration source(s).
- Create an OS-isolated execution environment for the CLI and any local
  MCP descendants, using the hardened PodSandboxBroker. Resolve the separate
  PR #107 Docker mount-target host mutation defect before allowing writable
  host mounts. No direct unconfined host subprocess fallback.
- Supply a controlled inference proxy with explicit egress allowlisting to
  local Ollama or approved hosted providers, not `--network=host`, shared
  Docker sockets, inherited cloud keys or arbitrary TCP destinations.
- Bind all MCP tool calls to the pod's Gateway session and action receipt
  ledger; deterministic action IDs and explicit uncertain outcomes prevent
  side-effect replay after crash/restart.
- Enforce bounded wall time, token/spend metering, process-tree kill, output
  redaction and receipt-backed typed artifacts before the trusted independent
  verifier can mark a DAG node as complete.
- Verify at least one live multi-agent task with Ollama/OpenCode plus denial
  tests for file/process/network/skill/browser/computer attempts outside the
  admitted envelope. Demonstrate provider-neutral CLI capability negotiation.

CI on the exact PR HEAD must pass backend, quality/type, Windows, integration,
frontend, DAST, performance and required-gates. The current repository may
skip CodeQL/AppSec/SBOM when disabled; those are explicitly NOT EXECUTED and
must not be represented as a release security pass.

## Rollback

Increment B is additive and not registered as an executable backend.
Disable or omit the trusted preflight integration without schema migration,
new network path, elevated privileges or altered pod receipts. Preserve
unreconciled admitted child attempts in the existing SQLite ledger.
