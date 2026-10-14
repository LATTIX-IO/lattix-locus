# Locus design baseline

Status: **approved as the implementation design baseline** (MVP publication review, 2026-10-13). Not evidence of production readiness. Everything under `locus-prototype/` runs on simulated, in-browser state and is not connected to the Locus backend.

This folder is the hand-off point from design to implementation. Treat it as read-only reference: change the design here only through a reviewed PR, and implement against [`locus-prototype/locus-contract.md`](locus-prototype/locus-contract.md).

| Path | What it is | Version |
|---|---|---|
| [`lattix-design-system/`](lattix-design-system/) | Lattix Operational Interface System (blended model): tokens, themes, CSS variables, fonts, logos, component kit | 2026-10-11 |
| [`locus-prototype/`](locus-prototype/) | Synchronized prototype release: 19 screens, shared state and simulator, acceptance tests, QA harness, screenshots | v0.3.1 |
| [`locus-prototype/locus-contract.md`](locus-prototype/locus-contract.md) | Implementation contract: entities and state machines, run-state contract, identity, consequential operations, event stream, rendering rules, release gates | contract v1.1 |
| [`locus-prototype/MANIFEST.md`](locus-prototype/MANIFEST.md) | SHA-256 of every prototype file plus the verification results at export | v0.3.1 |

## Design system

- `lattix-design-system/DESIGN.md`: principles, colour roles, status grammar, operating modes, typography and component rules. This is the authoritative spec.
- `lattix-design-system/system/lattix-theme.css`, `variables.css` and `variables.dark.css`: CSS custom properties for light and dark themes.
- `lattix-design-system/system/tokens.default.json`, `tokens.dark.json` and `tokens.compact.json`: token values for code generation.
- `lattix-design-system/system/fonts/`: IBM Plex Sans and Mono woff2 (SIL OFL 1.1).
- `lattix-design-system/system/logos/`: Lattix wordmarks and marks. The Locus product logos already live in `apps/frontend/public/brand/`.
- `lattix-design-system/system/kit.html` and `kit.dark.html`: rendered component reference.

Marketing templates (deck, email, landing page, newsletter, poster, form) from the source system are intentionally excluded. DESIGN.md rules them out of the operational component library.

Known gap: DESIGN.md refers to `canonical/lattix-tokens.json` and `canonical/validate.mjs` as the canonical token source and contrast validator. Neither was part of the design-system export. Until they are added, the `system/` token and CSS files are the published values.

## Prototype

Open `locus-prototype/index.html` in a browser. Pages work from `file://`; reset the simulated state from the command palette (Ctrl K).

| Check | How to run | Result at export |
|---|---|---|
| Acceptance suite (T1–T20) | open `prototype-tests.html` over http, or `node _qa/cdp.mjs eval prototype-tests.html 1280 "…"` | 20 of 20 |
| Store checks | `node _qa/store-smoke.js` | 17 of 17 |
| Reflow at 320–1280 px | `node _qa/cdp.mjs reflow` | 95 of 95 |
| Accessibility tree, MVP paths | `node _qa/cdp.mjs a11y …` | 10 of 10 |

The harness needs Node 22+ and Chrome. `_qa/cdp.mjs` expects the default Windows Chrome path, so change `CHROME` on other platforms. A screen-reader pass and every Gate B item in the contract (section 7) are not yet verified.

## Recommended implementation order

1. Home → Plan → Run → Outcome vertical slice, behind feature flags, against contract v1.1.
2. Identity, session and step-up (contract section 3), plus the decision review and approval binding.
3. Agent builder and team composer against the versioned agent and team definitions.

Keep the seven-section navigation, the blended component system, the shell and the typography as specified. Do not expand toward the 230-screen inventory until the MVP slice ships.
