# LOCUS-413 follow-on: Gateway-bound worker dispatch driver

## Boundary and rollout

\`GatewayTaskDriver\` is the control-plane driver connecting the durable
\`DurableTaskScheduler\` task DAG to an administrator-registered \`TrustedWorker\`
adapter. It is **not** a model API, subprocess launcher, browser driver or
secondary policy decision point.

The dispatcher:

1. Reads the accepted, parent-bound graph and atomically claims eligible nodes.
2. Selects a **pre-registered** engine by required tool capabilities, in the
   administrator-defined registration order. Model output cannot supply argv,
   binary, endpoint, toolset, credential or raw Gateway session.
3. Reserves the node's entire declared action/tool/token/wall/spend ceiling
   against the existing parent pod ledger, then creates the child session
   via \`PodReservationLedger.admit\`.
4. Invokes the adapter with a \`PodLease\` in a bounded thread pool. The
   adapter is trusted supervisor code: it is responsible for running the
   untrusted agent process through the OS-isolated broker and for enforcing
   per-worker timeouts and cancellation. **Threads are not sandboxes.**
5. Accepts only a well-formed SHA-256 artifact digest and cumulative
   \`ResourceBudget\`. The ledger enforces the reservation and usage.
6. Closes the child with the digest (not raw artifact data), then calls the
   **separately configured** \`IndependentVerifier\`. No model/worker verdict
   can stand in for trusted independent verification.
7. Marks the node \`verified\` on accepted evidence, or \`blocked\` on
   independent rejection. Unfinished or ambiguous actions remain held and
   cannot be automatically replayed.

When one wave finishes, \`run_until_idle\` may start the next verified wave.
It returns \`done\` only when **every** graph node is independently verified.
Otherwise it returns an explicit \`blocked\`, \`waiting\`, \`stopped\` or
\`budget\` outcome. Caller continuation after \`waiting\` requires a separate
supervisor process/lease/reconciliation policy.

## Engine and provider separation

\`EngineBinding(engine_id, supported_tools, worker)\` binds a trusted, installed
execution adapter, not a model endpoint. A worker can use local Ollama through
the unified \`ModelClient\`, a hosted provider, or a vendor engine such as
OpenCode/Codex after its own CLI adapter passes the same Gateway, isolation,
MCP mediation, version-pinning and tool parity checks.

No user-installed skill, dynamically discovered MCP server or model-generated
command is automatically registered as a trusted worker. Registration and
requested tool capabilities are distinct from actual Gateway authorization.

An adapter implementing \`TrustedWorker\` must never give an untrusted process
the \`PodLease\` Python object or the raw GatewaySession. Instead, untrusted
work is constrained by the existing \`SandboxedPodToolBroker\`/
\`PodSandboxBroker\` or a separately audited equivalent transport. Direct
host execution and unrestricted vendor CLI tools are not supported by this
increment. Not all providers have the same native tool capabilities.

## Crash and cancellation invariants

- The parent budget and concurrent execution ceiling remain durable and
  shared across cooperating supervisors.
- A worker exception, invalid report, admission error or invalid settlement
  produces \`uncertain\`. Resource holds are not automatically released;
  reconcile the existing receipt/lease ledger before attempting a new run.
- A worker that returns a successful digest but whose independent verifier
  fails is terminal \`blocked\` for this graph revision, not eligible for retry.
- A user cancellation prior to claim does not launch anything or reserve
  budget. Runtime cancellation of an already executing adapter must be
  implemented by that adapter's OS-level process manager.
- \`run_until_idle\` is bounded by a finite \`max_waves\` and never counts
  a dormant active child as complete.
- No raw prompts, credentials, retrieved content, command output or artifact
  bytes are persisted by this driver. Return reason codes are bounded and
  intentionally exclude exception text.

## TDD and acceptance checks

\`pytest -q tests/unit/test_task_scheduler.py tests/unit/test_task_dispatch_driver.py\`

Regressions cover deterministic diamond waves with real Gateway child leases;
two simultaneous siblings; restart without replay; independently rejected
evidence; adapter capability mismatch (no grant or budget hold); malicious
worker exception redaction; invalid artifact digest; over-budget settlement;
concurrent parent limits; expired active leases becoming uncertain; explicit
cancellation; engine selection/registry validation; and forbidden direct
process execution paths.

Full exact-head CI (backend, quality/type, integration, Windows, frontend,
DAST, performance, final gates) remains required. Skipped CodeQL/AppSec/SBOM
is **not** passing security-scanner evidence.

## Limitations and next integration

The adapter *port* and real scheduling-to-pod ledger bridge now exist, but a
production OpenCode CLI adapter or browser/computer driver is not declared
ready without its own sandbox, network egress, version, permissions, MCP
config, process cancellation, and observable tool isolation attestations.

The general-purpose live API/worker service must explicitly call this driver
with a trusted registry, independent domain verifier, bound intent and valid
parent Gateway session. There is no new REST or desktop endpoint in this PR.
Dynamic graph revisions, bounded repair, cross-device recovery, remote tool
execution, RSI promotion and artifacts with versioned lineage require
independent increments and tests.

**Rollback:** disable the dispatch driver and continue using the existing
native runtime. Keep SQLite task/pod records until all uncertain child
attempts are reconciled. Do not truncate active attempt histories.
