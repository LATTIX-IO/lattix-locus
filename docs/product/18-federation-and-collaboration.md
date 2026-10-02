# 18 · Federation and Collaboration

**Decision (D-09):** instances federate **peer to peer**. There is no org hub, no shared server, and no instance with authority over another. Each principal's domain stays on their own instance. Collaboration happens through **spaces** that peers choose to share and through **delegation** that peers choose to accept.

## 1. Goals

- Two or more colleagues, each on their own Locus, can co-own a project, board or document and see each other's changes.
- One principal can delegate a task to a peer's agent, which runs it on the peer's machine under the peer's policy.
- An organization's instances can recognize each other as members without a central service.
- Nothing about one principal's domain is exposed beyond what they share.

## 2. What "pure P2P" means in practice

| Concern | Approach | Note |
|---|---|---|
| **Discovery on a LAN** | mDNS/DNS-SD service advertisement, opt-in | Extends the existing mDNS federation stub |
| **Discovery across networks** | Signed invite (link or QR) carrying the peer card and connection hints | No directory service |
| **Connectivity** | Direct QUIC connections with NAT hole punching | Some networks block direct paths. **Open question O-05:** allow optional, stateless, end-to-end-encrypted relays (which can't read or authorize anything), or accept "LAN or reachable networks only"? |
| **Offline peers** | Store-and-forward is not available without relays; changes sync when both are online | Spaces stay fully usable locally |
| **Transport security** | Mutual authentication with device keys; all traffic end-to-end encrypted | |

Candidate stacks to evaluate (with provenance checks): libp2p (QUIC, hole punching, mDNS) or iroh (QUIC, hole punching, optional relays). NATS is not used for federation; it's dropped from the local profile ([13](13-security-architecture.md) §12).

## 3. Identity and trust between peers

- **Peer card:** principal public key, device keys, display name, optional org attestation, supported protocol versions, signed by the principal key.
- **Pairing:** exchanging and verifying peer cards (invite + short verification code). Pairing creates a local peer record; it grants nothing by itself.
- **Org membership without a hub:** an org IdP-backed attestation (for example, a signed statement issued at login with the org's OIDC provider, or a key signed by an org root held offline) lets an instance recognize "a verified member of BairesDev". Membership enables discovery and default policies, never access to data.
- **Revocation:** unpairing or a revoked device key is gossiped to peers who share spaces; shared space keys rotate on membership change.

## 4. Spaces

| Aspect | Design |
|---|---|
| **What can be shared** | Projects (with tasks), boards, models, documents, memory collections (explicitly selected items) |
| **Membership** | Owner, editor, commenter, viewer; plus "agent may act" per member |
| **Sync** | CRDT documents per object (tasks as CRDT maps, boards per element, documents as rich-text CRDT). Candidates: Automerge or Yjs (both MIT; provenance check required) |
| **Encryption** | Each space has a symmetric key wrapped to each member's device keys; objects encrypted at rest and in transit; H3 aligns with Lattix data-centric protection so policy is bound to each object |
| **Local placement** | Each member chooses which of their areas a shared space appears in; their own policies apply to it |
| **Agent activity** | Agent-authored edits are marked with the agent principal and link to the run (visible to members only as "edited by James's agent", not the run's internals) |
| **Leaving** | A member can leave anytime; their local copy becomes read-only (or is deleted, by the space's retention setting); keys rotate |

## 5. Cross-peer delegation

```
Requester                                   Peer
task + proposed envelope ──signed request──▶ Triage (untrusted content, verified identity)
                                             peer reviews · edits envelope under own policy
                                             accepts → run on peer's machine with peer's grants
results (artifacts, status) ◀──space sync── outputs written to the shared space
```

- The requester can't grant capabilities on the peer's machine. A Biscuit token from the requester can only **attenuate** what the peer chooses to allow (for example, "only touch this shared project").
- Standing peer grants ("Ana's requests to update tasks in our shared project are auto-accepted") are R3 grants on the receiving side.
- Delegation results return as untrusted content plus verified provenance.

## 6. Agent-to-agent communication

Peer agents exchange only structured messages over the peer protocol: delegation requests, status, results, questions. The current custom signed-HTTP "A2A" envelopes (HMAC/JWT, nonce, replay protection) evolve into this protocol, with public-key signatures replacing the shared HS256 secret. If an industry agent-to-agent standard is adopted later, it's an adapter on the same identity and grant model.

## 7. Threats specific to federation

| Threat | Control |
|---|---|
| Malicious peer injects instructions via shared content | All peer content is untrusted; taint rules apply |
| Peer requests harmful delegation | Local review and policy; requester can't widen grants |
| Compromised peer device | Device key revocation; key rotation on spaces |
| Metadata leakage (who collaborates with whom) | Direct connections; no directory; relays (if allowed) see only encrypted traffic |
| Divergent or poisoned CRDT state | Signed changes per author; per-author undo; space owner can roll back to a checkpoint |

## 8. Horizon

H1 lays seams only: principal, device and agent keys; signed audit; peer card format; data model with stable ids and CRDT-ready shapes. H3 ships pairing, spaces, sync and delegation.
