# LOCUS-432 — Trusted child session attenuation (second increment)

A trusted service can now call `Gateway.open_child_session(parent, run_id=..., engine=..., capabilities=...)`.
The gateway authenticates the parent against its server-side record, derives the
principal from the registered parent, rejects widening of named tools, filesystem
roots, executable/host/app allowlists, risk ceilings, budgets and data scope,
and registers a fresh child token. The child is namespaced beneath its parent
run. Its authorization fails when any ancestor is revoked.

This is **not** a pod scheduler or a substitute for child process sandboxing,
accounting aggregation, validated artifact handoffs, or independent verification.
A trusted orchestrator must derive child caps from a verified plan and reserve
resources atomically before opening siblings. The parent retains authority to
delegate only through the supported API, never via LLM-returned tokens.
Keep LOCUS-432 open until mediation inventory and hostile boundary tests close.

Run tests: `python -m pytest -q tests/unit/test_child_gateway_session.py tests/unit/test_pod_risk_ceiling.py`.
Changes to gateway.py are protected by CODEOWNERS and require principal review.
