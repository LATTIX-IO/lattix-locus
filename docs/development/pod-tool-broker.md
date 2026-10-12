# LOCUS-433 — immutable administrative tool registry (increment 5)

This module is an **administrator-controlled catalog**, not a second execution
engine. It consumes an existing child PodLease and delegates every invocation
to PodSandboxBroker from LOCUS-433 increment 4. It has no subprocess, Docker
launcher, independent PEP, or output collection channel.

## Execution contract

1. A trusted supervisor registers a nonempty mapping of named BrokerTool
   descriptors. The mapping is copied and exposed read-only, and descriptors
   are frozen. An LLM can select only a registered name and provide an
   idempotency key; it cannot mutate argv, executable, cwd, mounts, environment,
   network, runtime ceilings or sandbox preference.
2. Registration validates ASCII tool names, fixed tuple argv, absolute
   executable and workspace paths, no NUL or newline, <=60 second timeout and
   the same command grammar as BrokerCommand. Shell/interpreter entry points
   and mutable command descriptors are rejected.
3. Each invocation checks the admitted child capability for the exact tool
   and executable and rejects missing or symlinked configured entry points.
   PodSandboxBroker then checks the live lease, canonical working-directory
   read scope and trusted platform confinement plan before native Gateway
   policy authorization. The durable action receipt enforces pre-dispatch
   fencing, tool/action budget ceilings, and replay denial.
4. The only returned data is BrokerOutcome: receipt, exit status, backend
   and elapsed time. No raw stdout/stderr or tool arguments are persisted to
   the receipt database. The existing Docker time-limit cleanup is inherited.
   An ambiguous timeout remains non-replayable even if cleanup succeeds.

## Acceptance and regression checks

- Unknown tools, invalid descriptors, unapproved executable, revoked parent
  and missing confinement all fail without creating a dispatch effect.
- The registry resists caller map mutations and prevents dynamic tool updates.
- An authorized call is admitted once, audited through the native gateway and
  fenced receipt, sandboxed with network disabled, and blocked on retry.
- Symlinked executables, direct host-run plans and foreign tool capabilities
  deny before subprocess launch.
- Timeout followed by failed Docker cleanup remains uncertain and cannot
  replay; existing sandbox, action fencing, reservation and gateway regression
  suites must also pass.
- Merge requires latest-head backend tests, Ruff/mypy, policy/quality, Linux
  integration, Windows sandbox, DAST, release/version and required-gates.

## Deliberate exclusions

No generic shell or Python execution, AI-provided executable/argv, interactive
output streaming, untrusted plugin code, remote MCP/A2A execution, network
egress, distributed host fencing, provider-verified result evidence, or
general computer-use privileges. This is a narrow local trusted-supervisor
tool catalog; it is not an independently attested agent sandbox.
