# ADR-0003: Organization-scoped compute federation — proposed

Status: **Proposed / NOT approved for remote execution** (LOCUS-439, 2026-10-12).

## Context and product decisions

ADR-0002, D-03, D-09 and D-19 favor single-principal local sovereignty,
P2P networking and no mandatory hosted service. Organization resource pooling
is an explicitly new, opt-in deployment profile, not a retroactive statement
that remote work is currently safe or implemented.

## Decision under review

Organization trust roots admit device *membership*, never workspace, run,
model, memory or file access. Every device may enroll separately in one or more
organizations, but credentials, caches, processes and queues must remain
isolated per trust domain. The requesting Locus and remote worker each enforce
scope/purpose, data locality, host attestation, model/tool limits and quotas.
No server outside the enrolled organization's explicit trust domain is allowed
to receive prompts, intermediate artifacts, traces, KV caches, embeddings,
memory or execution metadata by default.

Three execution planes:

1. Agent tasks: typed PodSpec/PodAttempt and verified encrypted ArtifactRefs.
2. Inference requests: independently hosted Ollama/vLLM/compatible replicas,
   selected by the existing Gateway and model registry.
3. Distributed model serving: isolated compatible GPU islands; NCCL, Ray,
   KV-transfer and administrative ports are NEVER exposed through general P2P
   peer paths. GPU host/daemon isolation is a separate deployment requirement.

The control tower is a logical peer-elected/coordinated role, not a mandatory
cloud service or a substitute for each node's receiving-side authorization.
Offline standalone Locus and user resource opt-out remain fully functional.

## Interim fail-closed implementation

`locus_runtime.org_compute_contract` exposes a strict versioned parser for
**untrusted** remote-work claims, used solely to define the initial protocol
and adversarial fixtures. `admit_remote_execution` always denies: no
cryptographic attestation or authority can be inferred from the wire fields.
OCF-02 (LOCUS-440), OCF-04 (LOCUS-442) and LOCUS-320/432 must deliver and
independently verify the real admission and trust chain before enabling a
transport listener or remote tool execution. The parser is not a policy engine
and must not become one.

## Explicit security invariants

- Reject spoofed org ID, foreign trust root, replay, expiry, stale revocation
  epoch, revoked device, wrong workspace, incompatible schema and unknown fields.
- Strict per-organization namespace separation across discovery, artifact
  ciphertext, model serving, memory, traces, billing and metrics.
- Sharing off by default; no implied ambient user credentials; no general
  cross-org pool or telemetry; all high-risk action approval stays local.
- Do not treat secure transport (mTLS/QUIC) as data authorization or protection
  from a compromised host after plaintext disclosure.
- Remote side effects use existing Gateway action receipts, lease fencing,
  independent verification and auditable recovery.

## Open design decisions / owner sign-off required

Organization issuer (offline CA / enterprise IdP + signed root), trust-domain
revocation and disconnected policy freshness, per-org host process isolation,
GPU island attestation standard, Iroh/QUIC transport details, optional
coordinator failure modes, and retention/deletion evidence.

## Scope and closure

This first PR is contract-only. It cannot claim the entire LOCUS-439 story
complete until architecture review, diagrams, issuer/worker threat analysis,
interoperability profiles, risk tests and product-roadmap D-03/D-09/D-19
amendments are approved. Follow-on tasks: LOCUS-440–454.
