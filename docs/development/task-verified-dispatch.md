# LOCUS-413 — durable verified task-DAG dispatch (first supervisor bridge)

## Purpose and explicit scope

Locus has a versioned, model-proposed task DAG validator (LOCUS-411) and
durable Gateway-bound pod reservations (LOCUS-433), but no durable link
between a verified predecessor's evidence and dispatch of its dependents.

This increment provides that link, not a second orchestrator or PEP:

- \`DurableTaskScheduler.bind(compiled, parent, max_tool_calls)\` registers the
  *trusted* parent against the existing pod ledger and persists a fixed
  accepted graph. Repeated binds must match the exact digest and identity.
- \`ready\` returns only pending nodes whose every dependency is **verified**,
  not merely reserved, executed, or closed.
- \`claim_next\` serializes per-node claims with \`BEGIN IMMEDIATE\`, caps
  inflight work, and gives a deterministic per-run/node idempotency key.
- \`reserve\` delegates all budget checks and durable holds to
  \`PodReservationLedger.reserve\`.
- \`admit\` creates an **attenuated Gateway child session** only via
  \`PodReservationLedger.admit\`; the LLM-proposed graph never grants rights.
- A worker can run only through the resulting \`PodLease\` and existing broker.
  After it closes the lease, \`verify_closed\` requires (1) the same closed
  reservation, (2) its exact evidence digest, (3) a trusted independent
  verifier's successful decision. Only then can dependents become ready.
- Ambiguous/expired child work is quarantined, never automatically retried.

The supervisor schema is a small additive \`task_dispatch_node\` table inside
the existing pod-reservation SQLite database. Raw prompts, tool outputs,
artifacts and credentials are **not** persisted in this table.

## Where this connects

Trusted TaskIntent + bounded model proposal
  -> \`compile_task_plan\` (DAG, budgets, authority ceiling)
  -> \`DurableTaskScheduler\` (claim, dependencies, concurrency)
  -> \`PodReservationLedger\` (parent/child budget, lease, Gateway session)
  -> existing child worker/agent runtime adapter
  -> \`PodLease.close(total=..., evidence=<artifact_sha256>)\`
  -> independent domain verifier
  -> \`verify_closed(..., artifact_digest=<artifact_sha256>)\`
  -> next wave

**Engine neutral:** \`engine\` is the engine identity recorded on the
Gateway-issued child session. It is not a CLI command, and cannot cause
OpenCode, Codex, Ollama, hosted models, browser, computer, MCP or skills to
bypass the Gateway. Existing \`ModelClient\` and native harness continue to
serve Ollama and other provider integrations. A separate engine-adapter
increment is required for fully mediated OpenCode/Claude Code CLI execution.

## Non-goals / limitations

- No automatic long-running background worker, external CLI launch or live
  model/tool call was added. The caller must supply a legitimate worker runner
  using the \`PodLease\` and a verifier configured in trusted supervisor code.
- No automatic replay of an active/uncertain child after process crash.
  An expired unexecuted *reserved* hold may be released by the existing ledger
  recovery; active/admitting attempts require manual reconciliation.
- A crash after Gateway child admission but before supervisor state persistence
  conservatively requires reconciliation. Retry cannot mint a second child
  session under the same reservation.
- The evidence-verifier callback is **trusted code, not an API argument from
  an agent/model**. Passing an agent-controlled \`lambda: True\` would violate
  the caller's trust boundary.
- Dynamic graph revisions/replanning are not covered; revised plans require
  a new authenticated parent run. Broader domain verification, workload
  selection and resource coordination remain separate increments.

## Test-driven acceptance

- Diamond DAG releases sibling nodes concurrently, and a dependent only
  after **both** predecessor pods independently verify closed evidence.
- Restart before child admission reuses the same reservation id; repeat
  admission is denied, including changed budgets/revisions.
- Two scheduler processes cannot claim the same node or exceed the configured
  inflight count.
- Missing closure, wrong artifact digest, failed independent verifier,
  cross-run claim or changed plan digest cannot promote or dispatch work.
- Expired active reservations transition to uncertain without replay or budget
  release.
- No executable, subprocess, network proxy, tool, browser, MCP or skill
  authority is added. Every action still passes through existing Gateway and
  sandbox controls.

Run \`pytest -q tests/unit/test_task_scheduler.py\` and the standard exact-head
backend unit, frontend, integration, quality/type, Windows, DAST, performance
and required-gates workflows. Skipped CodeQL/AppSec/SBOM workflows are not
passing evidence.

## Rollout / rollback

Feature is additive and unreferenced by the live API until worker wiring is
explicitly enabled. Rollback stops using the scheduler; existing run and pod
tables continue functioning. Do not delete \`task_dispatch_node\` rows while
a child is active/uncertain; retain reconciliation evidence first.
