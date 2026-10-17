Release-Impact: minor
Version: 0.9

Introduces a default-disabled authenticated, read-only TaskPlan preview API deriving identity, project namespace, classification and requested authority ceilings from server-owned context. It does not sign, approve, authorize or execute a plan.

Action required: Only enable LOCUS_TASK_PLAN_PREVIEW_ENABLED=1 after provisioning an access-controlled server-owned LOCUS_TASK_PLAN_REVIEW_REGISTRY_PATH; otherwise the endpoint fails closed. No plan launch surface is enabled by this change.
