# LOCUS-472 / LOCUS-470/C — native Locus + Codex two-specialist execution

**Status:** implementation branch / release candidate, **not yet qualified**. Do not enable by default, mark Done, or publish without the live Linux acceptance and exact-head CI.

## Execution model

A trusted supervisor invokes `execute_two_specialists(plan, parent, scheduler, broker, ...)` only with a preaccepted TaskPlan containing the exact `native` → `codex` DAG and durable engine pins. The existing SQLite scheduler admits and accounts for each child separately.

1. **Native Locus specialist:** `NativeLocusDraftWorker` invokes the real pinned Deep Agents runtime, with the authenticated native child Gateway session, locally pinned Ollama model, a strict no-write/no-shell/no-subagent tool envelope, bounded model calls, and a verified `submit` result. The supervisor persists its answer using a fixed Python entrypoint inside the stage-only Docker kernel boundary, hashes the exact bytes, and independently seals one `draft.txt` into detached escrow.
2. The native child closes with receipt and manifest evidence. The existing independent TaskScheduler verifier checks durable parent/child lineage, source revision, artifact manifest, and sealed escrow. Until that succeeds, the `codex` node cannot be admitted.
3. **Verified handoff:** the supervisor verifies the still-ready escrow bytes and inode, recipient live PodLease/StageLease, different child identities, identical parent TaskPlan and original source, pinned Codex engine, bounded UTF-8, and produces a hash-bound `VerifiedSpecialistHandoff`. The original source is never made writable or replaced. The untrusted draft is passed to the Codex container as a bounded JSON prompt; it cannot grant authority or invoke host tools.
4. **Codex specialist:** official Codex CLI v0.163.0 in stage-only Docker with `--network=none`, the trusted Gate­way-mediated local Ollama relay, no host secrets, bounded tool budget and zero executable model tools. Codex produces one revised `answer.txt`. The independent detached escrow validates it against the pinned output digest and original source; the second child closes and the scheduler records verified completion.
5. **Independent human decision:** `TwoSpecialistPendingReview.review(escrow, operator)` checks exact escrow and returns a bounded/masked diff through a separately authenticated operator Gateway session. `publish` cannot create a grant: it requires the existing one-use human approval and exact source inode/revision validation, and calls atomic one-file `DetachedArtifactEscrow.promote`. Without consent, original source is unchanged.

## Non-goals and restrictions

Only one source filesystem and two specialist nodes. No auto-approval, no direct stage copy, no shared vendor profile, no hosted credentials, no general MCP, no Claude/OpenCode, no distributed execution, no multi-file promotion. Crash recovery relies on the existing durable supervisor/guardian; incomplete, ambiguous and uncertain attempts are quarantined and never silently replayed.

## Required acceptance

The real-host test `tests/integration/test_native_codex_two_specialist_acceptance.py` runs Deep Agents with live Ollama inference and official digest-pinned Codex CLI on Linux, verifies both escrows, human refusal, post-lease approval and exact final source bytes, and rejects an engine substitution. The existing Codex and escrow suites additionally exercise in-flight cancellation, duplicate receipts, stale approvals, SIGKILL/recovery and tamper denial.

Release gates: all mandatory real tests execute with **zero skips**; exact-head quality/backend/Linux Docker/DAST/CodeQL green; review comments resolved; no unsupported privileges. Any failure blocks merging. Use the existing human approval service; never synthesize its decision in production.
