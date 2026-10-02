# 13 · Security Architecture (Zero Trust)

A personal operator with desktop control, your accounts and your subscriptions is a high-value target and a capable insider. The design assumes **the model can be manipulated, tools can lie, content can attack, and peers can be compromised**, and still keeps actions bounded, attributable and reversible.

## 1. Threats that shape the design

| # | Threat | Primary controls |
|---|---|---|
| T-A | Prompt injection from web, email, documents, screens, tool output | Taint labels, injection screening, taint gate on actions (§6) |
| T-B | Confused deputy: agent uses legitimate access for an attacker's goal | Envelopes, intent judgments, narrow grants, approvals for R3 |
| T-C | Credential theft or leakage into context, logs or vendors | Keychain, secret-by-reference, redaction, no secrets in context (§7) |
| T-D | Malicious or drifting skills and MCP servers | Manifests, schema pinning, sandboxing, trust lifecycle ([15](15-skills-mcp-and-extensions.md)) |
| T-E | Runaway loops, spend or destructive cascades | Budgets, loop guards, reversible-by-default, kill switch |
| T-F | Vendor engine acting outside policy | Gateway-only tool surface, sandbox, egress proxy ([10](10-inference-and-model-routing.md) §3) |
| T-G | Compromised or malicious peer | Signed identities, attenuated grants, local acceptance, data-bound policy ([18](18-federation-and-collaboration.md)) |
| T-H | Local malware or another user on the machine | Loopback-only API with session auth, encrypted storage, signed helper IPC |
| T-I | Tampering with audit or policy | Hash-chained signed log, R4 for policy change by agents, dual confirmation for policy edits |

`THREAT-MODEL.md` must be rewritten against this list, with every evidence link pointing at real code and tests (P9).

## 2. Zones and segmentation

```
┌─────────────────────────────── Instance ───────────────────────────────┐
│ Z0 Trust core      policy engine · grant issuer · keychain broker ·     │
│                    audit log · posture                                  │
│ Z1 Control plane   API · UI · tracker · memory · router · run manager   │
│ Z2 Gateway (PEP)   tool calls · model calls · egress · computer use     │
│ Z3 Execution       sandboxed tool processes · vendor CLI engines ·      │
│                    skill code · MCP stdio servers                       │
│ Z4 Desktop         native helper · user session / isolated session      │
│ Z5 Egress          per-run proxy → internet, LAN, vendor APIs           │
└─────────────────────────────────────────────────────────────────────────┘
               Z6 Peers (other instances) — reachable only via the peer protocol
```

| From → To | Allowed path |
|---|---|
| Z1 → Z3/Z4/Z5 | Only through Z2 |
| Z3 → anything | Only through Z2 (the tools it was given) and Z5 (its run's allowlist) |
| Z2 → Z0 | Policy decisions, grant verification, secret injection, audit append |
| Z6 → Z1 | Peer protocol endpoint only; results land as untrusted content |
| Any → Z0 writes | Human only, via the Security space with re-authentication |

## 3. Identity

- **Principal keys:** each human principal has an identity keypair; each instance has a device key; the agent acting for a principal has its own agent key, so actions are attributable to "James's agent on kai-pc-001", not just "James".
- **Local auth:** the UI and API bind to loopback and require a session established by the desktop app (OS-account bound) or a local passkey. The current `local-lightweight` behavior of allowing unauthenticated requests when unset is removed.
- **Org identity (optional):** an org IdP (OIDC) can attest membership; attestations are signed statements carried in peer cards, not a runtime dependency ([18](18-federation-and-collaboration.md) §3).

## 4. The gateway (single PEP)

Every side effect passes the gateway. For each request it performs, in order:

1. **Authenticate** the caller (run, sub-run, engine adapter, skill process) by its run token.
2. **Resolve** the action: tool, arguments, target, computed risk class.
3. **Verify grant:** a Biscuit capability token covering this action pattern, run and expiry.
4. **Evaluate policy:** embedded Rego (area, global, skill, peer policies) with the action, grant, taint and judgments as input.
5. **Check taint gate** and **intent judgments** (Laya nouls: action matches goal; recipient/target in task context).
6. **Decide:** allow, ask (approval card), or deny.
7. **Execute** with secrets injected by reference and egress through the run's proxy.
8. **Record** an audit event with result and undo handle.

Model calls also pass the gateway: it enforces the area's data ceiling per engine, redacts configured patterns (DLP), and meters spend.

## 5. Policy engine and capability tokens

- **Policy:** keep Rego as the policy language and the existing 7 policies and 28 tests, but evaluate them **in process** with an embeddable Rego engine (Regorus, MIT-licensed, Microsoft) or, if feature gaps block that, a bundled OPA binary as a local sidecar. The hand-written Python copy of the rules is removed. Policy parity tests compare decisions across representative inputs (threat T9).
- **Capability tokens:** replace the custom HMAC JSON tokens with **Biscuit** (public-key, offline-verifiable, attenuable). This fits sub-agent attenuation and peer delegation, which HMAC shared secrets can't do safely. The existing HS256 shared secret for A2A is retired.
- **Grant UX:** standing grants are narrow by construction (the approval card proposes the tightest pattern: this recipient domain, this folder, this repo, this amount ceiling) and expire by default (30 days) unless pinned.

## 6. Taint and content trust

| Tier | Sources |
|---|---|
| **Trusted** | Principal input, policy, playbooks, system prompts |
| **Internal** | The principal's own files and memory (data, not instructions) |
| **Untrusted** | Web, email, messages, screens, tool output, M365 Copilot answers, vendor engine output |
| **Peer** | Content from peers (identity verified, content untrusted) |

Rules:

1. Untrusted text is delimited and labelled in every prompt, and screened by Laya for injection. High scores quarantine the chunk and show it to the user as "withheld".
2. Taint propagates to derived values. The gateway knows which action arguments came from tainted spans (tracked by the run manager at the value level where feasible, at the step level otherwise).
3. **Taint gate:** an R2/R3 action with tainted arguments requires a covering grant whose pattern constrains those arguments (for example "send only to @bairesdev.com"), plus passing intent judgments; otherwise it asks.
4. Tainted content can never cause a grant, policy, envelope or memory promotion change.

## 7. Secrets

- Stored in the OS keychain (Windows Credential Manager/DPAPI, macOS Keychain). Vault becomes optional, for users who already run one, behind the same secret-broker interface.
- Referenced as `secret://area/name`; resolved only inside the gateway at execution time.
- Never present in engine context, run logs, audit payloads or frames. Output scanning catches accidental echoes (gitleaks-style patterns plus known secret values) and redacts them.
- Vendor CLI credentials stay in the vendor's own store; Locus never reads them.

## 8. Execution sandboxing and egress

- **Sandbox tiers** (carry and restore): macOS seatbelt, Linux bwrap, Windows AppContainer (restore `win_sandbox.py`), hardened container when Docker is available, restricted process as the last resort behind an explicit flag. The planner fails closed when no adequate tier is available.
- **Run workspace:** each run gets a workspace directory; writes outside it are R2 with snapshot, or R3.
- **Egress proxy:** a per-run local forward proxy enforces the envelope's domain allowlist, blocks private-range access unless granted (SSRF and LAN pivot protection), and logs destinations. This replaces the Squid sidecar for desktop and makes egress control available in local mode, where it is missing today.

## 9. Audit and posture

- **Audit log:** hash-chained, signed with the instance key, stored durably in the local database with periodic signed checkpoints exported to a file the user can back up. Restores the durable audit log removed by PR #18.
- **Posture page:** for each control, state (enforced, degraded, off), the code path, the last test evidence and its age. A control without evidence shows as "unverified", never as enforced.
- **Exceptions:** any allow-by-override is recorded as an exception and listed in the weekly audit.

## 10. Data protection

- Local database and file stores encrypted at rest (OS full-disk encryption required at install, plus application-level encryption for memory, audit and frames).
- Area data ceilings restrict which engines may see which data classes.
- Shared-space objects are encrypted to member keys with policy bound to the object (H3, aligned with Lattix data-centric security and TDF-style policy binding) so protection travels with the data.

## 11. Supply chain

- Restore and extend security CI: CodeQL/Semgrep, secret scanning, SBOM (SPDX) with Grype/Trivy, dependency provenance check (P28), DAST against the local API.
- Pin the Microsoft Agent Framework dependency or remove it (it's currently an unpinned git branch used only for code generation).
- Sign Windows and macOS builds; notarize on macOS; publish SBOM and provenance attestations with releases.

## 12. Components and their fate

| Component | Today | Decision |
|---|---|---|
| OPA server | Deployed, never called | Replace with embedded Rego evaluation (§5) |
| Biscuit | Declared, unused | Adopt for grants (§5) |
| NATS | Declared, unused | Drop from the local profile; peer transport is defined in [18](18-federation-and-collaboration.md) |
| Envoy | Single route, no filters | Drop from desktop; the gateway is the PEP. Keep only in the optional server profile |
| Vault | Client only | Optional secret-broker backend; keychain is the default |
| Presidio | Optional | Keep as an optional DLP analyzer in the gateway for model calls |
| Squid | Full-stack only | Replaced by the per-run egress proxy |
