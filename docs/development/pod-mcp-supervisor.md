# LOCUS-416 increment C — Gateway-fenced MCP tool handling

## Delivered integration

The external CLI contract (PR #118) and authenticated CLI binary/pod
preflight (PR #119) cannot safely reuse `codex_mcp_server`: that older
Codex-specific server creates a **new** Gateway session and `CodingToolset`
from a local configuration file, rather than using the admitted Locus
`PodLease` and the durable `PodActionReceipts` ledger. Doing so for
OpenCode would bypass the very child budget and non-replay boundary the
scheduler expects to enforce.

This increment introduces `TrustedPodMcpHandler` in
`locus_runtime/harness/pod_mcp_supervisor.py` as a **supervisor-owned**
MCP request handler. It is not an MCP subprocess, listener, Unix socket
broker, HTTP service or replacement Gateway. It may only be constructed by
trusted control-plane code with an existing real `PodLease` and an
administrator-created `SandboxedPodToolBroker` (whose existing
`PodActionReceipts` and `PodSandboxBroker` remain the sole executable sink).

### Requests and admission

- Exact bounded JSON-RPC 2.0 envelopes and the MCP initialize, ping,
  tools/list and tools/call surface; all other methods fail closed.
- At most 32 explicit `locus_*` tool grants and exactly the matching
  administrator tool descriptors. Model-controlled tool names and MCP
  catalogs never widen the child Gateway capabilities or broker registry.
- `tools/list` is served only for an active, authenticated child and
  emits only approved names with an empty-input, no-extra-arguments schema.
- `tools/call` takes only an approved name and `arguments: {}`. This
  bounded first increment is for **fixed administrator commands only**;
  it does not permit shell strings, dynamic filesystem patches, arbitrary
  URLs, browser/computer actions, or raw MCP tool inputs.
- The server derives one idempotency key from the authenticated child run
  and JSON-RPC request ID (including scalar type). The caller cannot select
  the receipt key. Replaying a request or changing its target under the
  same request ID is refused by the existing durable action ledger.
- Each actual effect uses
  `SandboxedPodToolBroker.execute_status(lease, ...)` and thus the normal
  Gateway PEP, approved executable/workdir, non-network Docker sandbox,
  write-ahead action receipt, OS action fencing and bounded usage limits.
- Return only validated `status/v1` typed status/digest and a generic error.
  Raw process output, passwords, executor argv, paths, receipt tokens,
  private run data and exception messages never enter MCP responses.

The source is neutral to Ollama, OpenAI, Claude and arbitrary operator
registered CLI engines. An engine may request the **same MCP schema**, but
only a trusted child-specific transport is permitted to reach the endpoint.

### Regression and security verification

`pytest -q tests/unit/test_pod_mcp_supervisor.py`

The test suite drives actual Gateway session, SQLite pod admission, typed
tool result and durable action receipts. A mocked Docker process is used
to avoid claiming real host sandbox attestation. Cases include:

1. MCP capability discovery for only the allowed child tool, initialization
   and bounded protocol handling.
2. One actual supervisor-mediated Gateway tool invocation and its
   persisted `succeeded` action receipt; no process output in result.
3. Duplicate request ID, cross-tool and cross-supervisor retry rejection.
4. Four admitted tool calls then fifth call denied by child budget.
5. Rejection of arbitrary argv, cwd, timeout, shell and extra input keys;
   unknown methods/tools and malformed/oversized ids.
6. Gateway policy denial, revoked parent, unknown tool registry,
   concurrent attempts with the same request ID, and worker process crash
   retaining `uncertain` rather than silently retrying.
7. Static negative check for direct subprocess, host shell, independent
   Gateway, network or eval sinks.

These are real receipt/Gateway unit/regression checks, **not** real Docker
mount tests or a live OpenCode subprocess.

## Prerequisites for LIVE external CLI execution

- The V1/V2 OpenCode profile in #118 deliberately specifies
  `locus_runtime.harness.pod_mcp_stdio_proxy`, not the Codex MCP
  server. This proxy is **not yet installed**. Until then a worker with
  MCP tools fails closed; no model tool request may invoke the legacy
  independent Gateway path.
- A separate confined, authenticated transport must forward the OpenCode
  stdio MCP frames to this trusted supervisor handler. It must attest the
  exact child process/lease and prevent cross-pod impersonation/reuse.
  The model process must never receive a raw `GatewaySession`,
  `PodLease`, parent delegation token or supervisor socket credentials.
- The current default PodSandboxBroker forbids network use.
  Real Ollama or hosted inference needs a separate authenticated,
  allowlisted model proxy. No `--network=host` fallback.
- PR #107's known Docker host-mount mutation risk is still unresolved;
  no production writable host-workspace bind should be enabled until
  a non-mutating staging design and real Docker host-tree integrity tests
  pass.
- Live CLI process-tree cancellation, budgets for ongoing LLM tokens,
  durable long-run heartbeats, general typed MCP inputs, browser/computer
  controls, verified artifacts and replay recovery require subsequent
  acceptance tests.

### Merge rules

This draft is stacked on PR #119, itself stacked on #118. Land those
increments in dependency order after exact-head backend/quality/Windows/
integration/DAST/performance/final gates pass. CodeQL/AppSec/SBOM skips
remain NOT EXECUTED. No modification to CODEOWNERS or protected policy
files, no inference-provider or host-execution route added here.

Rollback is additive: stop constructing this handler and retain the
existing durable receipt and pod tables for uncertain work. No table
migration or cross-tenant credentials are added.
