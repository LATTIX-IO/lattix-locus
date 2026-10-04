# ADR-0001: Move the authorization core to Cedar

Status: **Accepted** by the principal, 2026-10-04 (D-30, amends D-12).
Owner: principal (trust kernel — release only, principal-reviewed, `docs/ARCHITECTURE-MODULES.md`).

## Context

The gateway (PEP) asks a policy engine for every agent action: model calls, tools, MCP,
file system, network egress, computer use and the user-browser tiers (D-25). Today the
engine is OPA evaluating Rego (`policies/*.rego`), with D-12 planning an in-process Rego
evaluator (Regorus). Desktop installs have to bundle a pinned OPA binary (LOCUS-385), and
when it is missing the gateway fails closed and the product stops working.

The agent-model redesign ("the model chooses; the gateway decides") needs more than
allow/deny answers. Each run gets a capability set (org ∩ profile ∩ envelope ∩ (base ∪
skill manifests)), and skills are admitted only if their manifest provably cannot widen
it. We want to **prove** invariants offline, for example "no tier permits a payment
without an ask" or "an Open tier never permits an account-security change", rather than
only test examples. Rego is general-purpose Datalog: very expressive, but hard to analyse
for properties like these.

## Decision

Cedar becomes the authorization language and engine for the trust kernel's authorization
decisions:

* **Entity model:** principals (user, profile, run, agent), actions (`model.call`, `tool.*`,
  `fs.*`, `net.*`, `browser.*`, `desktop.*`, `memory.*`, `connector.*`) and resources
  (paths, sites, tools, connectors, collections, models). Facts come in as typed
  attributes and context: risk class, taint labels, tier, `protected_action`.
* **Semantics:** Cedar is default-deny, `forbid` overrides `permit`, and policies are
  validated against a schema. This matches zero trust and fails closed on schema drift.
* **Analysis:** CI proves the invariants with Cedar's symbolic compiler
  (`cedar-policy-symcc`, which verifies properties and reports concrete counterexamples).
  The same machinery checks skill admission: a skill's requested capabilities must be a
  subset of the envelope.
* **Port:** the engine sits behind the D-28 `PolicyEngine` port with two adapters (OPA
  and Cedar). Migration is per policy domain, in shadow mode first.
* **Out of scope:** Biscuit grants (capability tokens) stay. Cedar decides; Biscuit
  carries the per-run capability set. Guardrail classifiers stay advisory inputs.

## Alternatives

| | Cedar | Keep Rego (OPA / Regorus, D-12) | Locus-specific policy DSL |
|---|---|---|---|
| Analysability | Schema validation; SMT-based symbolic analysis with counterexamples | Tests only; no practical property proofs | Whatever we build |
| Expressiveness | RBAC/ABAC; deliberately limited (no loops or arbitrary computation) | Very high | Tailored |
| Engine | Rust, Apache-2.0 (`cedar-policy/cedar`) | OPA Go binary (Apache-2.0) or Regorus (Rust) | We own and maintain it |
| Ecosystem | AWS Verified Permissions, growing | Large, CNCF graduated | None |
| Cost | Rewrite the policies, with parity tests | None now | Highest, and unproven |

Rego stays the right tool for data-shaped checks such as configuration linting. It is the
wrong tool for the analysable authorization core.

## Consequences

* **Rewrite:** every domain's Rego rules are rewritten in Cedar, and the existing Rego test
  fixtures become parity fixtures. Until a domain is cut over, both engines run (shadow).
* **Python binding (open; phase 0):** the only Python wrapper found, `cedarpy`
  (k9securityio/cedar-py, which tracks the Cedar engine version), is *not* officially
  supported by AWS or the Cedar team. Options:
  1. adopt `cedarpy` after a provenance inspection (P28/D-29 gate, principal sign-off);
  2. own a thin PyO3/maturin binding to the `cedar-policy` crate;
  3. run a small Rust `locus-authz` sidecar on a local socket.

  The deciding factors are the trust boundary, the latency budget and supply-chain risk.
  TODO(principal, phase 0): pick after the spike.
* **Footprint:** OPA leaves the desktop bundle once the last domain is cut over. The
  binding replaces it.
* **Formal verification claim:** this ADR relies only on the symbolic analysis above.
  TODO(phase 0): confirm the status of the Lean formal model (`cedar-policy/cedar-spec`)
  before docs cite it.
* **Risk:** a translation bug could silently widen access. Mitigations: shadow mode logs
  every divergence, a domain is cut over only after 100% parity on its fixtures plus an
  agreed shadow period with zero unexplained divergences, and the invariants are proved
  in CI.

## Rollout/Rollback

1. **Phase 0 — spike:** choose the binding; provenance attestation; latency benchmark
   (p99 under the gateway budget); draft the schema.
2. **Phase 1 — port and shadow:** `PolicyEngine` port with OPA and Cedar adapters. Cedar
   runs in shadow; OPA stays authoritative; divergences go to the audit log and Posture.
3. **Phase 2 — cutover:** one domain at a time (`user_browser` → `filesystem_access` →
   `tool_jail` / `network_egress` → `computer_use` → model gateway → the rest), each after
   parity.
4. **Phase 3 — proofs:** symbolic-analysis invariants and skill-admission proofs as CI
   gates (protected path, D-22).
5. **Phase 4 — remove OPA:** drop OPA from the bundle and close D-12.

**Rollback:** each domain flips back to the OPA adapter in a principal-reviewed release
(trust-kernel swap class). The Rego policies are kept until phase 4.
