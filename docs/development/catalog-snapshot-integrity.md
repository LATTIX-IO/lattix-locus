# LOCUS-433 — immutable catalog snapshot integrity (increment 8)

This increment hardens the trusted administrator tool registry shipped in PR #90. It does not create another policy decision point, runner, sandbox, action receipt mechanism, or tool output channel.

## Security invariant

A trusted supervisor creates a `SandboxedPodToolBroker` from a complete mapping of `BrokerTool` descriptors. The broker copies the map before admitting operations, exposes a read-only `MappingProxyType`, and now uses a frozen slotted dataclass so application code cannot accidentally rebind its registry, executor, or digest after construction. All dispatch still calls the same gateway-mediated, OS-fenced `PodSandboxBroker.execute` path.

The broker computes a deterministic SHA-256 snapshot digest from a sorted, canonical JSON representation of the registered names, fixed argv, working directories and timeout ceilings. The digest is domain-separated with `locus.admin-tool-catalog.v1`; the public property returns only the digest. Changing any descriptor changes the digest; independent mappings with identical descriptors have the same digest.

The digest is a **local correlation identifier, not an authentication signature, integrity measurement of the executable file, or attestation**. Python in-process reflection, malicious administrator code, replacement of executable files and hostile filesystem racing remain separate trust threats. Execution-time path checks, gateway approvals and sandbox isolation remain mandatory. No raw argv is emitted in the registry object's representation.

## Regression and security validation

- The original registry tests retain malformed descriptor, unknown tool, denied gateway, unauthorized executable, symlink, uncertain receipt, and replay tests.
- A preexisting test that mutates a live registry in place now constructs an explicit separate unapproved catalog instead, preserving its negative-path semantics.
- New tests require catalog source-map mutation not to affect the admitted catalog or digest, reject assigning registry/runner/digest fields, reject unexpected attributes, verify digest stability and change sensitivity, and confirm registry representation does not expose argv or paths.
- Required CI: exact-head Python unit suite and lint/type checks, policy, integration, Windows sandbox, DAST, frontend, performance, release/version, and required-gates. Optional disabled SAST, SBOM, and AppSec jobs cannot be reported as passing.

## Excluded scope

Dynamic registration, remote artifact signatures, executable file identity attestation, secure enclave support, distributed authorization and cross-process tamper protection are not included. The digest is **not** an authorization decision or a substitute for a durable action receipt.
