# Production outcome-review state and authorization contract

The backend's live workflow-run status is authoritative. "Blocked" represents a guardrail refusal, not a resumable human approval. "Needs Review" identifies a reviewable run *only when* an exact pending artifact revision exists. Stream end/stream_closed cannot override persisted run status.

POST /approvals only accepts a decision for an existing, accessible run with status "Needs Review" **in both** summary and detail, approvals.required==true, approvals.pending==true, and a currently reviewable artifact with exactly matching artifact_id/version. A denied or already decided revision, missing feedback for changes_requested, malformed artifact, or a guardrail-blocked/failed/done run is refused with 409 without changing state or minting grants. Authentication, run access checks and the desktop native widening proof policy still apply.

**Approval:** consume the reviewed revision, mark only that artifact Approved, mark the run Done, close its inbox, audit and persist. **Changes requested:** consume that revision and mark the artifact Draft, retain the run under Needs Review with progress "Awaiting revised artifact", close its inbox and require a **new** produced artifact version before any further approval. Merely resetting the pending flag on a previously decided revision cannot replay it. Decisions record actor, artifact ID, exact revision and type.

Compiled workflow runs now explicitly bind their pending review to the primary team handback artifact. During persisted-state startup, an earlier pending compiled handback without such fields is backfilled **only when exactly one matching "Completed Feature — Team Handback" artifact and durable artifact summary exist**. Ambiguous, already decided or unrelated records are left unbound and fail closed.

**Concurrency limit:** `_RUN_APPROVAL_TRANSITION_LOCK` serializes only approval calls in one Python process. It does not coordinate other ASGI workers, worker-side status mutations or multi-process reservations. `_persist_store_state` is best-effort and may swallow database failures, so these changes do **not** establish durable cross-process CAS or transactional audit/state commits. The in-memory audit event is appended before requesting a snapshot persist, reducing the previous restart-loss window; an authoritative transactional migration is required before deploying as multi-worker production. Track with LOCUS-433/462. No job may be presented as globally accepted based solely on this process-local receipt.

## Not a typed TaskPlan launch

TaskIntent/TaskPlan structural DAG admission is not a Gateway grant. TaskKickoffComposer still calls the existing prompt-run endpoint directly. Reviewed TaskPlan admission, signed expiring receipts, idempotent Gateway-bound launch and per-action PEP enforcement require LOCUS-459C/D. Do not conflate acceptance of outcome artifacts with plan or action authorization.

Suggested tests: `python -m pytest -q tests/unit/test_run_approval_contract.py apps/backend/tests/test_run_approval_mutation.py apps/backend/tests/test_run_approval_boundary.py apps/backend/tests/test_shell_proof_endpoints.py`. Real provider, multi-worker concurrency and durable restart acceptance still required.
