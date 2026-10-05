# 03 · Product Principles

These are constraints, not aspirations. A feature or screen that violates one is not done. Numbered so reviews and PRs can cite them ("violates P9").

## Delegation principles

**P1 · Outcomes, not steps.** The user states a goal and done criteria; the agent owns the plan. Step-level instruction is possible, never required.

**P2 · Every run has an envelope.** No run starts without a goal, done criteria, capability set, budget (time, spend, actions) and autonomy tier. Defaults fill what the user doesn't state; the envelope is always visible and editable.

**P3 · Iterate to done or stop honestly.** A run ends in exactly one state: done (criteria verified), blocked (with the specific blocker and what would unblock it), or stopped (by budget, user or policy). It never ends in an ambiguous "finished" without verification.

**P4 · Ask at decision points, not at every step.** Approvals are reserved for R3 actions without a covering grant, envelope edges and low-confidence commitments. An approval nobody needs to read is a defect.

**P5 · The human can always take the wheel.** Pause, take over, edit the plan, revoke a grant or kill a run, instantly, from any screen and from a global hotkey.

## Security principles

**P6 · One gateway.** Every tool call, model call, computer-use action, file write outside the run workspace and network egress passes through the policy gateway. There is no side channel, including for vendor CLI engines. See [13](13-security-architecture.md) §4.

**P7 · Deny by default, grant narrowly.** Capabilities are explicit, scoped to a run or area, attenuable and expiring. Holding an ID, a URL or a token string is never permission.

**P8 · Untrusted content informs but never authorizes.** Text from the web, email, documents, screens, tool output and peers is tainted. Tainted context cannot by itself justify an R3 action, change policy or widen a grant.

**P9 · Enforced or not claimed.** A security control is described as present only if code calls it on the execution path and a test proves it. Declared, deployed or generated components are labelled as such. (This exists because NATS, Biscuit, OPA server, Envoy authz and Vault are currently declared but not enforced.)

**P10 · Secrets never enter model context.** Credentials are injected by reference at the gateway. No engine, skill or log line sees a raw secret.

**P11 · Everything is attributable and replayable.** Each action records principal, run, grant, engine, inputs (redacted), result and before/after state where feasible, in a hash-chained, signed log.

**P12 · Reversible by default.** Prefer drafts over sends, trash over delete, branches over pushes, holds over bookings. Where an action can't be undone, say so before doing it.

## Local-first principles

**P13 · Works offline with local engines.** Chat, tracker, memory, whiteboard, local models and Laya work with no network. Hosted engines degrade gracefully when unavailable.

**P14 · Your domain is sovereign.** Data stays on the principal's instance unless explicitly shared to a space or sent to an engine permitted by area policy.

**P15 · Portable and vendor neutral.** Engines, trackers, identity providers and storage are adapters. No single vendor is load-bearing.

**P16 · Honest engines.** Missing credentials, unavailable models and failed calls fail loudly. Simulated or echo output is never presented as a model answer. (Today a missing key silently falls back to simulated output.)

## Experience principles

**P17 · Show the work.** Every run shows its plan, current step, engine, spend and evidence live. Every answer shows its sources and which tier produced it (code, Laya, local model, hosted model, vendor agent).

**P18 · Forms and text first; diagrams as views.** Definitions (skills, policies, playbooks, assemblies) are edited in forms or declarative files. Diagrams render and lightly edit those definitions. A diagram is never the only place a definition exists.

**P19 · Keyboard complete and fast.** Every primary workflow is operable from the keyboard. Cmd/Ctrl+K reaches every action. Tracker interactions meet Linear-class latency: local list filter ≤ 50 ms, issue open ≤ 100 ms p75.

**P20 · Calm by default.** One notification channel, batched and prioritized. Interrupt only for approvals that block progress, failures and safety events.

**P21 · Minimal, focused screens.** One screen, one job. No permanent instructional prose, eyebrow labels or decorative subtitles. Status is a glyph plus a word, and colour never carries meaning alone.

**P22 · Edit where you see it.** Tasks, memory items, grants and plans are editable inline or in the shared inspector; no bespoke edit pages per object type.

## Memory and knowledge principles

**P23 · Memory is visible and editable.** Anything the system remembers about the principal can be seen, corrected, scoped or forgotten, with provenance.

**P24 · Propose, then promote.** Agents propose durable memory; promotion is reviewed (singly or in batches). Working memory for a run is automatic and expires with it.

**P25 · Independent models before commitment.** For high-stakes decisions the system consults independent columns and records dissent rather than trusting one context window.

## Collaboration principles

**P26 · Peers are equals.** No instance has authority over another. Shared spaces sync; actions on a peer's machine happen only by their agent, under their policy, after their consent.

**P27 · Share objects, not domains.** Sharing is per space or object, encrypted to the recipients, with policy bound to the data. Sharing never exposes the rest of a domain.

## Supply-chain principles

**P28 · Provenance-inspected dependencies.** Open-source software and **locally run** model weights that originate in China, Russia, Iran, North Korea, Cuba, Venezuela or Belarus are admitted only after they pass the Locus provenance inspection, and only at the inspected version (D-29). Hosted, API-based or web-based services and inference from those origins are excluded. Everything else still meets the supply-chain controls in [13](13-security-architecture.md).

**P29 · Licenses compatible with AGPL distribution.** No dependency whose license requires per-deployment commercial keys or forbids redistribution (this rules out the tldraw SDK; see [17](17-canvas-and-whiteboard.md) §5).

## Build principles

**P30 · Extend FOSS before building.** Before implementing any capability, search for a maintained, license-compatible (P29), provenance-clean (P28) open-source project that does it, and extend, wrap or configure it. Building our own is a recorded decision with the alternatives considered, not a default.

**P31 · One design system.** Every UI element is a reusable component from the Locus design system, with tokens for colour, type, spacing and motion. No screen ships a one-off implementation of something the system has, or should have. A missing component is added to the system first, then used.

**P32 · Security first; debt only by informed consent.** Every change meets the security principles (P6–P12) and `SECURITY.md` as written. A shortcut that weakens security ships only if the principal has explicitly accepted it after being told the risk. It is then recorded as tech debt with an owner, a Linear issue and a removal target, and shown on the Posture page.
