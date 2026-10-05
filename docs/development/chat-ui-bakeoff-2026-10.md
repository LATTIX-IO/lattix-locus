# Chat UI bake-off: assistant-ui vs CopilotKit React UI (2026-10)

Status: **Measured 2026-10-05** · Owner: principal · Related: LOCUS-369 (AG-UI surfaces), LOCUS-409
(generative UI tiers), LOCUS-357 (desktop confirmation), [third-party assessment §3.4](../third-party-assessment.md#34-desktop-ui-surfaces-and-generative-ui) ·
Spike: [`spikes/chat-ui-bakeoff/`](../../spikes/chat-ui-bakeoff/README.md)

## 1. Decision question

Which library should render the Locus chat and run surface on top of the AG-UI protocol, given
the constraints already decided?

- The backend↔UI contract is AG-UI (`@ag-ui/core`, `@ag-ui/client` 1.0.x; `ag-ui-langgraph` on the backend).
- No `@copilotkit/runtime`: no Node middle tier and none of its Segment/Scarf telemetry.
- Every tool call goes through the Locus gateway. Client-side tools may render or request, never execute.
- A human-in-the-loop interrupt must be resolvable through the native desktop confirmation (`confirm_action`).
- Generative UI: tier 1 typed cards, tier 2 declarative specs from a shadcn allowlist, tier 3 sandboxed iframes (off by default).

Candidates: **assistant-ui** (`@assistant-ui/react` 0.15.23 + `@assistant-ui/react-ag-ui` 0.0.63)
and **CopilotKit** (`@copilotkit/react-core` 1.77.0, v2 API under `react-core/v2`, plus
`@copilotkit/react-ui` 1.77.0).

**Answer: assistant-ui, 86/100 against 62/100.** It wins on security footprint, gateway safety,
design-system fit and bundle size. CopilotKit is faster at streaming and ships a finished chat UI,
but those advantages do not outweigh its costs. Details and residual risks are below.

## 2. Rubric (defined before measuring)

Each criterion is scored 1–5. The weighted score is `score / 5 × weight`, so the maximum is 100.

| # | Criterion | Weight | What a 5 looks like |
|---|---|---:|---|
| 1 | Security and telemetry | 20 | No analytics or vendor hosts in the bundle, no install-time telemetry, permissive licences only, works under a strict CSP without `unsafe-eval`/`unsafe-inline`, no outbound requests |
| 2 | Gateway safety | 20 | Nothing executes client-side unless we register it. Advertises no tools to the agent by default. The runtime-less mode we need is a supported, ungated mode. No dormant hosted-service paths |
| 3 | AG-UI fidelity | 12 | Every AG-UI 1.0 event we emit is handled natively, with our pinned `@ag-ui/client`, and little adapter code |
| 4 | HITL interception | 12 | The interrupt reaches our code before anything resumes; we can call `confirmAction()` and pass its decision and proof into the resume; good UX and focus |
| 5 | Design-system fit | 10 | Composes with shadcn/Radix, Tailwind 4 and the Locus HSL tokens without overrides |
| 6 | Generative UI (tiers 1–2) | 6 | Typed cards per tool, plus a native allowlist renderer for declarative specs |
| 7 | Accessibility and keyboard | 6 | No axe violations caused by the library; fully keyboard operable; focus kept |
| 8 | Performance | 6 | Cheap per streamed token; long threads render and scroll without long tasks |
| 9 | Bundle size | 4 | Small first-load JS over a no-library baseline |
| 10 | Maintenance and churn | 4 | Steady releases, few breaking lines, more than one maintainer |

Security/telemetry and gateway safety carry 40% between them, as the brief asked. AG-UI and HITL
fidelity carry 24%. Design-system fit, generative UI, accessibility and performance follow, then
bundle size and maintenance.

## 3. Method

A standalone spike, not wired into `apps/frontend`, with its own `package.json` and lockfile
(npm workspaces). Reproduction steps are in the [spike README](../../spikes/chat-ui-bakeoff/README.md).

- **Stack.** Vite 8.3.2, React 19.2.3, Tailwind 4.3.3, TypeScript 5.9.3. The React and Tailwind majors match `apps/frontend`. Vite was used instead of Next 16 for two reasons. First, the bundle numbers then isolate the library. Second, the strict-CSP test is not confounded by Next's inline bootstrap scripts, which need nonces whichever library we pick. Absolute sizes will differ under Next and webpack, but the gap between the variants will not.
- **Mock AG-UI source.** `shared/src/fixtures.ts` produces a deterministic AG-UI 1.0 event stream, played one event per macrotask. A 16-line `AbstractAgent` subclass wraps it, and the subclass is identical in both variants. There is no backend and no network.
  - **run scenario:** a state snapshot (a 4-step plan) with deltas; 2,000 markdown text deltas, including headings, lists and a fenced code block; three tool calls with streamed arguments and results (a generic call, a tier-1 typed card, and a tier-2 spec containing one non-allowlisted `Script` node); and a fourth tool call, `send_email`, that ends in `RUN_FINISHED` with `outcome: interrupt`. Resuming with an approval yields `RUN_ERROR`.
  - **long scenario:** `MESSAGES_SNAPSHOT` with 200 messages and 50 tool calls.
- **Same surface, built twice.** Both variants have the same elements:
  - a thread and composer;
  - tool-call cards;
  - the approval routed through a `confirmAction()` stub of `confirm_action`;
  - a tier-1 typed card with a validator;
  - a tier-2 allowlist card;
  - a plan panel showing agent state;
  - light and dark themes via the Locus tokens;
  - keyboard-only operation.

  Both variants share one shell and one set of Locus cards, so any difference comes from the library. A third build, `variant-baseline`, has React plus the shared cards and no chat library; it is the control for bundle cost.
- **Measurements.** All are scripted in `bench/`, and the outputs are committed in `spikes/chat-ui-bakeoff/results/`.
  - `licenses.mjs`: an offline walk of the production dependency tree.
  - `scan-bundle.mjs`: sizes, first-load JS, hard-coded hosts and CSP-relevant patterns.
  - `attribute.mjs`: source-map attribution of bytes per package.
  - `count-glue.mjs`: lines of integration code between markers.
  - `npm-meta.mjs`: registry metadata.
  - `run.mjs`: a one-shot headless run, with 3 repetitions per scenario.

  `run.mjs` uses `playwright-core` 1.63.0 with the Chromium build already in the Playwright cache (Chromium 153; no browser download). It serves each build from a throwaway loopback server under a strict CSP meta tag (`default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; font-src 'self'; frame-src 'none'; …`). It aborts and logs every non-loopback request, reads CDP `Performance` metrics, long tasks and CSP violation events, drives the page from the keyboard, and runs axe-core 4.14 (WCAG 2.0/2.1/2.2 A and AA tags) in both themes.
- **Telemetry hygiene.** `COPILOTKIT_TELEMETRY_DISABLED=true`, `SCARF_ANALYTICS=false` and `DO_NOT_TRACK=1` were set for every install, build and run. The root `package.json` also sets `"scarfSettings": { "enabled": false }`.

## 4. Raw measurements

Numbers are from the final bench run (3 repetitions; the median is shown, with the range where it matters). The machine was shared with other work. An earlier session under heavier load gave absolute times about 40% higher, but the ratio between the variants stayed between 3.6× and 3.9×.

### 4.1 Security, telemetry, licences, CSP

| Measure | assistant-ui | CopilotKit | How |
|---|---|---|---|
| Production packages (unique names) | **203** (197) | **561** (501) | `bench/licenses.mjs` (offline walk of deps and non-optional peers) |
| Licences | MIT 197, Apache-2.0 2, BSD-3 1, ISC 1, 0BSD 1, Apache-2.0 AND BSD-3 1 | MIT 471, ISC 46, Apache-2.0 18, BSD-3 16, BSD-2 4, CC0 1, Unlicense 1, 0BSD 1, MPL-2.0 OR Apache-2.0 1 (dompurify), Apache AND BSD-3 1, **UNKNOWN 1** | same |
| Non-MIT/Apache/BSD/ISC | none | `khroma@2.1.0` has no `license` field (its `license` file is MIT); dompurify is dual MPL/Apache (we take Apache) | same |
| Duplicate package versions in the tree | 6 (`@ag-ui/*` 1.0.2 + **0.0.59**, zod 3 + 4) | 60 (e.g. react-markdown 8 + 10, unified 10 + 11, micromark 3 + 4, lucide-react ×2, `@ag-ui/*` 1.0.1 + 1.0.2, zod 3 + 4) | same |
| Install-time scripts | none | `@scarf/scarf@1.4.0` postinstall (prod dependency of `@copilotkit/react-core`). **It reports by default.** It honours `SCARF_ANALYTICS=false`, `DO_NOT_TRACK=1` or `scarfSettings.enabled=false` (`report.js`, lines 56–63) | `npm query ":attr(scripts,[postinstall])"`, install logs |
| Vendor/analytics hosts in the shipped JS | **none** (`assistant-cloud` is in the dependency tree but tree-shaken out) | 9 hosts. In first-load JS: `telemetry.copilotkit.ai/ingest`, `cdn.copilotkit.ai/notifications/v1.json`, `api.cloud.copilotkit.ai`, `dashboard.operations.copilotkit.ai`, `copilotkit.ai`, `docs.copilotkit.ai`. In lazy chunks: `fonts.googleapis.com` (the Inspector), `intelligence.copilotkit.ai`, `www.copilotkit.ai` | `bench/scan-bundle.mjs` |
| Outbound requests observed (6 runs per variant) | **0** | **0** (Inspector disabled; production build) | `run.mjs` aborts and logs all non-loopback requests |
| Same-origin non-asset requests (e.g. runtime `/info` probes) | 0 | 0 | same |
| Runs under the strict CSP | **yes, every feature** | **yes, every feature**, including shiki highlighting (no `wasm-unsafe-eval` needed) | `run.mjs` |
| CSP violation reports per run | 1 × `script-src eval` | 2 × `script-src eval` | `securitypolicyviolation` events. Source: zod 4's `allowsEval` probe (`Function("")` in a try/catch). Harmless, and silenced by `z.config({ jitless: true })`; CopilotKit carries two zod copies |
| Inline style injection paths shipped (`createElement('style')` / `adoptedStyleSheets` / `insertRule`) | 1 / 0 / 0, same as the React baseline | 12 / 2 / 5 (KaTeX, mermaid, the Inspector); not triggered in our runs | `scan-bundle.mjs` |
| `iframe` / `srcdoc` code paths | 0 / 0 | 5 / 4 (websandbox for OpenGenerativeUI, MCP Apps) | same |
| Runtime console warning | none | `selfManagedAgents is part of CopilotKit's Enterprise Intelligence tier. Provide a publicLicenseKey for production use` | `run.mjs` console capture |

### 4.2 Gateway safety

| Measure | assistant-ui | CopilotKit |
|---|---|---|
| Works without a Node runtime | Yes: `useAgUiRuntime({ agent })` takes any `AbstractAgent` | Yes, through `selfManagedAgents`, which the vendor flags as an **Enterprise tier** feature at runtime, or through `agents__unsafe_dev_only`, whose name says it is unsupported. The MIT licence still applies to the code; the risk is that the vendor gates this mode later |
| Tools advertised to the agent in `RunAgentInput.tools` | `[]` | `[]` |
| `context` and `forwardedProps` sent | none | none |
| Client-side execution paths we did not ask for | none active. Frontend tools exist only if we register `makeAssistantTool`. The cloud thread list and MCP-app renderers are opt-in | none active in our run. Built in: OpenGenerativeUI (`generateSandboxedUi` tool plus the websandbox iframe, enabled when the runtime reports it or the prop is set), the A2UI renderer (runtime flag or catalog prop), MCP Apps, and Intelligence/learning features. All need a runtime flag, a prop or a licence key |
| Resume transport | through our `AbstractAgent.run()`. We control it | through our `AbstractAgent.run()`. We control it |

### 4.3 AG-UI fidelity

Tested events, as each library handles them without glue:

| Event(s) | assistant-ui | CopilotKit |
|---|---|---|
| `RUN_STARTED` / `RUN_FINISHED` | native | native |
| `RUN_FINISHED` `outcome: interrupt` (AG-UI 1.0) | native (`useAgUiInterrupts`, `useAgUiSubmitInterruptResponses`; also a native tool-approval part) | native (`useInterrupt`) |
| Legacy `CUSTOM on_interrupt` (older `ag-ui-langgraph`) | forwarded as a data part; resuming it needs glue | native (`useInterrupt`, `useLangGraphInterrupt`) |
| `RUN_ERROR` | **native, rendered in the thread** (`MessagePrimitive.Error`) | **not rendered in the thread**; only `onError`, and the AG-UI `code` is replaced by `agent_run_error_event` (glue: 7 lines) |
| `TEXT_MESSAGE_*` | native | native |
| `TOOL_CALL_START` / `ARGS` / `END` / `RESULT` | native; raw `argsText` available while streaming | native; only parsed partial `parameters` (no raw text) |
| `STATE_SNAPSHOT` / `STATE_DELTA` | native (`useAgUiState`) | native (`useAgent().agent.state`) |
| `MESSAGES_SNAPSHOT` | native | native |
| `STEP_STARTED` / `STEP_FINISHED` | ignored | ignored |
| Not exercised (code present): `REASONING_*`, `ACTIVITY_*` (A2UI, MCP Apps), `SUBAGENT_*`, `*_CHUNK` | handled in `react-ag-ui` | handled through `@ag-ui/client` and the activity renderers |
| `@ag-ui/client` version the library pins | **`^0.0.59` (pre-1.0)**, which bundles a second AG-UI client and needs an `as never` cast on our 1.0.2 agent | **`1.0.1` exact**; also needs a cast (nominal private-field mismatch) unless we pin 1.0.1 |
| Adapter glue (lines, excluding the 16-line mock agent) | **9** | **16** (includes the error rendering) |

### 4.4 HITL interception (`confirmAction()` before resume)

| Measure | assistant-ui | CopilotKit |
|---|---|---|
| Interrupt reaches our code before any resume | yes | yes |
| `confirmAction()` called, then resume sent with `{approved, proof}` | yes (1 call, then resume `[{interruptId, status: "resolved", payload: {approved: true, proof}}]`) | yes (identical resume payload) |
| Where the approval renders | inline in the `send_email` tool card (matched by `toolCallId`) | at the bottom of the chat, detached from the tool card, which shows "executing" |
| HITL glue (lines) | **16** | **19** |
| Focus after sending and through the interrupt | stays in the composer (`TEXTAREA`) | **dropped to `BODY`** |
| Tab presses to reach Approve from there | 6 | 4 |

### 4.5 Design-system fit, generative UI, accessibility

| Measure | assistant-ui | CopilotKit |
|---|---|---|
| Styling model | unstyled Radix-style primitives; our Tailwind 4 classes and Locus tokens used directly | a prebuilt Tailwind **4.1.18** stylesheet (90 KB) whose shadcn token names (`--background`, `--primary`, …) collide with Locus's names but hold full **oklch** colours scoped to `[data-copilotkit]`, with dark mode keyed on a `.dark` class |
| Theme override glue | **0 lines** | **52 lines** (49 CSS lines remapping the tokens and restoring Locus's triplets inside a `.locus-scope`, a wrapper on every Locus card, and `.dark` toggling). Without them our cards inside the chat lost their backgrounds and borders, and dark mode left the chat white. The composer stays CopilotKit's own neutral grey |
| UI composition we write | 47 lines (thread, messages, composer, markdown) | 1 line (`<CopilotChat/>`) |
| CSS shipped | 13.5 KB (3.6 KB gz), 0 fonts | 126.5 KB (20.7 KB gz) + **60 KaTeX font files** |
| Tier 1 typed card | `by_name` tool UI | `useRenderTool` with a schema |
| Tier 2 allowlist spec | **native**: `MessagePrimitive.GenerativeUI` with our registry; unknown names go to `Fallback` (9 lines of glue) | no allowlist renderer for plain specs (its A2UI renderer needs the A2UI wire format and a catalog), so we added our own 28-line walker plus 11 lines of glue |
| Non-allowlisted `Script` node | rendered as a "blocked" placeholder; no `<script>` in the DOM | the same (through our walker) |
| axe-core violating nodes, light / dark | 1 / 0, from our shared warning badge (also present in the CopilotKit run) | **5 / 7**: button-name ×2 *critical* (unlabeled tools-menu and send icon buttons) in both themes, plus colour contrast on shiki token colours and our badge (3 light, 5 dark) |
| Keyboard-only run (composer → send → approve) | works (composer reached in 4 Tabs) | works (composer reached in 4 Tabs) |

### 4.6 Performance (Chromium 153 headless, 1280×800)

| Measure (median of 3) | assistant-ui | CopilotKit |
|---|---|---|
| 2,000-delta stream: run start to last token visible | 2,145 ms (2,104–2,314) | **567 ms** (554–569) |
| Main-thread task time for that stream (CDP `TaskDuration`) | 2,145 ms (script 1,828) ≈ **1.1 ms/delta** | **576 ms** (script 491) ≈ **0.29 ms/delta** |
| Same, plain text instead of markdown (ablation) | 1,197 ms (script 794): markdown re-parsing is about 45% of the cost | — |
| Long tasks (>50 ms) during the stream | 0 | 0 |
| 200-message / 50-tool thread: render | 83 ms (task 89 ms, 0 long tasks) | **52 ms** (task 60 ms, 0 long tasks) |
| DOM elements after that render | 878 (all 50 tool cards mounted) | 189 (virtualised: about 4 cards mounted) |
| Scrolling the whole long thread | **16 ms** of main-thread work | 123 ms (re-renders as it virtualises) |
| JS heap after the long thread | 19.3 MB | 23.1 MB |
| JS files loaded for the run scenario | 1 | 7 (3 first-load + 4 lazy: shiki and markdown) |

At realistic local-model speeds (30–100 tokens/s), streaming would use roughly 3–11% of a core
with assistant-ui and 1–3% with CopilotKit. Neither produced a long task.

### 4.7 Bundle size (Vite production build, gzip level 6)

| Measure | baseline (React + shared cards) | assistant-ui | CopilotKit |
|---|---|---|---|
| First-load JS, raw / gzip | 201 KB / 62.9 KB | 1,127 KB / **316.9 KB** | 2,329 KB / **649.6 KB** |
| Library cost over baseline (gzip) | — | **+254 KB** | **+587 KB** |
| Total JS emitted | 1 chunk | 1 chunk, 1,127 KB | **402 chunks, 16.2 MB raw / 3.6 MB gz** (shiki grammars, mermaid, cytoscape, KaTeX) |
| Largest first-load contributors (source-map attribution, minified) | — | zod 223 KB (two copies), `@assistant-ui/core` 183 KB, react-dom 175 KB, `react-ag-ui` 81 KB, `@ag-ui/client` 67 KB (two versions) | zod 335 KB, **KaTeX 236 KB**, react-dom 175 KB, `@copilotkit/react-core` 171 KB, `@copilotkit/core` 142 KB, `@ag-ui/client` 135 KB (two versions), parse5 122 KB, `@ag-ui/proto` 88 KB |

### 4.8 Maintenance and churn (npm registry metadata, as of 2026-10-05)

| Package | Latest | Stable releases 30 / 90 / 365 days | Breaking lines opened in 365 days | npm maintainers |
|---|---|---|---|---|
| `@assistant-ui/react` | 0.15.23 (2026-10-02) | 5 / 27 / 103 | 3 (0.12, 0.14, 0.15) | 2 |
| `@assistant-ui/react-ag-ui` | 0.0.63 (first published 2025-11-19) | 5 / 18 / 58 | 0.0.x throughout; every release may break | 2 |
| `@copilotkit/react-core` | 1.77.0 (2026-10-02) | 16 / 41 / 85 (1,144 versions in total, counting prerelease and branch tags) | 0 by semver, but the **v2 API was introduced inside 1.x** (1.50.0, 2025-12-11) and the runtime-less mode is now vendor-flagged | 1 |
| `@ag-ui/client` | 1.0.2 (2026-10-05) | 2 / 4 / 21 | 1 (1.0) | 2 |

Both libraries are MIT. Neither has a stable API: assistant-ui is 0.x with
`unstable_`/`@experimental` surfaces, and CopilotKit rewrote its API inside a minor series.

## 5. Scores

| # | Criterion (weight) | assistant-ui | CopilotKit | Deciding evidence |
|---|---|---:|---:|---|
| 1 | Security and telemetry (20) | 4.5 → 18.0 | 2.5 → 10.0 | CopilotKit has vendor telemetry and licence hosts in first-load JS, a default-on Scarf postinstall, 2.8× the packages and one with missing licence metadata. Both: 0 requests and full strict-CSP compatibility |
| 2 | Gateway safety (20) | 4.5 → 18.0 | 3.0 → 12.0 | Both send no tools and resume through our agent. CopilotKit's runtime-less mode is "Enterprise tier" or "unsafe_dev_only", and it ships dormant hosted, sandbox and generative paths |
| 3 | AG-UI fidelity (12) | 4.0 → 9.6 | 4.0 → 9.6 | assistant-ui renders `RUN_ERROR` but pins the pre-1.0 client. CopilotKit tracks the protocol first-hand but drops `RUN_ERROR` from the thread and loses its code |
| 4 | HITL interception (12) | 4.5 → 10.8 | 4.0 → 9.6 | Both intercept cleanly. assistant-ui keeps the approval on its tool card and keeps focus |
| 5 | Design-system fit (10) | 5.0 → 10.0 | 2.0 → 4.0 | 0 vs 52 override lines; token-name collision; second Tailwind build |
| 6 | Generative UI (6) | 4.5 → 5.4 | 3.5 → 4.2 | assistant-ui has a native allowlist renderer; CopilotKit needs a walker or the A2UI format |
| 7 | Accessibility (6) | 4.5 → 5.4 | 3.0 → 3.6 | 0 vs 2 critical library violations; focus dropped in CopilotKit |
| 8 | Performance (6) | 3.0 → 3.6 | 4.5 → 5.4 | CopilotKit is about 3.7× cheaper per streamed token and virtualises; assistant-ui scrolls cheaper |
| 9 | Bundle size (4) | 3.5 → 2.8 | 1.5 → 1.2 | +254 KB vs +587 KB gzip first load; 1 vs 402 chunks |
| 10 | Maintenance (4) | 3.0 → 2.4 | 3.0 → 2.4 | Both fast-moving with unstable APIs |
| | **Total (100)** | **86.0** | **62.0** | |

**Sensitivity.** With all ten criteria weighted equally, the result is 82 to 62. Giving
performance a weight of 30 (scaling the other weights down proportionally) still leaves
assistant-ui ahead, 79 to 69. The ranking
does not depend on the weights.

## 6. Recommendation

**Adopt assistant-ui** (`@assistant-ui/react` plus `@assistant-ui/react-ag-ui`) for the chat and
run surfaces. Pin exact versions and put it behind the `SurfaceAdapter` port (D-28).

**Drop CopilotKit's React packages from "pinned fallback" to "reference only".** Keep `@ag-ui/*`
as the protocol, as decided. The deciding trade-off: CopilotKit gives us a finished chat and about
3.7× cheaper streaming. In exchange we would ship:

- vendor telemetry and licence endpoints in first-load code;
- an install-time telemetry hook;
- a second Tailwind design system whose tokens collide with ours;
- twice the first-load JS (2.3× the library cost over the baseline);

and we would depend on a runtime-less mode the vendor now labels as a paid tier. assistant-ui's
streaming cost is real, but it is bounded (no long tasks) and fixable on our side (§7).

This confirms the preference recorded in [third-party assessment §3.4](../third-party-assessment.md#34-desktop-ui-surfaces-and-generative-ui).
That entry should be updated to cite this measurement and the downgrade of the CopilotKit fallback.

## 7. Residual risks (assistant-ui) and mitigations

| Risk | Evidence | Mitigation |
|---|---|---|
| The adapter pins `@ag-ui/client` **^0.0.59**, not our 1.0.x | duplicate client in the bundle; `as never` cast; the adapter is 0.0.x | Keep our transport as our own `AbstractAgent` (`LocusAgent`). Contract-test the gateway's AG-UI stream against the adapter in CI. Ask upstream to move to 1.0 (the adapter already speaks the 1.0 interrupt outcome) |
| 0.x churn: 3 breaking lines a year; `unstable_` APIs | §4.8 | Pin exact versions. Upgrade on a schedule through the port. Re-run this bench (`npm run build && node bench/run.mjs`) as the upgrade gate |
| Streaming costs about 1.1 ms of main thread per delta (one render per event; markdown re-parse) | §4.6 | Coalesce text deltas in the gateway to about 20–30 Hz. Try `smooth` and memoised markdown components (not measured). Budget: no long tasks |
| No virtualisation; the DOM grows with the thread | 878 elements for 200 messages | Page history (load the last N, then fetch more on scroll). Add virtualisation only if a run view exceeds about 1,000 messages |
| Legacy `CUSTOM on_interrupt` is not resumable natively | §4.3 | Have the gateway emit AG-UI 1.0 `RUN_FINISHED outcome: interrupt` (we own the gateway). Cover it with a contract test |
| We own the accessibility of the composed UI | §4.5 | Keep the axe pass and the keyboard flow from `run.mjs` as a vitest/Playwright check in `apps/frontend` |
| The zod `Function("")` probe reports a CSP violation | §4.1 | `z.config({ jitless: true })` at app start; keep `unsafe-eval` out of the CSP |
| Small maintainer set (2 on npm) | §4.8 | MIT and thin primitives, so a fork is cheap if needed. That stays a last resort (P30) |
| The UI is not the security boundary for approvals | design | The gateway must refuse to resume a widening interrupt without a shell-signed proof. The UI only routes the request (see the plan below) |

## 8. Integration plan

1. **ADR and assessment update (S).** Record this decision, update §3.4, and pin these versions in `apps/frontend/package.json`:
   - `@assistant-ui/react` 0.15.23
   - `@assistant-ui/react-ag-ui` 0.0.63
   - `@assistant-ui/react-markdown` 0.14.18
   - `@ag-ui/client` 1.0.x

   Add a licence-allowlist check over the production tree to CI; `bench/licenses.mjs` already does this offline.
2. **Transport (M).**
   - Add `apps/frontend/src/lib/agui/locus-agent.ts`: a `LocusAgent extends AbstractAgent`. It posts `RunAgentInput` to the gateway's AG-UI endpoint through `lib/api.ts`, so the single client, its error mapping and the correlation IDs all apply.
   - Parse the SSE stream with `@ag-ui/client`.
   - A dropped stream must surface as "disconnected" with a retry, never as a finished run (FRONTEND.md).
   - Put it behind a `SurfaceAdapter` port so it can be swapped later.
3. **Surface (M).**
   - Build `components/chat/*` from assistant-ui primitives and `components/ui/*`: thread, composer and markdown.
   - Port the spike's Locus cards: tool call, plan, error.
   - Mount it in the Activity run view (`/activity?session=`) as a client component, loaded with a dynamic import so other routes do not pay for it.
   - Show goal, evidence and commitment state next to the thread, per "Cognition is inspectable".
4. **HITL through the desktop shell (M).** The gateway emits `RUN_FINISHED outcome: interrupt` with `metadata.widening`. The tool UI renders the approval card on the tool call. **Review** calls a new shell action (mirrored in `lib/desktop-confirmation.ts` and `shell_actions.rs`). The shell shows the native dialog, signs the resume and sends it itself, as `confirm_action` already does for widening requests. Alternatively, `LocusAgent.run()` routes resume runs that carry widening entries through the shell, which keeps the boundary in one place. The gateway rejects an unsigned resume. Outside the shell, the web profile keeps the plain approval endpoint rules.
5. **Generative UI tiers (S–M, LOCUS-409).**
   - **Tier 1:** a registry of typed cards keyed by tool name, each validating its result before rendering.
   - **Tier 2:** `MessagePrimitive.GenerativeUI` with an allowlist built from `components/ui/*`, a zod schema for each component's props, and a "blocked" fallback.
   - **Tier 3:** stays off; when built, a sandboxed iframe without `allow-same-origin` and with `connect-src 'none'`.
6. **Performance guard (S).** Coalesce text deltas in the gateway. Add a bench check: 2,000 deltas with no long task, and first-load chat JS under about 330 KB gzip.
7. **CSP (separate issue).** `apps/desktop-tauri/src-tauri/tauri.conf.json` has `"csp": null` today. Both libraries ran under a strict CSP in this spike. The blocker for a strict webview CSP is Next's inline bootstrap, which needs nonces or hashes; the chat library is not the blocker.

## 9. What was not measured, and why

- **Next.js 16 / webpack build of the chat route.** We used Vite to isolate library cost (§3). Next-specific first-load numbers come with step 3.
- **The Tauri webview itself (WebView2).** Measured in headless Chromium 153. WebView2 is Chromium-based, so the CSP and network behaviour should carry over, but this is not verified.
- **Install-time network traffic.** Not observed: there was no proxy and no packet capture on this machine. Statically, the only install script in either tree is `@scarf/scarf`. Its opt-out code path was read and the opt-out variables were set; the absence of a request was not observed.
- **Screen readers (NVDA or Narrator).** Only axe-core and a keyboard walk.
- **Realistic paced streaming, and memory over hours-long sessions.** We measured per-delta cost at maximum rate. Effects of assistant-ui's `smooth` and memoised markdown were not measured.
- **A2UI, MCP Apps, reasoning and subagent events, and tier 3.** The code paths exist in both, but the fixture does not exercise them.
- **The real `ag-ui-langgraph` output.** The fixture emits the AG-UI 1.0 interrupt outcome. Whether the pinned backend version emits that or the legacy `on_interrupt` custom event was not checked: no backend ran, by design.
- **Bus factor beyond npm maintainers.** GitHub contributor data was out of scope (no web scraping).
- **`@copilotkit/react-ui` (the v1 components).** It was installed and is counted in the licence numbers. It is not imported, so it adds nothing to the bundle. The surface was built on the v2 `CopilotChat` in `@copilotkit/react-core/v2`, which is the current API.
