# LOCUS-432 — Trusted child session attenuation (second increment)

A trusted supervising service must first explicitly register its top-level
session with `Gateway.open_session(..., allow_child_delegation=True)`, then
call `Gateway.open_child_session(parent, run_id=..., engine=..., capabilities=...)`.
Ordinary sessions have delegation disabled by default; child sessions are
**never** granted delegation rights. Pods requesting subtasks must send them
to the trusted supervisor rather than mint nested sessions or fresh budgets.
The gateway authenticates the parent against its server-side record, derives the
principal from the registered parent, rejects widening of named tools, normalized lexical filesystem
roots, executable/host/app allowlists, autonomy tiers, risk ceilings, budgets and data scope,
and registers a fresh child token and unique run identity. The child is namespaced beneath its parent
run; reusing a logical pod name cannot reuse an earlier approval identity.
Authorization fails when any ancestor is revoked, and parent/ancestor taint
is inherited even if the ancestor becomes tainted after delegation.
A full gateway session table causes an explicit child admission failure rather
than silently evicting the just-created child's parent. Top-level eviction
cleans corresponding lineage bookkeeping, preventing unbounded retained links.

This is **not** a pod scheduler or a substitute for child process sandboxing,
accounting aggregation, validated artifact handoffs, or independent verification.
Each child requires explicit, finite tool-call/action limits and token/time/cost
budgets, and autonomy may only become more restrictive. These are local
per-session constraints: **they do not reserve a shared pool**. A trusted
orchestrator must derive child caps from a verified plan and reserve resources
atomically across siblings before opening them. The parent retains authority to
delegate only through the supported API, never via LLM-returned tokens.
Keep LOCUS-432 open until mediation inventory and hostile boundary tests close.

Run tests: `python -m pytest -q tests/unit/test_child_gateway_session.py tests/unit/test_pod_risk_ceiling.py`.
This increment does not prove symlink-safe filesystem traversal, hostile process
containment or cancellation of already-in-flight actions after revocation; those
remain separate runtime and sandbox acceptance tests.
Changes to gateway.py are protected by CODEOWNERS and require principal review.

## Delegation trust boundary

The `allow_child_delegation` flag is granted only by trusted in-process startup
code. An untrusted worker must never receive the supervisor session handle or
access to unrestricted Python gateway objects. Process/container isolation and
shared-budget reservation remain necessary for production agent execution.
A child holding only its own gateway session cannot delegate another child.
