# Production outcome-review state and authorization contract

The backend's live workflow-run status is authoritative. "Blocked" represents a guardrail refusal, not a resumable human approval. "Needs Review" is a pending **artifact outcome review**. The frontend must not infer resumption from a Blocked status, and Stream end/stream_closed cannot override persisted status.

POST /approvals only accepts a decision for an existing, accessible run with status "Needs Review" **in both** the run summary and detail, approvals.required==true, approvals.pending==true, and a currently reviewable artifact with matching artifact_id and positive integer revision. A second decision, stale artifact, forged pending flag, missing feedback for changes_requested, arbitrary decision, or a guardrail-blocked/failed/done run is refused with 409 without changing state or minting grants. The request still passes operator authentication, run access checks and the desktop native widening proof policy. This is an artifact decision, **not** an authorization to execute tools or make external side effects.

Approved: mark the exact artifact revision Approved, the run Done, close the approval and its inbox item, persist, and record an audit and event. Changes requested: record feedback and keep the run and artifact pending review. Event metadata carries the exact artifact ID and revision.

## This does not implement typed TaskPlan launch

Existing TaskIntent/TaskPlan DAG admission is structural only and does not issue Gateway grants. TaskKickoffComposer currently calls POST /workflow-runs directly. Do not describe this endpoint as a reviewed, version-pinned task-plan launch until a backend-controlled plan proposal, approval receipt, execution adapter, idempotency/replay boundary and full PEP/risk enforcement path exist. Keep approval of outcomes separate from authorization of plans and tool actions.

Test: `python -m pytest -q tests/unit/test_run_approval_contract.py apps/backend/tests/test_run_approval_boundary.py apps/backend/tests/test_shell_proof_endpoints.py`. Tests for provider integration remain required at merge.
