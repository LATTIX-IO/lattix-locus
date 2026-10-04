# 20 · Roadmap and Decisions

## 1. Release horizons

Horizons are defined by what the principal can do. Each has exit criteria demonstrated on clean Windows and macOS machines.

### H0 · Recover — "a trustworthy main"

The 2 Oct 2026 System State Review found origin/main regressed: the PR #18 merge (`0a7adc0`) deleted 218 files landed by PRs #20–#24. The fullest state is `ae4d703`.

Scope:

- Protect main (required reviews and checks; no agent conflict resolution that takes one side wholesale).
- Restore the deleted files from `ae4d703` onto origin/main, keeping PR #18's additions; reconcile the 108 modified files by hand (`main.py`, `platform_services.py`, `sandbox.py`, `pyproject.toml` first).
- Re-add `openai` to dependencies; restore `tests/__init__.py`; remove simulated-output fallback from production paths.
- Land the uncommitted CI rewrite (ten jobs, required-gates aggregator), rebased, with the workstation path removed from `WORKFLOW.md`.
- Re-measure every gate and get them green.
- Fix silent failure modes: swallowed persistence errors, SSE drop shown as completion.

Exit: all gates green on main; the desktop app, harness, Windows sandbox, knowledge, skills, local models and schedules present and passing their tests; existing posture views report true control state (no declared-only control shown as enforced); the full posture page with per-control evidence is H1 (per [08](08-feature-catalog.md)).

### H1 · Personal operator — "hand it a task and trust what comes back"

Scope: chat → task → run with envelopes and tiered autonomy; run loop with verification; desktop and browser computer use on Windows and macOS; engines (local, API, Codex CLI, Claude Code) behind the gateway; Laya judge in shadow mode plus injection screening live; work tracker (areas, tasks, labels, projects, views, board, triage, recurrence, assign-to-agent); forever whiteboard with memory indexing; memory v1 with review; skills and MCP through the gateway with schema pinning; security core enforced (embedded Rego, Biscuit grants, taint gate, keychain, per-run egress, durable audit, posture page); schedules and file triggers; `main.py` split begun along domains; signed installers.

Exit criteria:

- 20 representative delegated tasks across three areas complete at ≥ 70% with only planned approvals.
- Zero escaped R3/R4 actions in the safety suite (injection on screen, email and web; payment and credential screens).
- Panic key and preemption ≤ 100 ms on both platforms.
- The principal uses it for real work ≥ 5 days a week for 4 weeks.

### H2 · Always on — "it keeps up without being asked"

Scope: inbox and messaging intake; Linear/Asana/Jira sync; Symphony generalized (tracker trigger + coding playbook + any engine); M365 Copilot as a context provider; Laya gating live where calibrated; Jev adapter; column assemblies gating R3 actions; sub-agents; isolated desktop mode; diagram-js models and formalization from whiteboards; cycles and project updates; voice; Locus MCP server; headless peer profile; memory import.

Exit: ≥ 50% of new tasks arrive through intake and are triaged in batches; ≥ 60% of judgments served locally; weekly audit takes ≤ 10 minutes.

### H3 · Federated — "my instance and yours, working together"

Scope: pairing, peer cards and org attestations; shared spaces with CRDT sync for projects, boards, models and documents; cross-peer delegation; data-centric protection on shared objects; mobile approvals through a paired device.

Exit: two principals co-run a real project for 4 weeks from their own instances with no shared server; one delegated task per week across peers completes without manual file exchange.

## 2. Decisions taken in these docs

| ID | Decision | Rationale | Revisit when |
|---|---|---|---|
| D-01 | Reposition from multi-agent orchestration platform to **local-first personal AI operator** | The value is delegated, trusted completion of real work; orchestration is the means | — |
| D-02 | `docs/product/` takes precedence over implementation docs for product intent; `PRODUCT_SENSE.md` superseded | One owner for direction | — |
| D-03 | Single principal per instance; collaboration through peers, not multi-user tenancy | Matches personal-first plus partnership use | An org asks for a managed deployment |
| D-04 | Subscription capacity is used only through vendors' official clients (Codex CLI, Claude Code) as sandboxed engines; no token reuse | Vendor terms; stability; auditability | Vendors publish sanctioned third-party subscription access |
| D-05 | Default autonomy is tiered by action risk (R0–R4) with envelopes and budgets for every run | Autonomy where reversible; humans where irreversible | Calibration data supports wider standing grants |
| D-06 | Full desktop control on Windows and macOS in H1, via a separate signed native helper | Takeover needs desktop apps; containment needs OS-level controls | Safety suite shows escapes |
| D-07 | Microsoft 365 Copilot is a context provider (Retrieval, Chat, Meeting Insights APIs), not an engine; M365 actions go through Graph tools | The Chat API answers but doesn't act | Microsoft ships an action-capable API for custom apps |
| D-08 | Retire React Flow. Forms and declarative files are the source of truth; diagram-js renders models; Excalidraw is the whiteboard | Data-centric modelling and freeform thinking are different jobs | Kepler's engine bake-off picks a different modelling engine |
| D-09 | Federation is pure peer-to-peer; no hub | Sovereignty; no central target | Connectivity data (O-05) shows P2P is impractical without relays |
| D-10 | Cognitive columns are retained as independent reasoning and memory units that vote before commitments | Uncorrelated checks on high-stakes actions | — |
| D-11 | Laya (local) is the default System One judge; Jev optional per area policy | Open source, local, fast; Jev where accuracy needs it | Laya calibration fails for key judgments |
| D-12 | Policy stays in Rego, evaluated in process (Regorus, or bundled OPA as fallback); the Python copy of the rules is removed | Enforced, not declared (P9) | — |
| D-13 | Capability grants use Biscuit tokens; HMAC shared-secret tokens retired | Attenuation for sub-agents and peers; public-key verification | — |
| D-14 | Drop NATS, Envoy, Vault server, Squid and Casdoor from the desktop profile; keep optional adapters where useful | Remove declared-only components; reduce footprint | — |
| D-15 | Native work tracker modelled on Linear, generalized to knowledge work and personal life; external trackers sync | One system of record for all work, handed to the agent | — |
| D-16 | Playbooks (envelope templates) replace workflow graphs as the main reusable unit; existing graphs import as playbooks | Outcome delegation over node wiring | — |
| D-17 | Dependency provenance policy as in Kepler, including local models (P28); tldraw excluded for licensing (P29) | Security, customer eligibility, AGPL compatibility | — |
| D-18 | Skills use the Agent Skills format with a Locus capability manifest | Portability with vendor agents; enforceable bounds | — |
| D-19 | Helm/hosted profile unsupported; desktop and headless peer are the targets | Follows D-03 and D-09 | An org deployment is requested |
| D-20 | Publishing an agent, workflow, playbook or guardrail makes the new revision active immediately; "Activate" stays available to roll back to an earlier revision | One step for a single principal; rollback stays one click in Releases (LOCUS-311) | Shared spaces (H3) need staged promotion |
| D-21 | The self-improvement loop runs on hosted NVIDIA NIM (API catalog; key in the OS keychain) with local Ollama as the fallback tier | Free inference without a GPU host; offline fallback (P13) | Self-hosted NIM capacity is available |
| D-22 | Self-improvement PRs auto-merge only when every gate is green **and** no protected path changed (policies, gateway, sandbox, secrets, auth, CI workflows, `AGENTS.md`, `CODEOWNERS`); protected-path PRs need principal review. The loop can never weaken or skip a gate | Lets the loop iterate without letting it edit its own guardrails (P6–P9, P32) | Gate calibration shows safe wider autonomy |
| D-23 | Windows coding agents use a Locus-managed toolchain (portable CPython + BusyBox-w64) in a Locus-owned directory, granted only to the AppContainer | Usable shell and Python inside the jail without changing ACLs elsewhere | A uid-style Windows jail or WSL2 becomes the default |
| D-24 | Unsigned macOS and Windows test installers are built by GitHub Actions until signing certificates exist | Unblock installing and dogfooding now | Developer ID / Authenticode certificates are provisioned |
| D-25 | The agent can control the principal's **own** browser profiles (Chrome, Edge, Firefox) and their signed-in sessions, under a principal-chosen **browser autonomy tier** from Strict to Open. Each tier is a Rego policy plus grants and is shown on the Posture page. Every tier keeps a non-removable floor: gateway mediation and audit (P6, P11), panic and takeover (P5), and secrets never entering model context (P10). The agent uses the browser's saved logins and autofill but never reads or types raw passwords or card numbers. Choosing a tier wider than the default is informed consent under P32 | Real work happens in signed-in sessions; the principal sets the trade-off, and the floor keeps it auditable and stoppable | Safety-suite escapes, or an incident the floor did not contain |
| D-26 | Two update channels. **Dev** subscribers auto-install every release the self-improvement loop publishes after all gates and the scorecard pass. **Stable** users are notified and install on click. Promotion from Dev to Stable is a principal action. Every channel verifies the Tauri updater signature (minisign) | Closes the self-improvement cycle (merge → release → update → resume) without pushing unreviewed builds to general users | Code-signing certificates and a staged-rollout service exist |
| D-27 | The core agent runtime is chosen by a measured bake-off between the Locus verified loop and LangChain Deep Agents on LangGraph, using the same tasks, model and gateway. The losing runtime is removed and the remaining agent loops are consolidated onto the winner | One loop instead of twelve; FOSS first (P30) where it measures at least as well | The scorecard shows a regression on the winner |
| D-28 | Locus is built from **modules with versioned ports**: agent runtime, models, tools/connectors, computer use, memory, knowledge/ingestion, skills, intake/triggers, surfaces, evals, persistence, observability, delivery/update, and the trust kernel. Implementations are selected by a single composition root from profiles and swap live, at run boundaries or by release. Boundaries are enforced in CI (import-linter). Trust-kernel implementations change only in principal-reviewed releases. Third-party code is an out-of-process implementation behind the gateway. See [ARCHITECTURE-MODULES](../ARCHITECTURE-MODULES.md) | Change one part of the harness without touching the rest, including through the self-improvement loop, while keeping the security boundary intact | A module needs a cross-cutting change that a port cannot express |
| D-29 | Amends P28 and D-17. FOSS and **local** model weights originating in a P28-listed country are allowed once they pass a recorded **provenance inspection**, re-run on every version change: (1) ownership and ties: maintainers, organization and funding checked against the US Commerce Entity List and the DoD 1260H list; (2) SBOM (syft) and vulnerability scans (osv-scanner, grype); (3) static review (semgrep) for telemetry, analytics SDKs, hidden or obfuscated network calls and install-time code; (4) a dynamic test in the Locus sandbox with network egress denied, recording every connection attempt while exercising the feature, where any unexplained attempt fails the inspection; (5) pinned version and hash, a vendored copy, and runtime egress deny-by-default behind the gateway. Models also require weights-only formats (safetensors or GGUF; no pickle or custom loader code), a behavioural and red-team eval before use, and no network access at inference. Hosted, API or web inference and services from those origins stay excluded. The dependency gate refuses anything without a passing attestation | Lets Locus use the best FOSS and open-weight models wherever they come from, while proving that nothing collects or sends data or ties back to a hostile state | A finding in an attested dependency, or an inspection the gates cannot automate reliably |

## 3. Open questions (with recommendations)

| ID | Question | Recommendation |
|---|---|---|
| O-01 | How is Claude Code billed when Locus launches it (plan, Agent SDK credits, API key)? | Confirm against current Anthropic terms before H1; adapter supports all three and labels the mode |
| O-02 | May Codex ChatGPT-login runs execute unattended? | Treat as attended-only until confirmed; use an API key for scheduled runs |
| O-03 | Default local T3 model under the provenance filter on available hardware | Benchmark gpt-oss, Llama, Gemma and Mistral sizes on the target machines in H1 |
| O-04 | Should GitHub Copilot be added as an engine later? | Not selected now; design the adapter interface so it can be added |
| O-05 | Allow optional, stateless, end-to-end-encrypted relays for peers behind restrictive NATs? | Yes as opt-in per space; they can't read or authorize anything. Needs your confirmation given the pure-P2P choice |
| O-06 | Embedded PostgreSQL or SQLite for the desktop store? | Keep PostgreSQL in H1 (existing code, pgvector); evaluate SQLite for H2 footprint |
| O-07 | Is Microsoft Agent Framework kept? | Remove from the architecture story unless a concrete H2 use appears; at minimum pin it |
| O-08 | Name and voice for the assistant persona | Choose before H1 UI polish |
| O-09 | Retention defaults for computer-use frames and run payloads | 14 days frames, 90 days run payloads, audit indefinite; confirm |

## 4. Validation plan

- **Dogfood:** the principal runs H1 daily across BairesDev, Lattix and Personal areas; weekly review of metrics in [01](01-vision-and-positioning.md).
- **Safety suite:** an adversarial corpus (injected web pages, emails, documents, screens; malicious MCP tool descriptions; drifting schemas) run on every release.
- **Calibration:** Laya shadow-mode accuracy per judgment published on Memory → Columns before any judgment gates alone.
- **Peer pilot (H3):** two principals on separate networks.

## 5. How to change this document

Add a decision row with rationale and a revisit trigger; never delete superseded decisions; strike them through and point to the replacement.
