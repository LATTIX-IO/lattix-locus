# LOCUS-432 — Pod risk-ceiling PEP increment

## Delivered in this PR

The existing Zero Trust Gateway now admits an **optional server-registered**
`Capabilities.max_risk_class` (`RiskClass.R0` through `R3`) for a run/pod.
After action classification and **all policy risk floors**, but **before**
intent-gate approvals, browser-tier consents and Biscuit grants, the PEP
unconditionally denies any action whose final class exceeds this ceiling.
Rejections use the distinct audited reason `gateway.pod_risk_ceiling_exceeded`.

R4 is permanently prohibited, and no user-supplied string, integer or R4
ceiling can be registered. The gateway retains the immutable registered
`Capabilities` independently of the mutable session mirror. A spoofed lower
caller `GatewayAction.risk` cannot bypass reclassification; a raised risk
floor can only make an action less admissible. A grant/human approval cannot
override the hard ceiling.

**Legacy behavior:** a top-level session without `max_risk_class` behaves as
before. No live pod execution is enabled by this change.

## Boundary and remaining work

This is the **PEP primitive**, not complete pod delegation. The *trusted*
orchestrator (not an LLM) must still derive the child scope from an
authenticated parent: capabilities/tools, roots, model egress, data ceiling,
allowed apps, autonomy, costs and time. It must create a fresh gateway session
for each ephemeral pod using the derived scope **and** the node's risk ceiling.
A model may propose a `TaskAuthority`, but that proposal is not an
authorization. Until child-session attenuation, identity binding, lifecycle,
verification receipts and durable retry isolation are complete, the compiler
added in PR #83 must remain non-executing.

The gateway-level checks are covered by
`tests/unit/test_pod_risk_ceiling.py` across model calls, file operations,
process execution, network egress, tool calls, MCP calls, desktop actions,
policy floors, forged risk, approvals and grants.

## TDD, acceptance and rollback

- `python -m pytest -q tests/unit/test_gateway.py tests/unit/test_pod_risk_ceiling.py`
- CI `backend-unit`, policy conformance, integration, DAST and Windows sandbox.
- Reject without any irreversible side effect when an action exceeds the
  ceiling, including when broader policy would allow it.
- Verify audit `risk_class` is the effective class and includes the refusal.
- Observe no changes to existing runs with no ceiling.
- If incompatible, remove this opt-in field/guard and keep pod execution
  disabled. **Never** work around a ceiling denial by raising or deleting it.

Protected trust-kernel change: requires principal/code-owner review under D-22,
in addition to CI. The broader mediation inventory and network/process
isolation work remain tracked by LOCUS-432/LOCUS-320.
