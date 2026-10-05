# Chat UI bake-off: assistant-ui vs CopilotKit (spike)

Standalone spike, **not wired into `apps/frontend`**. It implements the same Locus chat and run
surface twice over a deterministic AG-UI 1.0 fixture stream (no backend, no network, no
`@copilotkit/runtime`) and measures both. The write-up, rubric and recommendation are in
[`docs/development/chat-ui-bakeoff-2026-10.md`](../../docs/development/chat-ui-bakeoff-2026-10.md).

| Directory | What it is |
| --- | --- |
| `shared/` | Library-agnostic code used by both variants: the AG-UI fixture streams (`fixtures.ts`), the `confirmAction()` stub for the native desktop confirmation, Locus cards (tool call, tier-1 typed card, tier-2 allowlist renderer, approval, plan, error), the page shell, Locus tokens (`theme.css`) and the in-page probe (`probe.ts`). |
| `variant-assistant-ui/` | `@assistant-ui/react` 0.15.23 primitives + `@assistant-ui/react-ag-ui` 0.0.63. |
| `variant-copilotkit/` | `@copilotkit/react-core` 1.77.0 v2 API (`CopilotKitProvider`, `CopilotChat`, `useRenderTool`, `useInterrupt`, `useAgent`) with `selfManagedAgents`; `@copilotkit/react-ui` 1.77.0 is installed as asked but the v2 chat lives in `react-core/v2`. |
| `variant-baseline/` | Control: React + the shared cards and shell, no chat library. Library cost = variant − baseline. |
| `bench/` | Measurement scripts (below). |
| `results/` | Committed outputs: JSON per measurement and screenshots. |

Stack: Vite 8.3.2 + React 19.2.3 + Tailwind 4.3.3 + TypeScript 5.9.3. Vite instead of Next 16 so
the bundle numbers isolate the library and the strict-CSP test is not confounded by Next's inline
bootstrap scripts. React and Tailwind majors match `apps/frontend`.

## Reproduce

Run from this directory. Keep telemetry off for every install, build and run:

```bash
export COPILOTKIT_TELEMETRY_DISABLED=true SCARF_ANALYTICS=false DO_NOT_TRACK=1
npm ci                       # root package.json also sets scarfSettings.enabled=false
npm run typecheck && npm run lint
npm run build                # three Vite builds -> */dist (gitignored)
npm run licenses             # -> results/licenses-<variant>.json (offline, walks node_modules)
npm run scan                 # -> results/scan-<variant>.json (sizes, hosts, CSP-relevant patterns)
node bench/count-glue.mjs    # -> results/glue-lines.json
node bench/npm-meta.mjs --asof 2026-10-05   # -> results/npm-meta.json (registry metadata only)
node bench/run.mjs --reps 3  # -> results/bench-<variant>.json + screenshots
BENCH_QUERY=md=0 BENCH_TAG=plaintext node bench/run.mjs variant-assistant-ui --reps 3   # markdown ablation
# Bundle attribution by package (source maps go to a scratch dir, not the repo):
(cd variant-copilotkit && npx vite build --sourcemap --outDir /tmp/bo-sm/variant-copilotkit --emptyOutDir)
node bench/attribute.mjs variant-copilotkit /tmp/bo-sm/variant-copilotkit
```

`bench/run.mjs` uses `playwright-core` with the Chromium already in the Playwright cache
(`%LOCALAPPDATA%\ms-playwright`); it never downloads a browser. It serves each `dist/` from a
throwaway loopback server, aborts and logs every non-loopback request, and drives each page
keyboard-first: Tab to the composer, type, Enter, wait for the 2,000-delta stream, Tab to the
approval, Enter (the probe auto-answers `confirmAction()`), then waits for the resumed run's
`RUN_ERROR`. It also runs axe-core (evaluated through CDP, so the page CSP still applies to the
page itself) in light and dark themes.

To look at a variant by hand: `npx vite preview` inside its directory, then open
`/?scenario=run` (or `/?scenario=long`), and send any message. `pace` (ms per event, default 4)
slows the stream down; `pace=0` is one event per macrotask.

## Fixture stream

`shared/src/fixtures.ts`, deterministic (seeded LCG):

- **run**: `RUN_STARTED`, `STATE_SNAPSHOT` (a 4-step plan), `STEP_STARTED`, 2,000
  `TEXT_MESSAGE_CONTENT` deltas of markdown (heading, lists, one fenced code block), three tool
  calls with streamed args and `TOOL_CALL_RESULT` (`search_files` generic, `summarize_run` tier-1
  typed card, `render_card` tier-2 spec with one non-allowlisted `Script` node), `STATE_DELTA` after
  each, a fourth tool call `send_email` with no result, and `RUN_FINISHED` with an AG-UI 1.0
  `outcome: { type: "interrupt" }` referencing that tool call. Resuming with `approved: true`
  streams a short reply then `RUN_ERROR` (`GATEWAY_UPSTREAM_TIMEOUT`); a denial finishes cleanly.
- **long**: `MESSAGES_SNAPSHOT` with 200 messages containing 50 tool calls.

## Constraints honoured

No `@copilotkit/runtime`, no backend, no network at runtime (verified: zero non-loopback requests
in every run). Nothing here is imported by `apps/frontend`. `node_modules/` and `dist/` are not
committed.
